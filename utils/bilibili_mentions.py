"""Resolve notification hints against real, UID-verified video comments."""

import json
from contextlib import closing

from utils import bilibili_api_transport as transport
from utils.bilibili_interaction import parse_bilibili_video_id
from utils.bilibili_reply import identity
from utils.bilibili_notifications import DiscoveryStopped
from utils.bilibili_reply_log import reply_log


def scan_notice(worker, snapshot, notice, budget):
    account, main_uid, enabled_at, credential = snapshot
    item = notice.get("item") or {}
    author = identity((notice.get("user") or {}).get("mid"))
    if not author or not item.get("type") or item.get("business_id") is None:
        return "deferred", ""
    if author != main_uid or item.get("type") != "reply" or str(item.get("business_id")) != "1":
        reply_log("忽略@通知", notice=identity(notice.get("id")), author=author,
                  reason="非主号或非视频评论，未交给模型")
        return "ignored", ""
    aid = identity(item.get("subject_id"))
    sources = {identity(item.get(key)) for key in ("source_id", "target_id", "id")} - {""}
    explicit_root = identity(item.get("root_id"))
    roots = [explicit_root] if explicit_root else list(dict.fromkeys(
        identity(item.get(key)) for key in ("source_id", "target_id", "id") if identity(item.get(key))))
    if not aid or not sources or not roots:
        reply_log("忽略@通知", reason="视频或评论定位字段缺失")
        return "deferred", ""
    def read(function, *args, **kwargs):
        if worker.snapshot("mention") != snapshot:
            raise DiscoveryStopped("Mention authorization changed")
        return budget.read(function, *args, **kwargs)
    info = read(transport.get_video_info_by_aid, credential, aid, cache_key=("aid", account, aid))
    bvid = parse_bilibili_video_id(info.get("bvid", ""))
    if identity(info.get("aid")) != aid or not bvid:
        raise ValueError("Video identity mismatch")
    for root_id in roots[:3]:
        page_key = f"mention_page:{account}:{aid}:{root_id}"
        with closing(worker.connect()) as database:
            page = int(worker.meta(database, page_key, "1"))
        for page_index in (1,) if page == 1 else (1, page):
            try:
                result = read(transport.get_comment_thread, credential, aid, root_id, page_index,
                    cache_key=("thread", account, aid, root_id, page_index))
            except DiscoveryStopped:
                raise
            except Exception as error:
                reply_log("@原评论读取暂缓", root=root_id, page=page_index, error=type(error).__name__)
                raise
            root = result.get("root") or {}
            root_author = identity((root.get("member") or {}).get("mid"))
            if (identity(root.get("rpid")) != root_id or identity(root.get("root"))
                    or identity(root.get("oid")) != aid or not root_author):
                continue
            job = {"account": account, "main_uid": main_uid, "aid": aid, "bvid": bvid,
                   "root": root_id, "root_author": root_author, "kind": "mention"}
            for reply in [root, *(result.get("replies") or [])]:
                source_id = identity(reply.get("rpid"))
                if source_id not in sources:
                    continue
                reason = worker.rejection(reply, job, enabled_at)
                reply_log("核实@留言", bvid=bvid, source=source_id, author=identity((reply.get("member") or {}).get("mid")),
                          root=root_id, reason=reason or "主号UID与互动账号@元数据核验通过")
                if reason:
                    if reason == "开启前的历史留言":
                        return "ignored", source_id
                    continue
                if worker.snapshot("mention") != snapshot:
                    raise DiscoveryStopped("Mention authorization changed")
                payload = {"reply": reply, "page": page_index, "title": str(info.get("title", ""))[:500], "root_author": root_author}
                with closing(worker.connect()) as database, database:
                    inserted = database.execute("INSERT OR IGNORE INTO bili_reply_jobs "
                        "(account,source,main_uid,root,bvid,aid,payload,created,kind) VALUES (?,?,?,?,?,?,?,?,?)",
                        (account, source_id, main_uid, root_id, bvid, aid, json.dumps(payload, ensure_ascii=False),
                         float(reply["ctime"]), "mention")).rowcount
                    if not inserted:
                        inserted = database.execute("UPDATE bili_reply_jobs SET kind='mention',payload=?,index_dirty=1 "
                            "WHERE account=? AND source=? AND kind='reply' AND state='pending'",
                            (json.dumps(payload, ensure_ascii=False), account, source_id)).rowcount
                reply_log("@留言入队" if inserted else "@留言已防重", bvid=bvid, source=source_id, root=root_id)
                return "verified", source_id
        count = int((result.get("page") or {}).get("count", 0))
        with closing(worker.connect()) as database, database:
            worker.set_meta(database, page_key, page + 1 if page * 20 < count else 1)
    reply_log("@留言本轮未定位", bvid=bvid, reason="未找到可验证原评论；分页轮扫或平台可见性可能影响发现")
    return "deferred", ""


def read_mention_material(credential, job):
    from utils.bilibili_scroll import BilibiliScrollClient
    reply_log("开始读取@视频资料", bvid=job["bvid"], source=job["source"])
    material = BilibiliScrollClient(credential).enrich(job["bvid"], comment_limit=5)
    if material.get("bvid") != job["bvid"] or identity(material.get("aid")) != job["aid"]:
        raise ValueError("Mention material video mismatch")
    subtitle = "" if material.get("subtitle_error") else str(material.get("subtitle") or "")
    comments = [str(item.get("content") or "")[:300] for item in (material.get("comments") or [])[:5]]
    reply_log("@视频资料读取完成", bvid=job["bvid"], title=str(material.get("title") or ""),
              description_chars=len(str(material.get("description") or "")), subtitle_chars=len(subtitle),
              subtitle_status="available" if subtitle else "unavailable", comments=len(comments))
    return {"title": str(material.get("title") or "")[:500], "author": str(material.get("author") or "")[:200],
            "description": str(material.get("description") or "")[:2000], "subtitle": subtitle[:12000],
            "subtitle_available": bool(subtitle), "comment_samples": comments}
