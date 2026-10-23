export type ApiMessage = {
  role: "user" | "assistant";
  content: string;
  id?: number;
  timestamp?: string;
  attachments?: Array<{ kind: "image" | "file"; fileName: string; path?: string }>;
  // 执行任务卡片（会话消息 metadata 会被后端平铺成消息字段）
  kind?: string;
  executionTaskId?: string;
  executionThreadId?: string;
  executionStatus?: string;
  executionGoal?: string;
  executionRounds?: number;
  executionElapsed?: number;
  executionReason?: string;
  executionSummary?: string;
  executionNarrated?: boolean;
};
export type ChatStreamEvent = {
  type: "started" | "image_analysis_started" | "image_analysis_result" | "file_attachment_saved" | "tool_round_start" | "tool_call" | "tool_result" | "delta" | "completed" | "error";
  round?: number;
  name?: string;
  args?: Record<string, unknown>;
  preview?: string;
  isError?: boolean;
  elapsedMs?: number;
  content?: string;
  error?: string;
  index?: number;
  fileName?: string;
  description?: string;
  message?: { role: "assistant"; content: string };
};
export type ToolActivityEvent = {
  id: number;
  kind: "call" | "result";
  seq: number;
  ts?: number;
  sessionId?: number;
  turn?: number;
  round?: number;
  name?: string;
  argsPreview?: string;
  status?: "done" | "error";
  preview?: string;
  elapsedMs?: number;
};
export type ExecutionApprovalFile = {
  path: string;
  movePath?: string;
  kind: string;
  diff: string;
  truncated?: boolean;
  diffBytes?: number;
  additions: number;
  deletions: number;
};
export type ExecutionApproval = {
  requestId: string;
  method?: string;
  approvalKind?: "fileChange" | "command" | "command+patch" | "permissions" | "other";
  title: string;
  itemId?: string;
  command?: string;
  cwd?: string;
  reason?: string;
  files?: ExecutionApprovalFile[];
  additions?: number;
  deletions?: number;
  askedAt?: number;
  auto?: boolean;
};
export type ExecutionTaskState = {
  enabled: boolean;
  closed?: boolean;
  running: boolean;
  backend: string;
  sandbox: string;
  approvalMode: string;
  networkAccess: boolean;
  workspace?: string;
  limits: {
    max_rounds: number;
    max_stale_rounds: number;
    timeout_seconds: number;
    max_declines: number;
    approval_timeout_seconds: number;
  };
  task: null | {
    goal: string;
    cwd?: string;
    sandbox?: string;
    approvalMode?: string;
    turn?: number;
    startedAt?: number;
    elapsedSeconds?: number;
    rounds?: number;
    staleRounds?: number;
    declines?: number;
    threadId?: string;
    state?: string;
  };
  lastResult: null | {
    status: string;
    reason?: string;
    goal?: string;
    rounds?: number;
    declines?: number;
    elapsedSeconds?: number;
    summary?: string;
    finishedAt?: number;
    threadId?: string;
  };
  pendingApprovals: ExecutionApproval[];
  backendStatus?: null | {
    available: boolean;
    backend?: string;
    version?: string;
    model?: string;
    provider?: string;
    reason?: string;
  };
};
export type BackgroundState = { enabled: boolean; opacity: number; chatOpacity?: number; fitMode: "cover" | "contain" | "stretch"; fingerprint: string; dataUrl?: string };
export type AvatarState = { enabled: boolean; size: number; gap: number; border: boolean; assistantDataUrl?: string; userDataUrl?: string; characterDataUrl?: string; characterFingerprint?: string; fingerprint: string };
export type PersonaState = { snapshot: { profile: Record<string, any>; revision: number; enabled: boolean; activated_at: string }; profiles: Array<Record<string, any>>; preview: { text: string; estimated_tokens: number; layers: string[]; error?: string }; growth: { summary: Record<string, any>; events: Array<Record<string, any>>; settings: Record<string, any> } };
export type SettingsPanelState = { global: { silentMode: boolean; userName: string; emotionProbability: number; backgroundEnabled: boolean; backgroundSource: string; backgroundSourceType: string; backgroundOpacity: number; chatBackgroundOpacity: number; backgroundFitMode: string; segmentPauseChatMin: number; segmentPauseChatMax: number; segmentPauseSemanticMin: number; segmentPauseSemanticMax: number }; sound: { ttsVolume: number; sfxVolume: number; silentMode: boolean; engine: string; speed: number; defaultMood: string; gptSovitsVersion: string; gptSovitsPath: string; refWavOverride: string } };
export type BilibiliSurfTag = {
  keyword: string;
  search_query: string;
  intent_note: string;
  query_source: "user" | "original";
  status: "active" | "paused";
  source: string;
  base_score: number;
  boost_score: number;
};
export type BilibiliSurfState = {
  ok: boolean;
  error?: string;
  tags: BilibiliSurfTag[];
  latest: null | {
    run_id: string;
    timestamp: number;
    tag: string;
    query: string;
    query_source: string;
    returned_count: number;
    fresh_count: number;
    enriched_count: number;
    selection_status: string;
    reason: string;
    summary: string;
    send_status: string;
    duration_ms: number;
  };
};
export type BilibiliInteractionState = {
  settings: {
    mode: "read_only" | "test";
    modeLabel: string;
    userUid: string;
    userName: string;
    userUidConfirmed: boolean;
    testVideo: string;
    testBvid: string;
    allowOtherReplies: boolean;
    autoCommentEnabled: boolean;
    paused: boolean;
  };
  account: { connected: boolean; uid: string; name: string; credentialStoreAvailable: boolean };
  login: { pending: boolean };
  draft: null | {
    id: string;
    status: string;
    bvid: string;
    text: string;
    mentionUid: string;
    mentionName: string;
    video: { bvid: string; title: string; author: string; commentOpen: boolean };
    createdAt: number;
  };
  writeCapability: { ready: boolean; structuredMentionSupported: boolean; blockers: string[] };
  replyCapability: { ready: boolean; allowOtherReplies: boolean; blockers: string[] };
  records?: Array<{ bvid: string; rpid: string; status: string; diagnostic: string }>;
};
export type LegacyFeature =
  | "ripple" | "persona" | "memory-constellation" | "prism-memory"
  | "history" | "note" | "workflow" | "duty" | "proactive" | "alarm" | "reminder"
  | "settings" | "api" | "network" | "capability" | "vision" | "voice-stt" | "sound"
  | "qq" | "wechat" | "study-room" | "time-capsule" | "data-tide" | "camera" | "galgame"
  | "video-call";

const API_ROOT = "http://127.0.0.1:8766/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, { ...init, headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) } });
  if (!response.ok) throw new Error((await response.text()) || `API ${response.status}`);
  return response.json() as Promise<T>;
}

export type MusicFeedbackSettings = {
  enabled: boolean;
  delaySeconds: number;
  minimumListenSeconds: number;
  cooldownSeconds: number;
  autoSpeak: boolean;
  saveToChat: boolean;
};

export const lianxinApi = {
  status: () => request<{ online: boolean; sessionId: number; busy: boolean }>("/app/status"),
  cancelChat: () => request<{ cancelled: boolean }>("/chat/cancel", { method: "POST", body: "{}" }),
  sessions: (keyword = "") => request<{ items: Array<{ id: number; title?: string; updated_at?: string; summary?: string; is_pinned?: number }> }>(`/conversations${keyword ? `?q=${encodeURIComponent(keyword)}` : ""}`),
  messages: (id: number, after = 0) => request<{ items: ApiMessage[] }>(`/conversations/${id}/messages${after ? `?after=${after}` : ""}`),
  newSession: () => request<{ id: number }>("/conversations", { method: "POST", body: "{}" }),
  selectSession: (id: number) => request<{ id: number }>(`/conversations/${id}/select`, { method: "POST", body: "{}" }),
  deleteSession: (id: number) => request<{ deleted: number }>(`/conversations/${id}`, { method: "DELETE" }),
  updateSession: (id: number, payload: { title?: string; togglePin?: boolean }) => request(`/conversations/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  note: () => request<{ content: string }>("/note"),
  saveNote: (content: string) => request<{ content: string }>("/note", { method: "POST", body: JSON.stringify({ content }) }),
  tasks: () => request<{ todos: unknown[]; liveTodos: unknown[]; autoTasks: unknown[]; logs: unknown[]; progress: { completed: number; total: number; active: string } }>("/tasks"),
  musicState: () => request<Record<string, unknown>>("/music/state"),
  musicControl: (action: string, payload?: Record<string, unknown>) => request<Record<string, unknown>>("/music/control", { method: "POST", body: JSON.stringify({ action, ...(payload ?? {}) }) }),
  musicEnsure: () => request<{ online: boolean; url: string }>("/music/ensure"),
  musicEvents: (after: number) => request<{ items: Array<{ id: number; type: string; content?: string; messageId?: number; status?: string; track?: string; trackId?: string }>; latest: number }>(`/music/events?after=${after}`),
  musicStats: () => request<{
    total_seconds: number;
    total_hours: number;
    today_seconds: number;
    week_seconds: number;
    play_count: number;
    feedback_count: number;
    most_played: { name: string; seconds: number } | null;
    tracks: Array<{ source: string; track_id: string; name: string; artist: string; seconds: number; play_count: number; last_played: string }>;
    recent: Array<{ source: string; track_id: string; name: string; artist: string; last_played: string }>;
  }>("/music/stats"),
  musicPlaylists: (offset = 0, limit = 24) => request<{ playlists?: Array<{ id: string; name: string; trackCount?: number; creator?: string; coverUrl?: string }>; total?: number; hasMore?: boolean }>(`/music/playlists?offset=${offset}&limit=${limit}`),
  musicPlaylist: (id: string) => request<{ playlist?: { id: string; name: string; tracks?: Array<{ id: string; name?: string; artist?: string; durationMs?: number }> }; tracks?: Array<{ id: string; name?: string; artist?: string; durationMs?: number }> }>(`/music/playlist?id=${encodeURIComponent(id)}`),
  musicPlayPlaylist: (id: string) => request<Record<string, unknown>>("/music/play-playlist", { method: "POST", body: JSON.stringify({ id }) }),
  musicFeedbackSettings: () => request<MusicFeedbackSettings>("/music/feedback-settings"),
  saveMusicFeedbackSettings: (payload: Partial<MusicFeedbackSettings>) => request<MusicFeedbackSettings>("/music/feedback-settings", { method: "POST", body: JSON.stringify(payload) }),
  openPlayer: () => request<{ ok: boolean; online: boolean; url: string }>("/open-player", { method: "POST", body: "{}" }),
  musicSpaceSettings: () => request<{ wallpapers: Array<{ id: string; name: string; url: string }>; settings: { wallpaper: string; wallpaper_opacity: number; content_mask_opacity: number; fit: string } }>("/music/space-settings"),
  saveMusicSpaceSettings: (payload: { wallpaper: string; wallpaper_opacity: number; content_mask_opacity: number; fit: string }) => request<Record<string, unknown>>("/music/space-settings", { method: "POST", body: JSON.stringify(payload) }),
  voiceStatus: () => request<{ active: boolean; state: string }>("/voice/status"),
  startVoice: () => request<{ active: boolean; state: string }>("/voice/start", { method: "POST", body: "{}" }),
  stopVoice: () => request<{ active: boolean; state: string }>("/voice/stop", { method: "POST", body: "{}" }),
  setVoiceMic: (muted: boolean) => request<{ active: boolean; muted: boolean }>("/voice/mic", { method: "POST", body: JSON.stringify({ muted }) }),
  toolEvents: (after: number) => request<{ items: ToolActivityEvent[]; latest: number }>(`/tools/events?after=${after}`),
  runtimeState: () => request<ExecutionTaskState>("/runtime/state"),
  runtimeEvents: (after: number) => request<{ items: Array<{ id: number; kind: string; ts: number; threadId?: string; turnId?: string; payload: Record<string, unknown> }>; latest: number; running?: boolean }>(`/runtime/events?after=${after}`),
  runtimeStart: (goal: string, options: { sessionId?: number; turn?: number; cwd?: string; sandbox?: string; networkAccess?: boolean; wait?: boolean; resumeThreadId?: string } = {}) =>
    request<{ ok: boolean; error?: string; state: ExecutionTaskState }>("/runtime/start", { method: "POST", body: JSON.stringify({ goal, ...options }) }),
  runtimeStop: (reason = "user") => request<{ ok: boolean; stopped: boolean; state: ExecutionTaskState }>("/runtime/stop", { method: "POST", body: JSON.stringify({ reason }) }),
  runtimeApprove: (requestId: string, decision: "accept" | "acceptForSession" | "decline" | "cancel" = "accept", remember = false) =>
    request<{ ok: boolean; decision?: string; error?: string }>("/runtime/approve", { method: "POST", body: JSON.stringify({ requestId, decision, remember }) }),
  voiceEvents: (after: number) => request<{ items: Array<{ id: number; type: string; state?: string; content?: string; error?: string }>; latest: number }>(`/voice/events?after=${after}`),
  startVoiceInput: () => request<{ active: boolean }>("/voice/input/start", { method: "POST", body: "{}" }),
  stopVoiceInput: () => request<{ active: boolean }>("/voice/input/stop", { method: "POST", body: "{}" }),
  proactiveState: () => request<{ desktopEnabled: boolean; qqEnabled: boolean; frequency: number; minIntervalMinutes: number }>("/proactive/state"),
  toggleProactive: (enabled: boolean) => request<{ desktopEnabled: boolean }>("/proactive/toggle", { method: "POST", body: JSON.stringify({ enabled }) }),
  proactiveStatus: () => request<{ ready: boolean; running: boolean }>("/proactive/status"),
  proactiveTrigger: (mode: string) => request<{ ok: boolean }>("/proactive/trigger", { method: "POST", body: JSON.stringify({ mode }) }),
  bilibiliInteractionState: () => request<BilibiliInteractionState>("/bilibili-interaction/state"),
  bilibiliSurfState: () => request<BilibiliSurfState>("/bilibili-surf/state"),
  saveBilibiliSurfTag: (payload: { keyword: string; search_query?: string; intent_note?: string; status?: "active" | "paused" }) => request<BilibiliSurfState>("/bilibili-surf/tag", { method: "POST", body: JSON.stringify(payload) }),
  recheckBilibiliComment: (rpid = "") => request<{ ok: boolean; message?: string; error?: string; diagnostic?: string; state?: BilibiliInteractionState }>("/bilibili-interaction/comment/recheck", { method: "POST", body: JSON.stringify({ rpid }) }),
  saveBilibiliInteractionSettings: (payload: { mode?: "read_only" | "test"; userUid?: string; testVideo?: string; allowOtherReplies?: boolean; autoCommentEnabled?: boolean; paused?: boolean }) => request<{ ok: boolean; error?: string; state?: BilibiliInteractionState }>("/bilibili-interaction/settings", { method: "POST", body: JSON.stringify(payload) }),
  verifyBilibiliUserUid: (uid: string) => request<{ ok: boolean; uid?: string; name?: string; error?: string }>("/bilibili-interaction/verify-user", { method: "POST", body: JSON.stringify({ uid }) }),
  confirmBilibiliUserUid: (uid: string) => request<{ ok: boolean; error?: string; state?: BilibiliInteractionState }>("/bilibili-interaction/confirm-user", { method: "POST", body: JSON.stringify({ uid }) }),
  inspectBilibiliTestVideo: (target: string) => request<{ ok: boolean; error?: string; video?: { bvid: string; title: string; author: string; commentOpen: boolean } }>("/bilibili-interaction/inspect-video", { method: "POST", body: JSON.stringify({ target }) }),
  requestBilibiliTestComment: (target = "", draftId = "", confirmed = false) => request<{ ok: boolean; sent: boolean; status: string; message?: string; error?: string; blockers?: string[]; state?: BilibiliInteractionState }>("/bilibili-interaction/test-comment", { method: "POST", body: JSON.stringify({ target, draftId, confirmed }) }),
  startBilibiliLogin: () => request<{ ok: boolean; error?: string; key?: string; url?: string; qrDataUrl?: string; expiresIn?: number }>("/bilibili-interaction/login/start", { method: "POST", body: "{}" }),
  pollBilibiliLogin: (key: string) => request<{ ok: boolean; state: string; error?: string; account?: { uid: string; name: string } }>("/bilibili-interaction/login/poll", { method: "POST", body: JSON.stringify({ key }) }),
  logoutBilibili: () => request<{ ok: boolean; error?: string; state?: BilibiliInteractionState }>("/bilibili-interaction/logout", { method: "POST", body: "{}" }),
  createBilibiliCommentDraft: (target: string, text = "") => request<{ ok: boolean; status?: string; error?: string; blockers?: string[]; draft?: BilibiliInteractionState["draft"]; state: BilibiliInteractionState }>("/bilibili-interaction/draft", { method: "POST", body: JSON.stringify({ target, text }) }),
  clearBilibiliCommentDraft: () => request<{ ok: boolean; state: BilibiliInteractionState }>("/bilibili-interaction/draft/clear", { method: "POST", body: "{}" }),
  managementState: () => request<{ modules: Array<{ id: string; label: string; available: boolean; state?: unknown }> }>("/management/state"),
  personaState: () => request<PersonaState>("/persona/state"),
  personaSave: (profile: Record<string, unknown>) => request<PersonaState>("/persona/save", { method: "POST", body: JSON.stringify(profile) }),
  personaCreate: (profileName: string) => request<PersonaState>("/persona/create", { method: "POST", body: JSON.stringify({ profile_name: profileName }) }),
  personaActivate: (id: string, enabled = true) => request<PersonaState>("/persona/activate", { method: "POST", body: JSON.stringify({ id, enabled }) }),
  personaToggle: (enabled: boolean) => request<PersonaState>("/persona/toggle", { method: "POST", body: JSON.stringify({ enabled }) }),
  personaDelete: (id: string) => request<PersonaState>("/persona/delete", { method: "POST", body: JSON.stringify({ id }) }),
  fiveAxis: () => request<{ axes: Record<string, number>; mood: string }>("/emotion/five-axis"),
  background: (include = true) => request<BackgroundState>(`/settings/background?include=${include ? "1" : "0"}`),
  avatars: (include = true) => request<AvatarState>(`/settings/avatars?include=${include ? "1" : "0"}`),
  settingsPanels: () => request<SettingsPanelState>("/settings/panels"),
  saveSettingsPanels: (payload: Partial<{ global: Partial<SettingsPanelState["global"]>; sound: Partial<SettingsPanelState["sound"]> }>) => request<SettingsPanelState>("/settings/panels", { method: "POST", body: JSON.stringify(payload) }),
  avatarAction: (action: "tap" | "headpat") => request<{ action: string; accepted?: boolean; sound?: boolean; response?: string; counterAction?: string; message?: string }>(`/avatar/action?action=${action}`),
  openLegacy: (feature: LegacyFeature) => request<{ feature: string; pid: number; mode: string }>("/legacy/open", { method: "POST", body: JSON.stringify({ feature }) }),
  qqStatus: () => request<{ running: boolean; connected: boolean; state: string; url: string; error?: string; disconnectReason?: string; fastReplyEnabled: boolean }>("/qq/status"),
  startQq: () => request<{ running: boolean; connected: boolean; state: string; url: string }>("/qq/start", { method: "POST", body: "{}" }),
  stopQq: () => request<{ running: boolean; connected: boolean; state: string; url: string }>("/qq/stop", { method: "POST", body: "{}" }),
  reloadQq: () => request<{ running: boolean; connected: boolean; state: string; url: string }>("/qq/reload", { method: "POST", body: "{}" }),
  setQqFastReply: (enabled: boolean) => request<{ running: boolean; connected: boolean; state: string; fastReplyEnabled: boolean }>("/qq/fast-reply", { method: "POST", body: JSON.stringify({ enabled }) }),
  taskSnapshot: () => request<{ workflowRuns: unknown[]; todos: unknown[]; autoTasks: unknown[]; logs: unknown[]; progress: { completed: number; total: number; active: string } }>("/tasks/snapshot"),
  taskEvents: (after: number) => request<{ items: Array<{ id: number; type: string; data: any }>; latest: number }>(`/tasks/events?after=${after}`),
  capabilities: () => request<{ items: Array<{ name: string; display_name: string; description: string; category: string; enabled: boolean; available: boolean; favorite: boolean }> }>("/capabilities"),
  speak: (text: string) => request<{ speaking: boolean }>("/tts/speak", { method: "POST", body: JSON.stringify({ text }) }),
  stopSpeaking: () => request<{ speaking: boolean }>("/tts/stop", { method: "POST", body: "{}" }),
  ttsStatus: () =>
    request<{
      speaking: boolean;
      stage?: { key: string; text: string };
      notice?: { id: number; engine: string; reason: string; text: string } | null;
    }>("/tts/status"),
  playSound: (name: string) => request<{ played: boolean; name: string }>("/sound/play", { method: "POST", body: JSON.stringify({ name }) }),
  startCallSound: () => request<{ playing: boolean }>("/sound/call-wait/start", { method: "POST", body: "{}" }),
  stopCallSound: () => request<{ playing: boolean }>("/sound/call-wait/stop", { method: "POST", body: "{}" }),
  timeCapsule: (day = "") => request<{ day: any; timeline: any[]; today: string }>(`/time-capsule/state${day ? `?day=${encodeURIComponent(day)}` : ""}`),
  saveTimeCapsule: (day: string, content: string) => request<Record<string, unknown>>("/time-capsule/save", { method: "POST", body: JSON.stringify({ day, content }) }),
  sealTimeCapsule: (day: string, content: string) => request<Record<string, unknown>>("/time-capsule/seal", { method: "POST", body: JSON.stringify({ day, content }) }),
  streamChat: async function* (message: string, attachments: Array<{ kind: "image" | "file"; fileName: string; dataUrl: string }> = [], selection: { forcedTool?: string; preferredTool?: string } = {}, quote?: { role: "user" | "assistant"; content: string }) {
    const response = await fetch(`${API_ROOT}/chat/stream`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message, attachments, ...selection, quote }) });
    if (!response.ok || !response.body) throw new Error(`API ${response.status}`);
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
    while (true) { const part = await reader.read(); if (part.done) break; buffer += decoder.decode(part.value, { stream: true }); const chunks = buffer.split("\n\n"); buffer = chunks.pop() ?? ""; for (const chunk of chunks) { const line = chunk.split("\n").find((item) => item.startsWith("data: ")); if (line) yield JSON.parse(line.slice(6)) as ChatStreamEvent; } }
  },
};
