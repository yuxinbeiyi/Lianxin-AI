"""Durable main-account-only conversations in locally known public threads."""

import json
import time
import uuid
from contextlib import closing

from utils import bilibili_api_transport as transport
from utils.bilibili_reply_log import reply_log
from utils.bilibili_thread_index import BilibiliThreadIndex
from utils.bilibili_notifications import DiscoveryBudget, DiscoveryStopped, NotificationInbox


def identity(value):
    text = str(value or "")
    return text if len(text) <= 24 and text.isascii() and text.isdigit() and int(text) > 0 else ""


class BilibiliReplyService:
    def __init__(self, service, generate, cancelled=lambda: False, clock=time.time):
        self.service = service
        self.generate = generate
        self.cancelled = cancelled
        self.clock = clock
        self.owner = uuid.uuid4().hex
        with closing(self.connect()) as database, database:
            database.executescript("""
                CREATE TABLE IF NOT EXISTS bili_reply_jobs (
                    account TEXT NOT NULL, source TEXT NOT NULL, main_uid TEXT NOT NULL,
                    root TEXT NOT NULL, bvid TEXT NOT NULL, aid TEXT NOT NULL,
                    payload TEXT NOT NULL, created REAL NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending', owner TEXT NOT NULL DEFAULT '',
                    lease REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
                    text TEXT NOT NULL DEFAULT '', rpid TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT 'reply',
                    PRIMARY KEY(account,source));
                CREATE TABLE IF NOT EXISTS bili_reply_meta (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            columns = {row[1] for row in database.execute("PRAGMA table_info(bili_reply_jobs)")}
            if "kind" not in columns:
                database.execute("ALTER TABLE bili_reply_jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'reply'")
            for name, definition in (("sent_at", "REAL NOT NULL DEFAULT 0"),
                    ("index_dirty", "INTEGER NOT NULL DEFAULT 1")):
                if name not in columns:
                    database.execute(f"ALTER TABLE bili_reply_jobs ADD COLUMN {name} {definition}")
            database.execute("CREATE INDEX IF NOT EXISTS bili_jobs_dirty ON bili_reply_jobs(index_dirty) WHERE index_dirty=1")
            database.execute("CREATE INDEX IF NOT EXISTS bili_comment_roots ON comments(account_uid,mention_uid,rpid,status)")
            database.execute("CREATE INDEX IF NOT EXISTS bili_external_roots ON bili_reply_jobs(account,main_uid,root) "
                "WHERE state='sent'")
            database.execute("CREATE INDEX IF NOT EXISTS bili_jobs_ready_order ON bili_reply_jobs "
                "(account,main_uid,created,length(source),source) WHERE state IN ('pending','generating')")
            database.execute("CREATE INDEX IF NOT EXISTS bili_jobs_thread_order ON bili_reply_jobs "
                "(account,main_uid,aid,root,created,length(source),source) WHERE state IN ('pending','generating')")
            database.execute("CREATE INDEX IF NOT EXISTS bili_jobs_exhausted ON bili_reply_jobs "
                "(account,main_uid,lease) WHERE attempts>=3 AND state IN ('pending','generating')")
        self.thread_index = BilibiliThreadIndex(self.connect)
        self.inbox = NotificationInbox(self.connect)

    def maintain_index(self):
        try:
            summary = self.thread_index.maintain()
            if summary["processed"]:
                reply_log("线程索引维护", **summary, scheduler="recent_due")
        except Exception as error:
            reply_log("线程索引维护暂缓", error=type(error).__name__, reason="源审计与修复标记保留，不影响发送状态")

    def connect(self):
        return self.service._journal.connect()

    def snapshot(self, kind="reply"):
        settings = self.service._settings()
        credential = self.service._read_credential()
        account = identity((credential or {}).get("dedeuserid", (credential or {}).get("DedeUserID")))
        try:
            enabled_at = float(settings.get("mention_enabled_at" if kind == "mention" else "reply_enabled_at", 0))
        except (ValueError, TypeError):
            return None
        enabled = settings.get("mention_reply_enabled" if kind == "mention" else "auto_reply_enabled") is True
        if (self.cancelled() or not enabled or settings["paused"]
                or not settings["user_uid_confirmed"] or not account
                or not settings["user_uid"] or account == settings["user_uid"]
                or not 0 < enabled_at <= self.clock()):
            return None
        return account, settings["user_uid"], enabled_at, dict(credential)

    def meta(self, database, key, default="0"):
        row = database.execute("SELECT value FROM bili_reply_meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, database, key, value):
        database.execute("INSERT OR REPLACE INTO bili_reply_meta VALUES (?,?)", (key, str(value)))

    def thread(self, credential, job, page=1, budget=None):
        arguments = (credential, job["aid"], job["root"], page)
        result = budget.read(transport.get_comment_thread, *arguments,
            cache_key=("thread", job["account"], job["aid"], job["root"], page)) if budget else transport.get_comment_thread(*arguments)
        root = result.get("root") or {}
        if (identity(root.get("rpid")) != job["root"]
                or identity(root.get("root"))
                or identity((root.get("member") or {}).get("mid")) != job.get("root_author", job["account"])
                or identity(root.get("oid")) != job["aid"]):
            reply_log("评论串身份不匹配", expected_root=job["root"], actual_root=identity(root.get("rpid")),
                      expected_author=job.get("root_author", job["account"]), actual_author=identity((root.get("member") or {}).get("mid")),
                      expected_aid=job["aid"], actual_aid=identity(root.get("oid")))
            raise ValueError("Thread identity mismatch")
        replies = result.get("replies") or []
        if not isinstance(replies, list):
            raise ValueError("Invalid thread")
        return root, replies, result.get("page") or {}

    def own_parents(self, job):
        with closing(self.connect()) as database:
            parents = {row[0] for row in database.execute(
                "SELECT rpid FROM bili_reply_jobs WHERE account=? AND root=? AND state='sent'",
                (job["account"], job["root"])) if identity(row[0])}
        return parents | ({job["root"]} if job.get("root_author", job["account"]) == job["account"] else set())

    @staticmethod
    def mentioned(reply, account):
        content = reply.get("content") or {}
        mapping = content.get("at_name_to_mid") or {}
        members = content.get("members") or []
        return ((isinstance(mapping, dict) and any(identity(mid) == account for mid in mapping.values()))
                or (isinstance(members, list) and any(isinstance(member, dict) and identity(member.get("mid")) == account for member in members)))

    def rejection(self, reply, job, enabled_at):
        if reply.get("invisible"):
            return "评论不可见"
        if not identity(reply.get("rpid")):
            return "评论ID缺失"
        if identity((reply.get("member") or {}).get("mid")) != job["main_uid"]:
            return "不是已核验主号"
        if identity(reply.get("oid")) != job["aid"]:
            return "视频对象不匹配"
        try:
            if not float(reply.get("ctime", 0)) > enabled_at:
                return "开启前的历史留言"
        except (TypeError, ValueError):
            return "时间字段无效"
        if not str((reply.get("content") or {}).get("message", "")).strip():
            return "正文为空"
        if job.get("kind") == "mention":
            if identity(reply.get("rpid")) != job["root"] and identity(reply.get("root")) != job["root"]:
                return "根评论不匹配"
            if not self.mentioned(reply, job["account"]):
                return "没有互动账号UID的真实@元数据"
        elif identity(reply.get("root")) != job["root"]:
            return "根评论不匹配"
        elif identity(reply.get("parent")) not in self.own_parents(job):
            return "不是直接回复莲心"
        return ""

    def valid_source(self, reply, job, enabled_at):
        return not self.rejection(reply, job, enabled_at)

    def scan(self, snapshot, budget=None):
        if budget is None:
            self.maintain_index()
        budget = budget or DiscoveryBudget(lambda: self.snapshot() == snapshot)
        account, main_uid, enabled_at, credential = snapshot
        try:
            roots = self.thread_index.recent_due(account, main_uid, self.clock())
        except Exception as error:
            reply_log("近期补查暂缓", error=type(error).__name__, reason="索引不可用，不回退全历史扫描")
            return
        scanned = 0
        for record in roots:
            if self.snapshot() != snapshot:
                break
            try:
                if (record["bvid"], record["root"]) not in budget.threads:
                    self.scan_root({**record, "rpid": record["root"]}, snapshot, budget)
                    scanned += 1
            except DiscoveryStopped:
                break
            except Exception as error:
                reply_log("近期串读取暂缓", root=record["root"], error=type(error).__name__)
                try:
                    self.thread_index.scan_failed(record, self.clock())
                except Exception as persistence_error:
                    reply_log("补查退避保存暂缓", error=type(persistence_error).__name__)
                self.inbox.throttle(account, self.clock(), error, budget)
                if budget.blocked:
                    break
        reply_log("近期补查汇总", selected=len(roots), scanned=scanned, interval_seconds=600, limit=2)

    def scan_root(self, record, snapshot, budget):
        account, main_uid, enabled_at, credential = snapshot
        if self.snapshot() != snapshot:
            raise DiscoveryStopped("Reply authorization changed")
        budget.threads.add((record["bvid"], str(record["rpid"])))
        lookup = getattr(self.service, "_reply_video_lookup", self.service._video_lookup)
        video = budget.read(lookup, record["bvid"], cache_key=("video", account, record["bvid"]))
        if video.get("bvid") != record["bvid"] or not identity(video.get("aid")):
            raise ValueError("Video identity mismatch")
        if record.get("aid") and str(record["aid"]) != identity(video["aid"]):
            raise ValueError("Indexed video identity mismatch")
        job = {"account": account, "main_uid": main_uid, "root": identity(record["rpid"]),
               "bvid": record["bvid"], "aid": identity(video["aid"]), "root_author": record.get("root_author", account)}
        if not job["root"]:
            return
        page_key = f"page:{account}:{job['root']}"
        with closing(self.connect()) as database:
            page = int(self.meta(database, page_key, "1"))
        if self.snapshot() != snapshot:
            raise DiscoveryStopped("Reply authorization changed")
        root, replies, paging = self.thread(credential, job, page, budget)
        if self.snapshot() != snapshot:
            raise DiscoveryStopped("Reply authorization changed")
        try:
            self.thread_index.resolve(account, main_uid, job["aid"], job["root"], job["bvid"], job["root_author"])
        except Exception as error:
            reply_log("线程身份补全暂缓", root=job["root"], error=type(error).__name__)
        candidates = [reply for reply in replies if self.valid_source(reply, job, enabled_at)]
        reply_log("评论串扫描", bvid=job["bvid"], root=job["root"], page=page, scanned=len(replies), eligible=len(candidates))
        for reply in replies:
            reply_log("发现留言", source=identity(reply.get("rpid")), author=identity((reply.get("member") or {}).get("mid")),
                      parent=reply.get("parent"), root=reply.get("root"), chars=len(str((reply.get("content") or {}).get("message", ""))),
                      eligible=reply in candidates, reason=self.rejection(reply, job, enabled_at) or "主号与父子关系校验通过")
        with closing(self.connect()) as database, database:
            total = int(paging.get("count", 0))
            next_page = page + 1 if page * 20 < total else 1
            self.set_meta(database, page_key, next_page)
            for reply in candidates:
                payload = {"reply": reply, "page": page, "title": str(video.get("title", ""))[:500], "root_author": job["root_author"]}
                inserted = database.execute("INSERT OR IGNORE INTO bili_reply_jobs "
                    "(account,source,main_uid,root,bvid,aid,payload,created) VALUES (?,?,?,?,?,?,?,?)",
                    (account, identity(reply["rpid"]), main_uid, job["root"], job["bvid"], job["aid"],
                     json.dumps(payload, ensure_ascii=False), float(reply["ctime"]))).rowcount
                reply_log("留言入队" if inserted else "留言已处理或已在队列", source=identity(reply["rpid"]), bvid=job["bvid"])
        try:
            self.thread_index.scanned({**record, **job}, job["aid"], self.clock(), next_page,
                max((float(reply["ctime"]) for reply in candidates), default=0))
        except Exception as error:
            reply_log("补查状态保存暂缓", root=job["root"], error=type(error).__name__, reason="已核验任务保留")
        return {identity(reply["rpid"]) for reply in candidates}

    def scan_reply_notice(self, snapshot, notice, budget):
        account, main_uid = snapshot[:2]
        item = notice.get("item") or {}
        if not identity((notice.get("user") or {}).get("mid")) or not item.get("type") or item.get("business_id") is None:
            return "deferred", ""
        if identity((notice.get("user") or {}).get("mid")) != main_uid:
            return "ignored", ""
        if item.get("type") != "reply" or str(item.get("business_id")) != "1":
            return "ignored", ""
        sources = {identity(item.get(key)) for key in ("source_id", "target_id", "id")} - {""}
        roots = list(dict.fromkeys(identity(item.get(key)) for key in ("root_id", "target_id", "source_id") if identity(item.get(key))))
        if not roots or not sources:
            return "deferred", ""
        record = None
        with closing(self.connect()) as database:
            for root in roots[:3]:
                row = database.execute("SELECT bvid,rpid,account_uid AS root_author,created_at,sent_at,aid FROM comments "
                    "WHERE account_uid=? AND mention_uid=? AND rpid=? AND status IN ('sent_verified','sent_unverified') LIMIT 1",
                    (account, main_uid, root)).fetchone()
                if row:
                    record = dict(row)
                    break
                row = database.execute("SELECT bvid,root AS rpid,payload,created AS created_at,sent_at,aid FROM bili_reply_jobs "
                    "WHERE account=? AND main_uid=? AND root=? AND state='sent' AND kind='mention' LIMIT 1",
                    (account, main_uid, root)).fetchone()
                if row:
                    record = {**dict(row), "root_author": json.loads(row["payload"])["root_author"]}
                    break
        if not record:
            return "ignored", ""
        found = self.scan_root(record, snapshot, budget) or set()
        matching = sources & found
        return ("verified", next(iter(matching))) if matching else ("deferred", "")

    def discover(self, snapshots):
        def active():
            if any(self.inbox.blocked_until(snapshot[0]) > self.clock() for snapshot in snapshots.values()):
                budget.blocked = True
                return False
            return any(self.snapshot(kind) == snapshot for kind, snapshot in snapshots.items())
        budget = DiscoveryBudget(active)
        if any(self.inbox.blocked_until(snapshot[0]) > self.clock() for snapshot in snapshots.values()):
            budget.blocked = True
            reply_log("账号发现退避", reason="此前通知接口限流，暂不读取或发送")
            return budget
        reply_log("通知流检查", account=next(iter(snapshots.values()))[0], streams=",".join(snapshots), latest_first=True)
        for history in (False, True):
            for stream, snapshot in snapshots.items():
                try:
                    self.inbox.fetch(self, snapshot, stream, budget, history)
                except Exception as error:
                    reply_log("通知落盘暂缓", stream=stream, error=type(error).__name__, reason="未完成的检查点保留")
        from utils.bilibili_mentions import scan_notice
        for notice in self.inbox.candidates(snapshots, self.clock()):
            stream = notice["stream"]
            snapshot = snapshots[stream]
            if self.snapshot(stream) != snapshot or budget.blocked:
                continue
            try:
                budget.candidates += 1
                payload = json.loads(notice["payload"])
                state, source = (scan_notice(self, snapshot, payload, budget) if stream == "mention"
                                 else self.scan_reply_notice(snapshot, payload, budget))
                self.inbox.finish(notice, state, self.clock(), source)
            except DiscoveryStopped:
                reply_log("通知核验暂缓", stream=stream, reason="预算、暂停或身份变化；通知留待下轮")
                break
            except Exception as error:
                reply_log("通知核验异常", stream=stream, notice=notice["notice_id"], error=type(error).__name__)
                try:
                    self.inbox.throttle(snapshot[0], self.clock(), error, budget)
                    self.inbox.finish(notice, "deferred", self.clock())
                except Exception as persistence_error:
                    reply_log("通知重试标记暂缓", error=type(persistence_error).__name__)
        if "reply" in snapshots and not budget.blocked and budget.reads < 12:
            self.scan(snapshots["reply"], budget)
        reply_log("通知发现汇总", reads=budget.reads, pages=budget.pages, stored=budget.stored,
                  candidates=budget.candidates, account_backoff=budget.blocked, fallback="recent_due")
        return budget

    def claim(self, snapshots, kind="reply"):
        snapshots = snapshots if isinstance(snapshots, dict) else {kind: snapshots}
        if not snapshots or any(name not in ("reply", "mention") or self.snapshot(name) != value
                                for name, value in snapshots.items()):
            return None
        account, main_uid = next(iter(snapshots.values()))[:2]
        if any(value[:2] != (account, main_uid) for value in snapshots.values()):
            return None
        parameters = {"account": account, "main": main_uid, "now": self.clock()}
        for name, value in snapshots.items():
            parameters[f"kind_{name}"] = name
            parameters[f"cutoff_{name}"] = value[2]
        def scope(alias):
            return "(" + " OR ".join(f"({alias}.kind=:kind_{name} AND {alias}.created>:cutoff_{name})"
                                     for name in snapshots) + ")"
        with closing(self.connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            remaining = float(self.meta(database, "last_send")) + 120 - self.clock()
            if remaining > 0:
                reply_log("发送冷却", streams=",".join(snapshots), remaining_seconds=int(remaining)+1)
                return None
            database.execute("UPDATE bili_reply_jobs SET state='exhausted',owner='',lease=0 WHERE rowid IN ("
                "SELECT rowid FROM bili_reply_jobs AS expired WHERE account=:account AND main_uid=:main "
                "AND attempts>=3 AND state IN ('pending','generating') "
                f"AND (state='pending' OR lease<=:now) AND {scope('expired')} LIMIT 100)", parameters)
            row = database.execute("SELECT job.* FROM bili_reply_jobs AS job "
                "WHERE job.account=:account AND job.main_uid=:main AND job.state IN ('pending','generating') "
                f"AND {scope('job')} AND job.attempts<3 AND (job.state='pending' OR job.lease<=:now) "
                "AND NOT EXISTS (SELECT 1 FROM bili_reply_jobs AS prior WHERE prior.account=job.account "
                "AND prior.main_uid=job.main_uid AND prior.aid=job.aid AND prior.root=job.root "
                f"AND prior.state IN ('pending','generating') AND {scope('prior')} "
                "AND (prior.attempts<3 OR (prior.state='generating' AND prior.lease>:now)) "
                "AND (prior.created,length(prior.source),prior.source)<(job.created,length(job.source),job.source)) "
                "ORDER BY job.created,length(job.source),job.source LIMIT 1", parameters).fetchone()
            if row is None:
                return None
            database.execute("UPDATE bili_reply_jobs SET state='generating',owner=?,lease=?,attempts=attempts+1 "
                "WHERE account=? AND source=?", (self.owner, self.clock()+600, account, row["source"]))
            reply_log("队列选择", kind=row["kind"], source=row["source"], root=row["root"], rule="留言时间先来先回，同串不越过生成中任务")
            return dict(row)

    def mark(self, job, state):
        with closing(self.connect()) as database, database:
            database.execute("UPDATE bili_reply_jobs SET state=? WHERE account=? AND source=? "
                "AND owner=? AND state='generating' AND lease>?",
                (state, job["account"], job["source"], self.owner, self.clock()))

    def source_at(self, snapshot, job, page):
        root, replies, paging = self.thread(snapshot[3], job, page)
        source = root if job["kind"] == "mention" and job["source"] == job["root"] else next(
            (reply for reply in replies if identity(reply.get("rpid")) == job["source"]), None)
        if not source or not self.valid_source(source, job, snapshot[2]):
            return root, None
        return root, source

    def process(self):
        snapshot = self.snapshot()
        mention_snapshot = self.snapshot("mention")
        if not snapshot and not mention_snapshot:
            reply_log("轮询未执行", reason="功能关闭、暂停、身份未核验、授权缺失或退出请求")
            return "disabled"
        self.maintain_index()
        snapshots = {kind: value for kind, value in (("reply", snapshot), ("mention", mention_snapshot)) if value}
        budget = self.discover(snapshots)
        if budget.blocked or any(self.inbox.blocked_until(value[0]) > self.clock() for value in snapshots.values()):
            return "backoff"
        if any(self.snapshot(kind) != value for kind, value in snapshots.items()):
            return "cancelled"
        job = self.claim(snapshots)
        if not job:
            reply_log("本轮无待处理留言", reason="无新留言、已防重或发送冷却中")
            return "idle"
        phase = "source_recheck"
        try:
            kind = job["kind"]
            snapshot = self.snapshot(kind)
            if not snapshot or snapshot[:2] != (job["account"], job["main_uid"]):
                self.mark(job, "skipped")
                return "cancelled"
            reply_log("任务领取", kind=kind, bvid=job["bvid"], root=job["root"], source=job["source"], attempt=job["attempts"]+1)
            payload = json.loads(job["payload"])
            job["root_author"] = payload.get("root_author", job["account"])
            root, source = self.source_at(snapshot, job, payload["page"])
            if source is None:
                self.mark(job, "skipped")
                reply_log("原留言复核未通过", source=job["source"], reason="原评论不可见、身份关系变化或不是启用后的新留言")
                return "skipped"
            with closing(self.connect()) as database:
                history = [dict(row) for row in database.execute(
                    "SELECT payload,text FROM bili_reply_jobs WHERE account=? AND root=? AND main_uid=? "
                    "AND state='sent' ORDER BY created DESC LIMIT 4", (job["account"], job["root"], job["main_uid"]))]
            context = {"video_title": payload["title"],
                "root_comment": str((root.get("content") or {}).get("message", ""))[:2000],
                "previous_turns": [{"user": str((json.loads(turn["payload"])["reply"].get("content") or {}).get("message", ""))[:2000],
                                    "assistant": turn["text"][:2000]} for turn in reversed(history)],
                "new_reply": str((source.get("content") or {}).get("message", ""))[:3000]}
            if kind == "mention":
                phase = "video_material"
                from utils.bilibili_mentions import read_mention_material
                context["video_material"] = read_mention_material(snapshot[3], job)
            if self.snapshot(kind) != snapshot:
                self.mark(job, "skipped")
                reply_log("生成前取消", source=job["source"], reason="开关、身份、暂停或退出状态变化")
                return "cancelled"
            started = time.monotonic()
            phase = "model_generation"
            reply_log("开始生成回复", kind=kind, source=job["source"], history_turns=len(history), source_chars=len(context["new_reply"]))
            text = self.generate(context)
            reply_log("生成结束", source=job["source"], chars=len(text) if isinstance(text, str) else 0,
                      elapsed_ms=int((time.monotonic()-started)*1000), decision="skip" if not text or str(text).strip().upper() == "SKIP" else "candidate")
            if not isinstance(text, str) or not text.strip() or text.strip().upper() == "SKIP":
                self.mark(job, "skipped")
                return "skipped"
            latest = self.snapshot(kind)
            phase = "before_send_recheck"
            if self.inbox.blocked_until(job["account"]) > self.clock():
                reply_log("发送暂缓", source=job["source"], reason="账号接口退避中；生成任务保留租约")
                return "backoff"
            if not latest or latest != snapshot or not self.service.check_authorization().get("ok"):
                self.mark(job, "skipped")
                reply_log("发送前取消", source=job["source"], reason="设置、身份变化或实时授权未通过")
                return "cancelled"
            video = self.service._video_lookup(job["bvid"])
            if (video.get("bvid") != job["bvid"] or identity(video.get("aid")) != job["aid"]
                    or video.get("commentOpen") is not True):
                self.mark(job, "skipped")
                reply_log("发送前取消", source=job["source"], reason="视频对象不匹配或评论区未确认开放")
                return "skipped"
            verified_root, verified = self.source_at(latest, job, payload["page"])
            if (verified is None or (verified.get("content") or {}).get("message") != (source.get("content") or {}).get("message")
                    or (verified_root.get("content") or {}).get("message") != (root.get("content") or {}).get("message")):
                self.mark(job, "skipped")
                reply_log("发送前取消", source=job["source"], reason="原留言或根评论内容已变化")
                return "skipped"
            with closing(self.connect()) as database, database:
                database.execute("BEGIN IMMEDIATE")
                if float(self.meta(database, "last_send")) + 120 > self.clock():
                    return "cooldown"
                if self.snapshot(kind) != snapshot:
                    return "cancelled"
                changed = database.execute("UPDATE bili_reply_jobs SET state='sending',text=? "
                    "WHERE account=? AND source=? AND owner=? AND state='generating' AND lease>?",
                    (text.strip(), job["account"], job["source"], self.owner, self.clock())).rowcount
                if not changed:
                    return "lease_lost"
                self.set_meta(database, "last_send", self.clock())
            if self.snapshot(kind) != snapshot:
                return "cancelled"
            reply_log("开始提交回复", kind=kind, bvid=job["bvid"], root=job["root"], parent=job["source"], chars=len(text.strip()))
            phase = "network_send"
            result = transport.send_comment_reply(latest[3], job["aid"], job["root"], job["source"], text.strip())
            data = result.get("data", result)
            rpid = identity(data.get("rpid") or (data.get("reply") or {}).get("rpid"))
            if result.get("code", 0) != 0 or not rpid:
                reply_log("提交结果不确定", source=job["source"], api_code=result.get("code"), retry=False)
                return "unknown"
            phase = "persist_result"
            with closing(self.connect()) as database, database:
                database.execute("UPDATE bili_reply_jobs SET state='sent',rpid=?,sent_at=?,index_dirty=1 WHERE account=? AND source=? "
                    "AND owner=? AND state='sending'", (rpid, self.clock(), job["account"], job["source"], self.owner))
            reply_log("回复已提交", bvid=job["bvid"], parent=job["source"], rpid=rpid, visibility="需到B站核对")
            return "sent"
        except Exception as error:
            reply_log("任务异常", source=job["source"], phase=phase, error=type(error).__name__, api_code=getattr(error, "code", None),
                      reason="生成阶段租约到期可有限重试；进入发送阶段后不自动重发")
            return "deferred_or_unknown"
