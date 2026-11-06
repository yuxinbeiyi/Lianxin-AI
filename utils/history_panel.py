"""Native history panel adapter; all records remain in HistoryManager."""

from copy import deepcopy
from datetime import date, datetime
import json
import re
import threading
import uuid


class HistoryConflict(Exception):
    pass


def session_id(value):
    if type(value) is not int or value <= 0 or value > 9223372036854775807:
        raise ValueError("会话 ID 必须是正整数")
    return value


def selected_ids(value):
    if not isinstance(value, list) or not value:
        raise ValueError("请先选择会话")
    return list(dict.fromkeys(session_id(item) for item in value))


def valid_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("日期必须为 YYYY-MM-DD")
    date.fromisoformat(value)
    return value


def confirmed(body):
    if body.get("confirmed") is not True:
        raise ValueError("此操作需要先确认")


def write_diary(target_date, messages):
    from utils.diary import generate_diary_content, save_diary

    data = generate_diary_content(messages)
    if not data or not data.get("content"):
        raise ValueError("书页生成失败，原书页未修改")
    save_diary(target_date, data["content"], data.get("weather", ""),
               data.get("is_red_line", False), data.get("echo_text", ""),
               source_event_ids=data.get("source_event_ids", []))


def diary_exists(target_date):
    from utils.diary import has_diary_for_date
    return has_diary_for_date(target_date)


class HistoryPanelService:
    def __init__(self, manager, summary=None, diary=write_diary, has_diary=diary_exists):
        from utils.history_summary import generate_session_summary

        self.manager = manager
        self.summary = summary or generate_session_summary
        self.diary = diary
        self.has_diary = has_diary
        self.lock = threading.RLock()
        self._export_lock = threading.Lock()
        self._job = None

    def require_session(self, value):
        record = self.manager.get_session(session_id(value))
        if record is None:
            raise LookupError("会话不存在，可能已被删除，请刷新")
        return record

    def listing(self, query="", target_date=""):
        if not isinstance(query, str) or len(query) > 2000:
            raise ValueError("搜索内容过长")
        if target_date:
            valid_date(target_date)
        records = self.manager.search_sessions(query.strip()) if query.strip() else self.manager.get_sessions()
        counts = self.manager.get_session_message_counts()
        return {"items": [{**record, "message_count": counts.get(record["id"], 0)} for record in records
                          if not target_date or record["created_at"].startswith(target_date)]}

    def detail(self, value):
        record = self.require_session(value)
        messages = self.manager.get_messages(value)
        return {"session": {**record, "message_count": len(messages)}, "messages": messages,
                "character_count": sum(len(message["content"]) for message in messages)}

    def update(self, body):
        with self.lock:
            value = session_id(body.get("id"))
            self.require_session(value)
            if "title" in body:
                title = body["title"]
                if not isinstance(title, str) or not title.strip():
                    raise ValueError("会话名称不能为空")
                self.manager.update_session_title(value, title.strip())
            elif body.get("togglePin") is True:
                self.manager.toggle_pin(value)
            else:
                raise ValueError("未知的会话更新操作")
            record = self.require_session(value)
            return {**record, "message_count": self.manager.get_message_count(value)}

    def ensure_idle(self):
        if self._job and self._job["status"] == "running":
            raise HistoryConflict("历史摘要/书页正在生成，请完成后再操作")

    def delete(self, ids):
        with self.lock:
            self.ensure_idle()
            for value in ids:
                self.require_session(value)
            for value in ids:
                self.manager.delete_session(value)

    def export(self, body):
        file_format = body.get("format")
        if file_format not in ("txt", "json"):
            raise ValueError("导出格式必须为 txt 或 json")
        export_all = body.get("all", False)
        if type(export_all) is not bool or (export_all and file_format != "json"):
            raise ValueError("全部会话只支持 JSON 导出")
        records = self.manager.get_sessions() if export_all else [self.require_session(body.get("id"))]
        result = []
        for record in records:
            result.append({"session": {"id": record["id"], "title": record["title"],
                                       "created_at": record["created_at"], "summary": record.get("summary") or "",
                                       "is_pinned": bool(record.get("is_pinned"))},
                           "messages": [{key: message[key] for key in ("role", "content", "timestamp")}
                                        for message in self.manager.get_messages(record["id"])]})
        title = "莲心AI全部对话" if export_all else records[0]["title"][:20]
        title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .") or "历史对话"
        if re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", title):
            title = "_" + title
        if file_format == "json":
            content = json.dumps(result if export_all else result[0], ensure_ascii=False, indent=2)
        else:
            record = result[0]["session"]
            lines = [f"会话标题：{record['title']}", f"创建时间：{record['created_at']}", ""]
            if record["summary"].strip():
                lines += [f"摘要：{record['summary']}", ""]
            lines.append("=" * 40)
            for message in result[0]["messages"]:
                role = "主人" if message["role"] == "user" else "莲心"
                lines.append(f"[{message['timestamp'][11:16]}] {role}：{message['content']}")
            content = "\n".join(lines)
        return {"filename": f"{title}.{file_format}", "content": content,
                "mime": "application/json" if file_format == "json" else "text/plain"}

    def export_local(self, body):
        from utils.history_export import save_history_export

        payload = self.export(body)
        if not self._export_lock.acquire(blocking=False):
            raise HistoryConflict("本地保存窗口已打开，请先完成或取消当前导出")
        try:
            return save_history_export(payload)
        finally:
            self._export_lock.release()

    def job_state(self):
        with self.lock:
            return {"job": deepcopy(self._job)}

    def start_job(self, body):
        confirmed(body)
        kind = body.get("kind")
        with self.lock:
            self.ensure_idle()
            target_date = ""
            if kind == "batch-summary":
                records = self.listing()["items"]
                ids = [record["id"] for record in records
                       if record["message_count"] >= 10 and not (record.get("summary") or "").strip()]
            elif kind == "summary":
                ids = [session_id(body.get("id"))]
            elif kind == "diary":
                ids = selected_ids(body.get("ids"))
                target_date = valid_date(body.get("date"))
                for value in ids:
                    if not self.require_session(value)["created_at"].startswith(target_date):
                        raise ValueError("书页只能使用所选日期创建的会话")
                if self.has_diary(target_date) and body.get("overwrite") is not True:
                    raise HistoryConflict("此日期已有书页，请确认覆盖后重试")
            else:
                raise ValueError("未知的历史生成操作")
            if not ids:
                raise ValueError("没有符合条件的会话（批量摘要需要 ≥10 条消息且无摘要）")
            inputs = [(value, self.detail(value)["messages"]) for value in ids]
            if not any(messages for _, messages in inputs):
                raise ValueError("所选会话没有消息内容")
            self._job = {"id": uuid.uuid4().hex, "kind": kind, "status": "running", "total": len(inputs),
                         "processed": 0, "succeeded": 0, "errors": [], "date": target_date,
                         "started_at": datetime.now().isoformat(timespec="seconds")}
            thread = threading.Thread(target=self._generate, args=(inputs, target_date), daemon=True,
                                      name="history-panel-generation")
            try:
                thread.start()
            except Exception:
                self._job["status"] = "failed"
                raise
            return self.job_state()

    def _generate(self, inputs, target_date):
        try:
            if target_date:
                self.diary(target_date, [{"role": message["role"], "content": message["content"]}
                                         for _, messages in inputs for message in messages])
                with self.lock:
                    self._job["processed"] = self._job["succeeded"] = len(inputs)
            else:
                for value, messages in inputs:
                    try:
                        summary = self.summary(messages)
                        if not isinstance(summary, str) or not summary.strip():
                            raise ValueError("摘要为空，原摘要未修改")
                        with self.lock:
                            self.require_session(value)
                            self.manager.update_summary(value, summary.strip())
                            self._job["succeeded"] += 1
                    except Exception as error:
                        with self.lock:
                            self._job["errors"].append({"id": value, "message": str(error)})
                    finally:
                        with self.lock:
                            self._job["processed"] += 1
        except Exception as error:
            with self.lock:
                self._job["errors"].append({"id": None, "message": str(error)})
                self._job["processed"] = len(inputs)
        finally:
            self.manager.close()
            with self.lock:
                self._job["status"] = ("completed" if not self._job["errors"] else
                                       "partial" if self._job["succeeded"] else "failed")
