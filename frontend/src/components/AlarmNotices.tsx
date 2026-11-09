import { useEffect, useRef, useState } from "react";
import { lianxinApi, type AlarmNotice } from "../services/lianxinApi";
import { confirmConfigAction } from "../utils/nativeConfig";
import "../styles/alarm-panel.css";

export function AlarmNotices({ online }: { online: boolean }) {
  const [notices, setNotices] = useState<AlarmNotice[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const actionPending = useRef(false);
  useEffect(() => {
    let disposed = false;
    let pending = false;
    let cursor = 0;
    const poll = async () => {
      if (!online || disposed || pending) return;
      pending = true;
      try {
        const result = await lianxinApi.alarmPanelEvents(cursor);
        if (disposed) return;
        cursor = result.latest;
        setNotices(current => [...current, ...result.items.filter(item => item.message)].slice(-30));
      } catch {
      } finally { pending = false; }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [online]);
  const dismiss = (id: number) => setNotices(current => current.filter(item => item.id !== id));
  const act = async (notice: AlarmNotice, action: string, id?: string) => {
    if (!id || actionPending.current) return;
    actionPending.current = true;
    setBusy(true);
    setError("");
    try {
      if (action === "auto_task.run" && !await confirmConfigAction("补做此任务？它可能调用工具产生实际操作。", "莲心 · 错过的任务")) return;
      await lianxinApi.alarmPanelAction(action, { id });
      dismiss(notice.id);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "操作失败"); }
    finally { actionPending.current = false; setBusy(false); }
  };
  return <aside className="alarm-notices" aria-label="到期提醒" aria-live="polite">
    {notices.map(notice => <section className="alarm-notice" key={notice.id}>
      <p>{notice.message}</p><div className="alarm-actions">
        {notice.type === "alarm" && <button disabled={busy} onClick={() => void act(notice, "alarm.snooze", notice.alarm_id)}>贪睡 5 分钟</button>}
        {notice.type === "todo_due" && <><button disabled={busy} onClick={() => void act(notice, "todo.complete", notice.todo_id)}>完成待办</button><button disabled={busy} onClick={() => void act(notice, "todo.snooze", notice.todo_id)}>10 分钟后提醒</button></>}
        {notice.type === "auto_task_missed" && <><button disabled={busy} onClick={() => void act(notice, "auto_task.run", notice.task_id)}>现在补做</button><button disabled={busy} onClick={() => void act(notice, "auto_task.skip", notice.task_id)}>跳过本次</button></>}
        <button disabled={busy} onClick={() => dismiss(notice.id)}>知道了</button>
      </div>
    </section>)}
    {error && <p role="alert">{error}</p>}
  </aside>;
}
