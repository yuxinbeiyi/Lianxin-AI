import type { ExecutionScope, ExecutionTaskState } from "../services/lianxinApi";
import type { Message } from "../types/ui";

export function executionScope(state: ExecutionTaskState | null | undefined, sessionId?: number | null): ExecutionScope | null {
  const task = state?.task;
  if (!state?.running || !task?.taskId || !sessionId || task.sessionId !== sessionId) return null;
  return { sessionId, taskId: task.taskId, threadId: task.threadId ?? "", turnId: task.turnId ?? "" };
}

export function executionSignature(state: ExecutionTaskState): string {
  const { elapsedSeconds: _elapsed, ...task } = state.task ?? {};
  return JSON.stringify([state.running, task, state.pendingApprovals, state.lastResult,
    state.sandbox, state.approvalMode, state.networkAccess]);
}

export function mergePersistedMessages(current: Message[], persisted: Message[]): Message[] {
  const messages = new Map<string, Message>();
  for (const message of [...current, ...persisted]) {
    const taskId = message.kind === "execution_task" ? message.execution?.taskId : "";
    messages.set(taskId ? `execution:${taskId}` : `message:${message.id}`, message);
  }
  return Array.from(messages.values()).sort((left, right) => {
    const leftId = Number(left.id);
    const rightId = Number(right.id);
    if (Number.isFinite(leftId) && Number.isFinite(rightId)) return leftId - rightId;
    return 0;
  });
}

export function continuationMode(status?: string): "resume" | "revise" | "retry" | null {
  if (status === "interrupted" || status === "stopped") return "resume";
  if (status === "completed") return "revise";
  if (status === "failed") return "retry";
  return null;
}
