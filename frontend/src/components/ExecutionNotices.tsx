import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { lianxinApi, type ExecutionNotice } from "../services/lianxinApi";
import { mergeExecutionNotices } from "../utils/executionNotices";
import "./executionNotices.css";

export function ExecutionNotices({ sessionId, online, onChange }: {
  sessionId?: number; online: boolean; onChange?: () => void;
}) {
  const [notices, setNotices] = useState<ExecutionNotice[]>([]);
  const dismissed = useRef(new Set<number>());
  useEffect(() => {
    onChange?.();
  }, [notices, onChange]);
  useEffect(() => {
    setNotices([]);
    const local = (event: Event) => {
      const notice = (event as CustomEvent<ExecutionNotice>).detail;
      setNotices((current) => mergeExecutionNotices(current, [notice], sessionId));
    };
    window.addEventListener("lianxin-execution-notice", local);
    return () => window.removeEventListener("lianxin-execution-notice", local);
  }, [sessionId]);
  useEffect(() => {
    let disposed = false;
    let pending = false;
    let cursor = 0;
    const poll = async () => {
      if (disposed || pending || !online) return;
      pending = true;
      try {
        const result = await lianxinApi.executionNotices(cursor, sessionId);
        if (disposed) return;
        cursor = result.latest;
        const recent = result.items.filter((item) => Date.now() - item.timestamp < 120000
          && !dismissed.current.has(item.id));
        setNotices((current) => mergeExecutionNotices(current, recent, sessionId));
      } catch {
      } finally {
        pending = false;
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [sessionId, online]);
  return <div className="execution-notices" aria-live="polite" aria-atomic="false">
    {notices.map((notice) => <div className={`execution-notice is-${notice.level}`} role="status" key={notice.id}>
      <span>{notice.message}</span>
      <button type="button" aria-label="关闭系统提示" onClick={() => {
        dismissed.current.add(notice.id);
        if (dismissed.current.size > 128) dismissed.current.delete(dismissed.current.values().next().value!);
        setNotices((current) => current.filter((item) => item.id !== notice.id));
      }}><X size={12} /></button>
    </div>)}
  </div>;
}
