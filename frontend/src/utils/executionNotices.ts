import type { ExecutionNotice } from "../services/lianxinApi";

export function mergeExecutionNotices(current: ExecutionNotice[], incoming: ExecutionNotice[], sessionId?: number): ExecutionNotice[] {
  const visible = [...current];
  for (const item of incoming) {
    if (item.sessionId != null && item.sessionId !== sessionId) continue;
    if (item.state !== "completed" && !item.message.trim()) continue;
    const index = visible.findIndex((previous) => previous.source === item.source
      && previous.sessionId === item.sessionId && previous.requestId === item.requestId);
    if (index >= 0) visible.splice(index, 1);
    if (item.state !== "completed") visible.push(item);
  }
  return visible.slice(-4);
}

export function showExecutionNotice(message: string, code: string, sessionId?: number, requestId = "local", level: ExecutionNotice["level"] = "warning") {
  window.dispatchEvent(new CustomEvent<ExecutionNotice>("lianxin-execution-notice", { detail: {
    id: Date.now(), timestamp: Date.now(), message, code, source: "chat",
    sessionId, requestId, state: level === "info" ? "running" : "failed", level,
  } }));
}
