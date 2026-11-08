export type ConfigPanelDomain = "stt" | "sound" | "network";
export type ConfigPanelValue = string | number | boolean | string[];
export type ConfigPanelTask = { id: number; status: "idle" | "running" | "done" | "error"; message: string; progress: number | null; committedRevision?: string };
export type ConfigNetworkTool = { id: string; name: string; description: string; enabled: boolean; checked: boolean; state: string };
export type NativeConfigState = {
  sections: Record<string, Record<string, ConfigPanelValue>>; revision: string;
  task?: ConfigPanelTask; output?: { silent_mode: boolean; tts_volume: number; sfx_volume: number };
  migration?: boolean; modelPath?: string; advancedDefaults?: Record<string, ConfigPanelValue>;
  catalog?: Record<"search" | "fetch", ConfigNetworkTool[]>;
};
export type CapabilityUsage = { tool_name: string; call_count: number; success_rate: number; avg_duration_ms: number; last_called: string };
export type CapabilityTool = { name: string; description?: string; parameters?: Record<string, unknown> };
export type CapabilityItem = CapabilityTool & {
  display_name: string; category: string; source_kind: "builtin" | "skill" | "mcp";
  provider_id: string; provider_name: string; enabled: boolean; available: boolean; favorite: boolean;
  version: string; status: string; usage: CapabilityUsage;
};
export type CapabilityExtension = {
  name: string; display_name: string; description: string; version: string; enabled: boolean;
  license?: string; external?: boolean; connected?: boolean; error?: string; tools: CapabilityTool[];
};
export type CapabilityLog = { timestamp: string; level: string; message: string };
export type CapabilityPanelState = {
  items: CapabilityItem[]; skills: CapabilityExtension[]; mcp: CapabilityExtension[]; categories: string[];
  mcpScanning: boolean; lastRefresh: string; logs: CapabilityLog[]; installLogs: CapabilityLog[];
};
export type CapabilityUsageState = {
  overview: { call_count: number; success_rate: number; avg_duration_ms: number; used_tool_count: number };
  unused: number; items: Array<CapabilityUsage & { display_name: string; provider_name: string }>;
};
export type CapabilityPluginInfo = { type: string; name: string; description: string; version: string; source: string };

export type ExecutionNotice = {
  id: number;
  timestamp: number;
  message: string;
  code: string;
  source: string;
  sessionId?: number | null;
  requestId: string;
  state: "running" | "completed" | "failed" | "cancelled" | "skipped";
  level: "info" | "warning" | "error";
};

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
  executionTurnId?: string;
  executionStatus?: string;
  executionGoal?: string;
  executionRounds?: number;
  executionElapsed?: number;
  executionReason?: string;
  executionApprovalReason?: string;
  executionSummary?: string;
  executionNarrated?: boolean;
  executionGoalProgress?: ExecutionGoalProgress | null;
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
export type DecisionSettings = {
  enabled: boolean;
  provider: "api" | "local";
  api_models: { id: string; size: string }[];
  api_fallback_enabled: boolean;
  allow_cloud_context: boolean;
  timeout_seconds: number;
  budget_seconds: number;
  local_model_dir: string;
  local_python: string;
  local_port: number;
  local_device: string;
  local_auto_start: boolean;
  api_key_configured: boolean;
  api_key_source: "saved" | "environment" | "none";
  api_key_error: string;
  api_model: string;
  local_model: string;
  local: DecisionLocalState;
};

export type DecisionLocalState = {
  status: string;
  ready: boolean;
  managed: boolean;
  auto_start_paused: boolean;
  pid: number | null;
  port: number | null;
  device: string | null;
  modalities: string[];
};

export type DecisionRuntime = {
  local: DecisionLocalState;
  gpu: {
    status: string;
    devices: { index: number; uuid: string; name: string; total_mb: number | null;
      used_mb: number | null; free_mb: number | null; utilization_percent: number | null }[];
    process: { pid: number | null; status: string; used_mb: number | null };
  };
  last_decision: {
    configured_model: string; model: string; provider: string; stage: string; attempt: number;
    fallback_reason: string; error: string | null; status: string; elapsedMs: number | null; updated_at: number;
  } | null;
};

export type ChatRequestMetadata = { force_memory_activation: true };
export type MemoryActivationDebug = {
  trigger: "forced" | "ordinary";
  phase: "started" | "retrieved" | "deciding" | "completed";
  status: string;
  reason: string;
  retrieval_status: string;
  retrieval_reason: string;
  candidate_ids: number[];
  channels: string[];
  inspected: number;
  retrieval_elapsed_ms: number;
  decision_called: boolean;
  decision_status: "not_called" | "running" | "validated" | "failed";
  provider: "api" | "local" | null;
  model: string | null;
  action: "suppress" | "prompt_only" | "light_mention";
  prompt_injected: boolean;
  candidate_id: number | null;
  expression_status: "unknown";
  reply_status: "pending" | "finished" | "failed" | "cancelled";
};

export type ToolActivityEvent = {
  id: number;
  kind: "call" | "result" | "memory_activation";
  category?: "tool" | "decision" | "memory_activation";
  activation?: MemoryActivationDebug;
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
export type ExecutionScope = { sessionId: number; taskId: string; threadId: string; turnId: string };
export type ExecutionPlanStep = { step: string; status: "pending" | "in_progress" | "completed" };
export type ExecutionGoalContract = {
  objective: string;
  acceptanceCriteria: string[];
  maxTurns?: number;
  maxTokens?: number;
  maxElapsedSeconds?: number;
  driver?: "native";
  verificationChecks?: Array<{ criterionIndex: number; command: string; kind?: "test" | "build" }>;
};
export type ExecutionGoalProgress = {
  goalId: string;
  state: string;
  contract: ExecutionGoalContract;
  turnsStarted: number;
  turnsCompleted: number;
  tokensUsed: number;
  elapsedSeconds: number;
  verificationPassed: boolean;
  endReason?: string;
  evidence: Array<{ criterionIndex: number; kind: string; status: string; summary: string; source: string; turnId: string }>;
};
export type ExecutionGoalMode = { available: boolean; driver: "native"; code: string; reason: string };
export type ExecutionTaskState = {
  enabled: boolean;
  closed?: boolean;
  running: boolean;
  backend: string;
  goalMode?: ExecutionGoalMode;
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
    taskId?: string;
    sessionId?: number;
    turnId?: string;
    plan?: ExecutionPlanStep[];
    currentStep?: string;
    planExplanation?: string;
    lastControl?: { status?: string; message?: string; at?: number };
    lastApproval?: { decision?: string; reason?: string; at?: number };
    stopReason?: string;
    goal: string;
    goalProgress?: ExecutionGoalProgress | null;
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
    taskId?: string;
    sessionId?: number;
    status: string;
    goalProgress?: ExecutionGoalProgress | null;
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
export type AvatarState = { enabled: boolean; size: number; gap: number; border: boolean; assistantDataUrl?: string; userDataUrl?: string; characterDataUrl?: string; characterPath?: string; uploadedPath?: string; role?: "assistant" | "user" | "character"; sourcePath?: string; characterFingerprint?: string; fingerprint: string };
export type PersonaPreview = { text: string; estimated_tokens: number; layers: string[]; error?: string };
export type GrowthEvent = {
  id: number; persona_id: string; title: string; detail: string; evidence: string;
  status: string; field: string; proposed_value: string; old_value: string;
  scope: "shared" | "persona"; scene: string; source_kind: string;
  source_session_id: number; source_message_ids: number[]; source_channel: string;
  created_at: string; applied_at: string; expires_at: string; risk: string;
  supersedes_id: number; evidence_count: number;
  evidence_sources: Array<{ source_ref: string; evidence_text: string }>;
};
export type GrowthState = {
  persona_id: string; events: GrowthEvent[]; effective_ids: number[]; context: string;
  summary: Record<string, any>;
  settings: { mode: "off" | "confirm" | "low_risk_auto"; growth_paused_until: string;
    allow_proactive_requests: boolean; allow_photo_invites: boolean };
};
export type PersonaState = { snapshot: { profile: Record<string, any>; revision: number; enabled: boolean; activated_at: string }; profiles: Array<Record<string, any>>; preview: PersonaPreview; growth: { summary: Record<string, any>; events: Array<Record<string, any>>; settings: Record<string, any> }; created_id?: string };
export type SettingsGlobal = {
  silentMode: boolean; userName: string; emotionProbability: number;
  showExitConfirmation: boolean; startupCheckEnabled: boolean; startupMode: "fast" | "complete";
  trayEnabled: boolean; closeBehavior: "ask" | "tray" | "quit"; minimizeToTray: boolean;
  restoreWindowState: boolean; alwaysOnTop: boolean; reducedMotion: boolean; desktopNotifications: boolean;
  fontSize: number; noteFilePath: string; backgroundEnabled: boolean; backgroundSource: string;
  backgroundSourceType: "single" | "folder_random" | "folder_first"; backgroundOpacity: number;
  chatBackgroundOpacity: number; backgroundFitMode: "cover" | "contain" | "stretch"; defaultBackgroundSource: string;
  avatarMode: "animated" | "static"; avatarPath: string; avatarSourcePath: string; firstMeetDate: string; autostart: boolean;
  segmentPauseChatMin: number; segmentPauseChatMax: number;
  segmentPauseSemanticMin: number; segmentPauseSemanticMax: number;
};
export type ChatAvatarSettings = {
  enabled: boolean; size: number; gap: number; border: boolean; assistant_path: string; user_path: string;
  interactions_enabled: boolean; dynamic_response: boolean; counter_tap: boolean;
  animation_enabled: boolean; response_in_chat: boolean; tap_cooldown_seconds: number;
};
export type SettingsPanelState = {
  global: SettingsGlobal;
  avatar: ChatAvatarSettings;
  quickLaunch: Array<{ name: string; exe_name: string; path: string }>;
  performance: { whisper: "auto" | "cpu" | "cuda"; funasr: "auto" | "cpu" | "cuda"; rag: "auto" | "cpu" | "cuda" };
  sound: { ttsVolume: number; sfxVolume: number; silentMode: boolean; engine: string; speed: number; defaultMood: string; gptSovitsVersion: string; gptSovitsPath: string; refWavOverride: string };
};
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
    share_status?: string;
    comment_receipt?: { text: string; rpid: string };
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
    autoReplyEnabled: boolean;
    mentionReplyEnabled: boolean;
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
export type SocialChatChannel = "qq" | "wechat";
export type SocialChatValue = string | number | boolean;
export type SocialChatSettings = { timing: Record<string, SocialChatValue>; bridge: Record<string, SocialChatValue> };
export type SocialChatStatus = { running: boolean; connected: boolean; state: string; url?: string; error?: string; disconnectReason?: string; fastReplyEnabled?: boolean; listenPort?: number };
export type SocialChatState = { channel: SocialChatChannel; settings: SocialChatSettings; defaults: SocialChatSettings; revision: string; status: SocialChatStatus };
export type LegacyFeature =
  | "ripple" | "persona" | "memory-constellation" | "prism-memory"
  | "history" | "note" | "proactive" | "alarm" | "reminder"
  | "settings" | "api" | "network" | "capability" | "vision" | "voice-stt" | "sound"
  | "qq" | "wechat" | "study-room" | "time-capsule" | "data-tide" | "camera" | "galgame" | "live2d"
  | "video-call";

export type VisionState = {
  phase: "idle" | "loading" | "running" | "error";
  running: boolean;
  loading: boolean;
  error: string;
  features: { face: boolean; gesture: boolean; companion: boolean };
  device: string;
  cooldown: number;
  camera: string;
  fps: number;
  provider: string;
  face: string;
  faceCount: number;
  gesture: string;
  gestureConfidence: number;
  companion: string;
  videoDuration: number;
  todayPresence: number;
  frameAt: number;
  tracking: {
    active: boolean;
    simulated: boolean;
    message: string;
    status: string;
    frameAt: number;
  };
};

export type VisionEvent = {
  id: number;
  type: string;
  message: string;
  timestamp: number;
};

export type HistorySession = {
  id: number;
  title: string;
  created_at: string;
  summary?: string;
  is_pinned?: number;
  message_count: number;
};
export type ProactiveBehavior = "normal" | "observe" | "bilibili" | "slack";
export type ProactiveSettings = Record<string, unknown> & {
  weights: number[];
  behavior_weights: Record<ProactiveBehavior, number>;
  behavior_cooldowns: Record<ProactiveBehavior, number>;
};
export type ProactivePanelState = {
  settings: ProactiveSettings; revision: string; defaultWeights: number[]; baseRate: number;
  hour: number; recentBehaviors: string[]; lastSuccess: string;
};
export type ProactiveStatus = { ready: boolean; running: boolean; lastResult?: string; lastResultText?: string; lastFireTime?: number; runCount?: number; successCount?: number; failCount?: number };
export type BilibiliHistoryRecord = { id: string; date: string; keyword: string; results: Array<{ bvid: string; title: string; author: string; play_count: number; user_reaction?: string }> };

export type HistoryDetail = {
  session: HistorySession;
  messages: Array<{ role: string; content: string; timestamp: string }>;
  character_count: number;
};

export type HistoryJob = {
  id: string;
  kind: "summary" | "batch-summary" | "diary";
  status: "running" | "completed" | "partial" | "failed";
  total: number;
  processed: number;
  succeeded: number;
  errors: Array<{ id: number | null; message: string }>;
  date: string;
};

const API_ROOT = "http://127.0.0.1:8766/api";

export class ApiRequestError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiRequestError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, { ...init, headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) } });
  if (!response.ok) throw new ApiRequestError(response.status, (await response.text()) || `API ${response.status}`);
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

export type DataTideState = {
  metrics?: {
    today_seconds?: number;
    seconds?: number;
  };
  user_name?: string;
};
export type ApiConfigSection = "deepseek" | "agnes" | "qq_bridge" | "siliconflow" | "image_gen" | "video_gen" | "qweather";
export type ApiConfigValues = Record<string, string | number | boolean>;
export type ApiChatProfile = { name: string; base_url: string; model: string; api_format: string; max_tokens: number; api_key: string; api_key_set: boolean };
export type ApiConfigState = {
  sections: Record<ApiConfigSection, ApiConfigValues>;
  profiles: ApiChatProfile[];
  activeProfile: string;
  revision: string;
  warnings?: string[];
};
export type ApiConfigDraft = { revision: string; sections?: Partial<Record<ApiConfigSection, ApiConfigValues>>; clearSecrets?: string[]; sourceProfile?: string };
export type ApiConfigTestResult = { message: string; reply?: string; path?: string };

export const lianxinApi = {
  apiConfig: () => request<ApiConfigState>("/api-config/state"),
  saveApiConfig: (action: "save" | "profile-save" | "profile-apply" | "profile-delete", payload: ApiConfigDraft & { name?: string }) => request<ApiConfigState>(`/api-config/${action}`, { method: "POST", body: JSON.stringify(payload) }),
  revealApiKey: (payload: { revision: string; section: ApiConfigSection; field: string; sourceProfile?: string }) => request<{ value: string }>("/api-config/reveal", { method: "POST", body: JSON.stringify(payload) }),
  apiConfigModels: (payload: ApiConfigDraft) => request<{ models: string[] }>("/api-config/models", { method: "POST", body: JSON.stringify(payload) }),
  testApiConfig: (payload: ApiConfigDraft & { kind: "connection" | "image" | "video"; confirmed?: boolean }) => request<ApiConfigTestResult>("/api-config/test", { method: "POST", body: JSON.stringify(payload) }),
  historySessions: (query = "", date = "") => request<{ items: HistorySession[] }>(`/history/sessions?q=${encodeURIComponent(query)}&date=${encodeURIComponent(date)}`),
  historyDetail: (id: number) => request<HistoryDetail>(`/history/detail?id=${id}`),
  historyUpdate: (payload: { id: number; title?: string; togglePin?: boolean }) => request<HistorySession>("/history/update", { method: "POST", body: JSON.stringify(payload) }),
  historyDelete: (ids: number[]) => request<{ deleted: number[]; activeSessionId: number }>("/history/delete", { method: "POST", body: JSON.stringify({ ids, confirmed: true }) }),
  historyImport: (id: number, targetSessionId: number) => request<{ imported: number; activeSessionId: number }>("/history/import", { method: "POST", body: JSON.stringify({ id, targetSessionId, confirmed: true }) }),
  historyExport: (payload: { id?: number; format: "txt" | "json"; all?: boolean }) => request<{ saved: boolean; cancelled: boolean; path?: string }>("/history/export-local", { method: "POST", body: JSON.stringify(payload) }),
  historyDiary: (date: string) => request<{ exists: boolean }>(`/history/diary?date=${encodeURIComponent(date)}`),
  historyJob: () => request<{ job: HistoryJob | null }>("/history/job"),
  historyStartJob: (payload: { kind: HistoryJob["kind"]; id?: number; ids?: number[]; date?: string; overwrite?: boolean }) => request<{ job: HistoryJob }>("/history/job", { method: "POST", body: JSON.stringify({ ...payload, confirmed: true }) }),
  executionNotices: (after: number, sessionId?: number) => request<{ items: ExecutionNotice[]; latest: number }>(`/execution-status/events?after=${after}${sessionId ? `&sessionId=${sessionId}` : ""}`),
  status: () => request<{ online: boolean; sessionId: number; busy: boolean }>("/app/status"),
  dataTideState: () => request<DataTideState>("/data-tide/state"),
  cancelChat: () => request<{ cancelled: boolean }>("/chat/cancel", { method: "POST", body: "{}" }),
  sessions: (keyword = "") => request<{ items: Array<{ id: number; title?: string; updated_at?: string; summary?: string; is_pinned?: number }> }>(`/conversations${keyword ? `?q=${encodeURIComponent(keyword)}` : ""}`),
  messages: (id: number, after = 0) => request<{ items: ApiMessage[] }>(`/conversations/${id}/messages${after ? `?after=${after}` : ""}`),
  newSession: () => request<{ id: number }>("/conversations", { method: "POST", body: "{}" }),
  selectSession: (id: number) => request<{ id: number }>(`/conversations/${id}/select`, { method: "POST", body: "{}" }),
  deleteSession: (id: number) => request<{ deleted: number }>(`/conversations/${id}`, { method: "DELETE" }),
  updateSession: (id: number, payload: { title?: string; togglePin?: boolean }) => request(`/conversations/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  note: () => request<{ content: string }>("/note"),
  saveNote: (content: string) => request<{ content: string }>("/note", { method: "POST", body: JSON.stringify({ content }) }),
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
  runtimeStart: (goal: string, options: { sessionId?: number; turn?: number; cwd?: string; sandbox?: string; networkAccess?: boolean; wait?: boolean; resumeThreadId?: string; goalContract?: ExecutionGoalContract } = {}) =>
    request<{ ok: boolean; error?: string; code?: string; goalMode?: ExecutionGoalMode; state?: ExecutionTaskState }>("/runtime/start", { method: "POST", body: JSON.stringify({ goal, ...options }) }),
  runtimeSteer: (text: string, scope: ExecutionScope) =>
    request<{ ok: boolean; status?: string; error?: string }>("/runtime/steer", { method: "POST", body: JSON.stringify({ text, ...scope }) }),
  runtimeContinue: (scope: ExecutionScope, mode: "resume" | "revise" | "retry", text = "") =>
    request<{ ok: boolean; error?: string; state?: ExecutionTaskState }>("/runtime/continue", { method: "POST", body: JSON.stringify({ ...scope, mode, text }) }),
  runtimeStop: (scope: ExecutionScope, reason = "user") => request<{ ok: boolean; error?: string; stopped: boolean; state?: ExecutionTaskState }>("/runtime/stop", { method: "POST", body: JSON.stringify({ reason, ...scope }) }),
  runtimeApprove: (scope: ExecutionScope, requestId: string, decision: "accept" | "acceptForSession" | "decline" | "cancel" = "accept", remember = false) =>
    request<{ ok: boolean; decision?: string; error?: string }>("/runtime/approve", { method: "POST", body: JSON.stringify({ requestId, decision, remember, ...scope }) }),
  voiceEvents: (after: number) => request<{ items: Array<{ id: number; type: string; state?: string; content?: string; error?: string }>; latest: number }>(`/voice/events?after=${after}`),
  startVoiceInput: () => request<{ active: boolean }>("/voice/input/start", { method: "POST", body: "{}" }),
  stopVoiceInput: () => request<{ active: boolean }>("/voice/input/stop", { method: "POST", body: "{}" }),
  proactiveState: () => request<{ desktopEnabled: boolean; qqEnabled: boolean; frequency: number; minIntervalMinutes: number }>("/proactive/state"),
  toggleProactive: (enabled: boolean) => request<{ desktopEnabled: boolean }>("/proactive/toggle", { method: "POST", body: JSON.stringify({ enabled }) }),
  proactiveStatus: () => request<ProactiveStatus>("/proactive/status"),
  proactiveTrigger: (mode: string, action = "") => request<{ ok: boolean; queued?: boolean; started?: boolean; error?: string }>("/proactive/trigger", { method: "POST", body: JSON.stringify({ mode, action }) }),
  proactivePanel: () => request<ProactivePanelState>("/proactive/panel/state"),
  saveProactivePanel: (revision: string, settings: ProactiveSettings) => request<ProactivePanelState>("/proactive/panel/save", { method: "POST", body: JSON.stringify({ revision, settings }) }),
  validateProactivePanel: (revision: string, settings: ProactiveSettings) => request<{ valid: boolean }>("/proactive/panel/validate", { method: "POST", body: JSON.stringify({ revision, settings }) }),
  proactiveCameras: () => request<{ items: Array<{ index: number; label: string }> }>("/proactive/panel/cameras", { method: "POST", body: JSON.stringify({ confirmed: true }) }),
  bilibiliHistory: (keyword = "") => request<{ items: BilibiliHistoryRecord[] }>("/proactive/panel/history", { method: "POST", body: JSON.stringify({ keyword }) }),
  deleteBilibiliTag: (keyword: string) => request<{ ok: boolean }>("/proactive/panel/tag-delete", { method: "POST", body: JSON.stringify({ keyword }) }),
  clearBilibiliHistory: () => request<{ items: BilibiliHistoryRecord[] }>("/proactive/panel/history-clear", { method: "POST", body: JSON.stringify({ confirmed: true }) }),
  reactBilibiliHistory: (recordId: string, bvid: string, reaction: "liked" | "disliked") => request<{ items: BilibiliHistoryRecord[] }>("/proactive/panel/history-react", { method: "POST", body: JSON.stringify({ recordId, bvid, reaction }) }),
  checkBilibiliAuthorization: () => request<{ ok: boolean; error?: string; state?: BilibiliInteractionState }>("/bilibili-interaction/account/check", { method: "POST", body: "{}" }),
  bilibiliInteractionState: () => request<BilibiliInteractionState>("/bilibili-interaction/state"),
  bilibiliSurfState: () => request<BilibiliSurfState>("/bilibili-surf/state"),
  openBilibiliVideo: (bvid: string) => request<{ ok: boolean; url?: string; error?: string }>("/bilibili-surf/open-video", { method: "POST", body: JSON.stringify({ bvid }) }),
  saveBilibiliSurfTag: (payload: { keyword: string; search_query?: string; intent_note?: string; status?: "active" | "paused" }) => request<BilibiliSurfState>("/bilibili-surf/tag", { method: "POST", body: JSON.stringify(payload) }),
  recheckBilibiliComment: (rpid = "") => request<{ ok: boolean; message?: string; error?: string; diagnostic?: string; state?: BilibiliInteractionState }>("/bilibili-interaction/comment/recheck", { method: "POST", body: JSON.stringify({ rpid }) }),
  saveBilibiliInteractionSettings: (payload: { mode?: "read_only" | "test"; userUid?: string; testVideo?: string; allowOtherReplies?: boolean; autoCommentEnabled?: boolean; autoReplyEnabled?: boolean; mentionReplyEnabled?: boolean; paused?: boolean }) => request<{ ok: boolean; error?: string; state?: BilibiliInteractionState }>("/bilibili-interaction/settings", { method: "POST", body: JSON.stringify(payload) }),
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
  personaGrowth: (id: string) => request<GrowthState>(`/persona/growth?id=${encodeURIComponent(id)}`),
  reviewGrowth: (personaId: string, eventId: number, action: "apply" | "revert" | "dismiss") => request<GrowthState>("/persona/growth/review", { method: "POST", body: JSON.stringify({ persona_id: personaId, event_id: eventId, action }) }),
  growthSettings: (personaId: string, values: Record<string, unknown>) => request<GrowthState>("/persona/growth/settings", { method: "POST", body: JSON.stringify({ persona_id: personaId, ...values }) }),
  personaSave: (profile: Record<string, unknown>) => request<PersonaState>("/persona/save", { method: "POST", body: JSON.stringify(profile) }),
  personaPreview: (profile: Record<string, unknown>) => request<{ profile: Record<string, any>; preview: PersonaPreview }>("/persona/preview", { method: "POST", body: JSON.stringify(profile) }),
  personaCreate: (profileName: string, template = "") => request<PersonaState>("/persona/create", { method: "POST", body: JSON.stringify({ profile_name: profileName, template }) }),
  personaSaveAndActivate: (profile: Record<string, unknown>) => request<PersonaState>("/persona/save-and-activate", { method: "POST", body: JSON.stringify(profile) }),
  personaActivate: (id: string, enabled = true) => request<PersonaState>("/persona/activate", { method: "POST", body: JSON.stringify({ id, enabled }) }),
  personaToggle: (enabled: boolean) => request<PersonaState>("/persona/toggle", { method: "POST", body: JSON.stringify({ enabled }) }),
  personaDelete: (id: string) => request<PersonaState>("/persona/delete", { method: "POST", body: JSON.stringify({ id }) }),
  fiveAxis: () => request<{ axes: Record<string, number>; mood: string }>("/emotion/five-axis"),
  background: (include = true) => request<BackgroundState>(`/settings/background?include=${include ? "1" : "0"}`),
  avatars: (include = true) => request<AvatarState>(`/settings/avatars?include=${include ? "1" : "0"}`),
  settingsPanels: () => request<SettingsPanelState>("/settings/panels"),
  decisionSettings: () => request<DecisionSettings>("/decision/settings"),
  decisionRuntime: () => request<DecisionRuntime>("/decision/runtime"),
  saveDecisionSettings: (payload: Partial<DecisionSettings> & { api_key?: string; clear_api_key?: boolean }) =>
    request<DecisionSettings>("/decision/settings", { method: "POST", body: JSON.stringify(payload) }),
  testDecision: () => request<{ ok: boolean; model?: string; provider?: string; summary?: string; error?: string }>(
    "/decision/test", { method: "POST", body: "{}" }),
  decisionLocal: (action: "start" | "stop") => request<DecisionSettings["local"]>(
    "/decision/local", { method: "POST", body: JSON.stringify({ action }) }),
  saveSettingsPanels: (payload: Partial<{ global: Partial<SettingsPanelState["global"]>; avatar: Partial<SettingsPanelState["avatar"]>; performance: Partial<SettingsPanelState["performance"]>; quickLaunch: SettingsPanelState["quickLaunch"]; sound: Partial<SettingsPanelState["sound"]>; preview: boolean }>) => request<SettingsPanelState>("/settings/panels", { method: "POST", body: JSON.stringify(payload) }),
  saveAvatarImage: (role: "assistant" | "user" | "character", data: string, sourcePath?: string) => request<AvatarState>("/settings/avatar-image", { method: "POST", body: JSON.stringify({ role, data, sourcePath }) }),
  avatarAction: (action: "tap" | "headpat") => request<{ action: string; accepted?: boolean; sound?: boolean; response?: string; counterAction?: string; message?: string }>(`/avatar/action?action=${action}`),
  openLegacy: (feature: LegacyFeature) => request<{ feature: string; pid: number; mode: string }>("/legacy/open", { method: "POST", body: JSON.stringify({ feature }) }),
  visionState: () => request<VisionState>("/vision/state"),
  visionStart: () => request<VisionState>("/vision/start", { method: "POST", body: "{}" }),
  visionStop: () => request<VisionState>("/vision/stop", { method: "POST", body: "{}" }),
  visionFeature: (name: "face" | "gesture" | "companion", enabled: boolean) => request<VisionState>("/vision/features", { method: "POST", body: JSON.stringify({ name, enabled }) }),
  visionDevice: (device: string) => request<VisionState>("/vision/device", { method: "POST", body: JSON.stringify({ device }) }),
  visionCooldown: (seconds: number) => request<VisionState>("/vision/cooldown", { method: "POST", body: JSON.stringify({ seconds }) }),
  visionEnroll: (name: string) => request<{ ok: boolean; name: string }>("/vision/enroll", { method: "POST", body: JSON.stringify({ name }) }),
  visionEvents: (after: number) => request<{ items: VisionEvent[]; latest: number }>(`/vision/events?after=${after}`),
  visionTracking: (mode: "esp32" | "simulate" | "off") => request<VisionState>("/vision/tracking", { method: "POST", body: JSON.stringify({ mode }) }),
  socialChatState: (channel: SocialChatChannel) => request<SocialChatState>(`/social-chat/${channel}/state`),
  saveSocialChat: (channel: SocialChatChannel, payload: { revision: string; settings: SocialChatSettings }) => request<SocialChatState>(`/social-chat/${channel}/save`, { method: "POST", body: JSON.stringify(payload) }),
  socialChatAction: (channel: SocialChatChannel, action: "auto-start" | "fast-reply" | "connect" | "disconnect" | "retry", payload: Record<string, unknown> = {}) => request<Record<string, unknown>>(`/social-chat/${channel}/${action}`, { method: "POST", body: JSON.stringify(payload) }),
  qqStatus: () => request<{ running: boolean; connected: boolean; state: string; url: string; error?: string; disconnectReason?: string; fastReplyEnabled: boolean }>("/qq/status"),
  startQq: () => request<{ running: boolean; connected: boolean; state: string; url: string }>("/qq/start", { method: "POST", body: "{}" }),
  stopQq: () => request<{ running: boolean; connected: boolean; state: string; url: string }>("/qq/stop", { method: "POST", body: "{}" }),
  reloadQq: () => request<{ running: boolean; connected: boolean; state: string; url: string }>("/qq/reload", { method: "POST", body: "{}" }),
  setQqFastReply: (enabled: boolean) => request<{ running: boolean; connected: boolean; state: string; fastReplyEnabled: boolean }>("/qq/fast-reply", { method: "POST", body: JSON.stringify({ enabled }) }),
  capabilities: () => request<{ items: Array<{ name: string; display_name: string; description: string; category: string; enabled: boolean; available: boolean; favorite: boolean }> }>("/capabilities"),
  capabilityPanel: () => request<CapabilityPanelState>("/capability-panel/state"),
  nativeConfig: (domain: ConfigPanelDomain) => request<NativeConfigState>(`/config-panels/${domain}/state`),
  nativeConfigAction: (domain: ConfigPanelDomain, action: string, payload: Record<string, unknown> = {}) => request<NativeConfigState>(`/config-panels/${domain}/${action}`, { method: "POST", body: JSON.stringify(payload) }),
  nativeConfigRead: <T,>(domain: ConfigPanelDomain, action: string) => request<T>(`/config-panels/${domain}/${action}`),
  revealNativeConfig: (domain: ConfigPanelDomain, field: string, revision: string) => request<{ value: string }>(`/config-panels/${domain}/reveal`, { method: "POST", body: JSON.stringify({ field, revision }) }),
  capabilityAction: (action: "toggle" | "favorite" | "refresh" | "usage-reset" | "mcp-test" | "install" | "uninstall" | "logs-clear" | "save", payload: Record<string, unknown> = {}) => request<CapabilityPanelState>(`/capability-panel/${action}`, { method: "POST", body: JSON.stringify(payload) }),
  capabilityUsage: (days: number | null) => request<CapabilityUsageState>("/capability-panel/usage", { method: "POST", body: JSON.stringify({ days }) }),
  inspectCapabilityPlugin: (source: string) => request<CapabilityPluginInfo>("/capability-panel/inspect", { method: "POST", body: JSON.stringify({ source }) }),
  capabilityBrowserLog: () => request<{ items: Array<Record<string, string>> }>("/capability-panel/browser-log"),
  speak: (text: string, generation?: number) => request<{ speaking: boolean }>("/tts/speak", { method: "POST", body: JSON.stringify({ text, generation }) }),
  prepareSpeech: (text: string, generation: number) => request<{ prepared: boolean; id?: string; unavailable?: boolean; muted?: boolean; cancelled?: boolean }>("/tts/prepare", { method: "POST", body: JSON.stringify({ text, generation }) }),
  playPreparedSpeech: (id: string) => request<{ played: boolean; cancelled?: boolean }>("/tts/play-prepared", { method: "POST", body: JSON.stringify({ id }) }),
  stopSpeaking: () => request<{ speaking: boolean; generation: number }>("/tts/stop", { method: "POST", body: "{}" }),
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
  streamChat: async function* (message: string, attachments: Array<{ kind: "image" | "file"; fileName: string; dataUrl: string }> = [], selection: { forcedTool?: string; preferredTool?: string } = {}, quote?: { role: "user" | "assistant"; content: string }, requestMetadata?: ChatRequestMetadata) {
    const metadata = requestMetadata?.force_memory_activation === true ? { force_memory_activation: true } : undefined;
    const response = await fetch(`${API_ROOT}/chat/stream`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message, attachments, ...selection, quote, requestMetadata: metadata }) });
    if (!response.ok || !response.body) throw new Error(`API ${response.status}`);
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
    try {
      while (true) { const part = await reader.read(); if (part.done) break; buffer += decoder.decode(part.value, { stream: true }); const chunks = buffer.split("\n\n"); buffer = chunks.pop() ?? ""; for (const chunk of chunks) { const line = chunk.split("\n").find((item) => item.startsWith("data: ")); if (line) yield JSON.parse(line.slice(6)) as ChatStreamEvent; } }
    } finally {
      await reader.cancel().catch(() => undefined);
      reader.releaseLock();
    }
  },
};
