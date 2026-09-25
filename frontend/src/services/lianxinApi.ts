export type ApiMessage = { role: "user" | "assistant"; content: string; id?: number; timestamp?: string };
export type ChatStreamEvent = {
  type: "started" | "tool_round_start" | "tool_call" | "tool_result" | "delta" | "completed" | "error";
  round?: number;
  name?: string;
  args?: Record<string, unknown>;
  preview?: string;
  isError?: boolean;
  elapsedMs?: number;
  content?: string;
  error?: string;
  message?: { role: "assistant"; content: string };
};
export type BackgroundState = { enabled: boolean; opacity: number; fitMode: "cover" | "contain" | "stretch"; fingerprint: string; dataUrl?: string };
export type AvatarState = { enabled: boolean; size: number; gap: number; border: boolean; assistantDataUrl?: string; userDataUrl?: string; fingerprint: string };
export type LegacyFeature =
  | "ripple" | "persona" | "memory-constellation" | "prism-memory"
  | "history" | "note" | "workflow" | "duty" | "proactive" | "alarm" | "reminder"
  | "settings" | "api" | "network" | "capability" | "vision" | "voice-stt" | "sound"
  | "qq" | "wechat" | "study-room" | "time-capsule" | "data-tide" | "camera" | "galgame";

const API_ROOT = "http://127.0.0.1:8766/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, { ...init, headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) } });
  if (!response.ok) throw new Error((await response.text()) || `API ${response.status}`);
  return response.json() as Promise<T>;
}

export const lianxinApi = {
  status: () => request<{ online: boolean; sessionId: number; busy: boolean }>("/app/status"),
  sessions: (keyword = "") => request<{ items: Array<{ id: number; title?: string; updated_at?: string; summary?: string; is_pinned?: number }> }>(`/conversations${keyword ? `?q=${encodeURIComponent(keyword)}` : ""}`),
  messages: (id: number) => request<{ items: ApiMessage[] }>(`/conversations/${id}/messages`),
  newSession: () => request<{ id: number }>("/conversations", { method: "POST", body: "{}" }),
  selectSession: (id: number) => request<{ id: number }>(`/conversations/${id}/select`, { method: "POST", body: "{}" }),
  deleteSession: (id: number) => request<{ deleted: number }>(`/conversations/${id}`, { method: "DELETE" }),
  updateSession: (id: number, payload: { title?: string; togglePin?: boolean }) => request(`/conversations/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  note: () => request<{ content: string }>("/note"),
  saveNote: (content: string) => request<{ content: string }>("/note", { method: "POST", body: JSON.stringify({ content }) }),
  tasks: () => request<{ todos: unknown[]; liveTodos: unknown[]; autoTasks: unknown[]; logs: unknown[]; progress: { completed: number; total: number; active: string } }>("/tasks"),
  musicState: () => request<Record<string, unknown>>("/music/state"),
  musicControl: (action: string) => request<Record<string, unknown>>("/music/control", { method: "POST", body: JSON.stringify({ action }) }),
  voiceStatus: () => request<{ active: boolean; state: string }>("/voice/status"),
  startVoice: () => request<{ active: boolean; state: string }>("/voice/start", { method: "POST", body: "{}" }),
  stopVoice: () => request<{ active: boolean; state: string }>("/voice/stop", { method: "POST", body: "{}" }),
  voiceEvents: (after: number) => request<{ items: Array<{ id: number; type: string; state?: string; content?: string; error?: string }>; latest: number }>(`/voice/events?after=${after}`),
  proactiveState: () => request<{ desktopEnabled: boolean; qqEnabled: boolean; frequency: number; minIntervalMinutes: number }>("/proactive/state"),
  toggleProactive: (enabled: boolean) => request<{ desktopEnabled: boolean }>("/proactive/toggle", { method: "POST", body: JSON.stringify({ enabled }) }),
  managementState: () => request<{ modules: Array<{ id: string; label: string; available: boolean; state?: unknown }> }>("/management/state"),
  fiveAxis: () => request<{ axes: Record<string, number>; mood: string }>("/emotion/five-axis"),
  background: (include = true) => request<BackgroundState>(`/settings/background?include=${include ? "1" : "0"}`),
  avatars: (include = true) => request<AvatarState>(`/settings/avatars?include=${include ? "1" : "0"}`),
  openLegacy: (feature: LegacyFeature) => request<{ feature: string; pid: number; mode: string }>("/legacy/open", { method: "POST", body: JSON.stringify({ feature }) }),
  taskSnapshot: () => request<{ workflowRuns: unknown[]; todos: unknown[]; autoTasks: unknown[]; logs: unknown[]; progress: { completed: number; total: number; active: string } }>("/tasks/snapshot"),
  streamChat: async function* (message: string) {
    const response = await fetch(`${API_ROOT}/chat/stream`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message }) });
    if (!response.ok || !response.body) throw new Error(`API ${response.status}`);
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
    while (true) { const part = await reader.read(); if (part.done) break; buffer += decoder.decode(part.value, { stream: true }); const chunks = buffer.split("\n\n"); buffer = chunks.pop() ?? ""; for (const chunk of chunks) { const line = chunk.split("\n").find((item) => item.startsWith("data: ")); if (line) yield JSON.parse(line.slice(6)) as ChatStreamEvent; } }
  },
};
