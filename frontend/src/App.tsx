import { useEffect, useMemo, useState, type MouseEvent, type ReactNode } from "react";
import {
  Activity, AlarmClock, ArrowUp, BookOpen, Bot, BrainCircuit, Check,
  CheckCircle2, ChevronDown, ChevronRight, Clock3, Command, FileText, FolderOpen,
  Gauge, Gem, Image, LayoutDashboard, Lightbulb, Maximize2, Menu,
  MessageCircle, Mic, Minimize2, Music2, Network, Pause, Play, Plus,
  Search, Send, Settings, Sparkles, UserRound, Volume2, Waves, Wrench, X,
} from "lucide-react";
import { managementGroups, primaryNavigation, demoMessages } from "./data/demo";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { lianxinApi, type AvatarState, type BackgroundState, type ChatStreamEvent, type LegacyFeature } from "./services/lianxinApi";
import type { Message, WorkspaceId } from "./types/ui";

type PanelId = "none" | "history" | "note" | "tasks" | "voice" | "proactive" | "management";
type Session = { id: number; title?: string; updated_at?: string; summary?: string; is_pinned?: number };
type MusicState = { active?: boolean; name?: string; artist?: string; album?: string; progress?: number; duration?: number };
type FiveAxisState = { axes: Record<string, number>; mood: string };
type ToolCallState = { id: string; name: string; args: string; status: "running" | "done" | "error"; preview?: string; elapsedMs?: number };
type ToolRoundState = { round: number; calls: ToolCallState[] };

const desktopWindow = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window ? getCurrentWindow() : null;

function MessageAvatar({ role, compact = false, avatar }: { role: "user" | "assistant"; compact?: boolean; avatar?: AvatarState }) {
  const sizeClass = compact ? "avatar-small" : "avatar-chat";
  const configuredSize = avatar ? Math.max(30, Math.min(72, Math.round(avatar.size * (compact ? 0.63 : 0.63)))) : undefined;
  const avatarStyle = configuredSize ? { width: `${configuredSize}px`, height: `${configuredSize}px`, border: avatar?.border === false ? "0" : undefined } : undefined;
  if (role === "assistant") {
    return <div className={`avatar ${sizeClass} assistant-avatar`} style={avatarStyle}><img src={avatar?.assistantDataUrl || "/lianxin-avatar.png"} alt="莲心" /></div>;
  }
  return <div className={`avatar ${sizeClass} user-avatar`} style={avatarStyle}>{avatar?.userDataUrl ? <img src={avatar.userDataUrl} alt="我" /> : <span>我</span>}</div>;
}

const managementFeatureMap: Record<string, LegacyFeature> = {
  "历史记录": "history", "备忘本": "note", "闹钟与提醒": "alarm", "任务运行中心": "workflow",
  "后台职责中心": "duty", "主动聊天": "proactive", "涟漪情感系统": "ripple", "人格枢控": "persona",
  "星图系统": "memory-constellation", "棱镜记忆系统": "prism-memory", "莲心自习室": "study-room",
  "时间胶囊": "time-capsule", "数据潮汐": "data-tide", "能力中枢": "capability", "视觉理解": "vision",
  "语音转录": "voice-stt", "声音设置": "sound", "全局设置": "settings", "API Key": "api",
  "网络设置": "network", "QQ 聊天": "qq", "微信聊天": "wechat",
};
const legacyFeatureIds = new Set(Object.values(managementFeatureMap));

const iconMap: Record<string, typeof MessageCircle> = {
  message: MessageCircle, music: Music2, clock: Clock3, diamond: Gem, book: BookOpen, world: Network,
};
const managementIconMap: Record<string, typeof Settings> = {
  "闹钟与提醒": AlarmClock, "任务运行中心": Gauge, "主动聊天": MessageCircle,
  "涟漪情感系统": Waves, "人格枢控": UserRound, "星图系统": Network,
  "能力中枢": BrainCircuit, "视觉理解": Image, "语音转录": Mic,
  "声音设置": Volume2, "全局设置": Settings, "API Key": Command,
  "网络设置": Network, "QQ 聊天": MessageCircle, "微信聊天": MessageCircle,
  "数据潮汐": Activity,
};

function Icon({ name }: { name: string }) {
  const Component = iconMap[name] ?? Sparkles;
  return <Component size={19} strokeWidth={1.8} aria-hidden="true" />;
}

function TopBar({ onNewChat, onPanel, onLegacy, onMinimize, onMaximize, onClose, onDrag }: { onNewChat: () => void; onPanel: (panel: PanelId) => void; onLegacy: (feature: LegacyFeature) => void; onMinimize: () => void; onMaximize: () => void; onClose: () => void; onDrag: (event: React.MouseEvent<HTMLElement>) => void }) {
  return <header className="topbar" data-tauri-drag-region onMouseDown={onDrag}>
    <div className="brand-block" data-tauri-drag-region><div className="brand-mark"><Sparkles size={24} /></div><div><div className="brand-name">莲心 <span>AI</span></div><div className="brand-status"><span className="status-dot" /> 在线</div></div></div>
    <div className="search-box" role="search"><Search size={17} /><span>搜索对话、记忆和功能</span><kbd>Ctrl K</kbd></div>
    <nav className="top-actions"><button className="top-action" onClick={() => onLegacy("history")}><Clock3 size={16} />历史记录</button><button className="top-action" onClick={onNewChat}><Plus size={17} />新建对话</button><button className="top-action" onClick={() => onLegacy("note")}><FileText size={16} />备忘本</button><button className="mode-button" onClick={() => onLegacy("galgame")}><Sparkles size={16} />Galgame 模式</button><button className="icon-button" title="全局设置" onClick={() => onPanel("management")}><Settings size={18} /></button></nav>
    <div className="window-actions"><button className="icon-button" title="最小化" onClick={onMinimize}><Minimize2 size={16} /></button><button className="icon-button" title="最大化 / 还原" onClick={onMaximize}><Maximize2 size={16} /></button><button className="icon-button close-button" title="关闭莲心" onClick={onClose}><X size={17} /></button></div>
  </header>;
}

function SideNavigation({ activeWorkspace, onWorkspaceChange, managementOpen, onManagementToggle, onPanel, onLegacy, avatar }: { activeWorkspace: WorkspaceId; onWorkspaceChange: (workspace: WorkspaceId) => void; managementOpen: boolean; onManagementToggle: () => void; onPanel: (panel: PanelId) => void; onLegacy: (feature: LegacyFeature) => void; avatar?: AvatarState }) {
  return <aside className="side-nav"><div className="nav-scroll"><div className="nav-section-label">莲心空间</div>{primaryNavigation.map((item) => <button className={`nav-item ${item.id === activeWorkspace ? "is-selected" : ""}`} key={item.id} onClick={() => onWorkspaceChange(item.id)}><Icon name={item.icon} /><span>{item.label}</span>{item.id === activeWorkspace && <span className="nav-indicator" />}</button>)}<div className="nav-divider" /><button className={`nav-item management-trigger ${managementOpen ? "is-open" : ""}`} onClick={onManagementToggle}><Settings size={19} /><span>管理中心</span>{managementOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</button>{managementOpen && <div className="management-menu">{managementGroups.map((group) => <div className="management-group" key={group.label}><div className="management-label">{group.label}</div>{group.items.map((item) => { const ItemIcon = managementIconMap[item] ?? Settings; const feature = managementFeatureMap[item]; const action = feature ? () => onLegacy(feature) : item === "语音聊天" ? () => onPanel("voice") : undefined; return <button className="management-item" key={item} onClick={action}><ItemIcon size={15} />{item}</button>; })}</div>)}</div>}</div><div className="profile-strip"><MessageAvatar role="assistant" compact avatar={avatar} /><div className="profile-copy"><strong>莲心</strong><span><span className="status-dot" />在线 · 你在我心里</span></div><Activity size={17} className="profile-activity" /></div></aside>;
}

function TaskCard() { return <div className="task-card"><div className="task-card-header"><span><LayoutDashboard size={15} />正在处理当前请求</span><span className="task-running"><Activity size={14} />运行中</span></div><div className="task-step is-done"><span className="step-icon"><Check size={12} /></span>分析用户问题</div><div className="task-step is-done"><span className="step-icon"><Check size={12} /></span>整理当前上下文</div><div className="task-step is-active"><span className="step-icon"><span /></span>生成回复</div></div>; }

function MessageItem({ message, avatar }: { message: Message; avatar?: AvatarState }) {
  if (message.kind === "task") return <div className="message-row assistant-row"><MessageAvatar role="assistant" avatar={avatar} /><div className="message-column"><div className="message-meta"><strong>莲心</strong><span>{message.time}</span></div><TaskCard /></div></div>;
  return <div className={`message-row ${message.role === "user" ? "user-row" : "assistant-row"}`}><MessageAvatar role={message.role} avatar={avatar} /><div className="message-column"><div className="message-meta"><strong>{message.role === "user" ? "你" : "莲心"}</strong><span>{message.time}</span></div><div className={`message-bubble ${message.role === "user" ? "user-bubble" : ""}`}>{message.content.split("\n").map((line, index) => <p key={`${message.id}-${index}`}>{line}</p>)}</div></div></div>;
}

function ToolRounds({ rounds }: { rounds: ToolRoundState[] }) {
  if (!rounds.length) return null;
  return <div className="tool-rounds">{rounds.map((round) => <section className="tool-round" key={round.round}><div className="tool-round-heading"><span><Wrench size={14} />莲心执行工具 · 第 {round.round} 轮</span><span>{round.calls.every((call) => call.status !== "running") ? "已完成" : "执行中"}</span></div>{round.calls.map((call) => <div className={`tool-call ${call.status}`} key={call.id}><span className="tool-call-icon">{call.status === "running" ? <span className="tool-spinner" /> : call.status === "error" ? <X size={13} /> : <CheckCircle2 size={14} />}</span><div className="tool-call-copy"><strong>{call.name}</strong><span>{call.status === "running" ? "正在执行…" : call.status === "error" ? "执行失败" : `已完成${call.elapsedMs ? ` · ${(call.elapsedMs / 1000).toFixed(1)}s` : ""}`}</span>{call.preview && <p>{call.preview}</p>}</div></div>)}</section>)}</div>;
}

function ChatWorkspace({ messages, toolRounds, avatar, onSend, busy, voiceActive, onVoice }: { messages: Message[]; toolRounds: ToolRoundState[]; avatar?: AvatarState; onSend: (text: string) => void; busy: boolean; voiceActive: boolean; onVoice: () => void }) {
  const [draft, setDraft] = useState("");
  const submit = () => { const text = draft.trim(); if (!text || busy) return; onSend(text); setDraft(""); };
  return <section className="workspace chat-workspace"><div className="conversation-header"><div><span className="conversation-live" /><strong>默认对话</strong><span className="edit-title">✎</span></div><span className="conversation-meta">Python 核心 · {busy ? "正在回复" : "已连接"}</span></div><div className="message-viewport"><div className="message-list">{messages.length ? messages.map((message) => <MessageItem key={message.id} message={message} avatar={avatar} />) : <div className="empty-state"><Sparkles size={28} /><h3>开始和莲心聊天</h3><p>你的新会话会自动保存到历史记录。</p></div>}<ToolRounds rounds={toolRounds} />{busy && <div className="typing-indicator"><span /><span /><span />莲心正在思考…</div>}</div></div><div className="composer-wrap"><div className="composer"><textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); submit(); } }} placeholder="和莲心聊点什么吧…" rows={2} /><div className="composer-toolbar"><div className="composer-tools"><button className="composer-tool"><Image size={16} />图片</button><button className="composer-tool"><FolderOpen size={16} />文件</button><button className="composer-tool"><Command size={16} />引用</button><button className="composer-tool"><FileText size={16} />备忘本</button></div><div className="composer-actions"><button className={`icon-button voice-button ${voiceActive ? "voice-active" : ""}`} title="语音聊天" onClick={onVoice}><Mic size={18} /></button><button className="send-button" onClick={submit} disabled={!draft.trim() || busy} title="发送"><Send size={18} /></button></div></div></div></div></section>;
}

function StatusCard({ online, avatar }: { online: boolean; avatar?: AvatarState }) { return <section className="rail-card presence-card"><div className="rail-card-title"><span><Sparkles size={15} />莲心状态</span><ChevronDown size={15} /></div><div className="presence-body"><MessageAvatar role="assistant" compact avatar={avatar} /><div><strong>{online ? "陪伴中" : "离线模式"}</strong><span>{online ? "在线 · Python 核心已连接" : "启动 api_server.py 后接入"}</span></div><Activity size={20} className="presence-wave" /></div></section>; }

function NowPlayingCard({ music, onControl }: { music: MusicState; onControl: (action: string) => void }) { const progress = music.duration ? Math.min(100, ((music.progress ?? 0) / music.duration) * 100) : 0; return <section className="rail-card"><div className="rail-card-title"><span><Music2 size={15} />当前播放</span><ChevronDown size={15} /></div><div className="playing"><div className="album-art">{music.active ? <Play size={25} /> : <Music2 size={25} />}</div><div className="playing-copy"><strong>{music.active ? music.name || "未知歌曲" : "暂无播放"}</strong><span>{music.artist || "网易云音乐"}</span><div className="progress-line"><span style={{ width: `${progress}%` }} /></div></div></div><div className="player-controls"><button title="上一首" onClick={() => onControl("previous")}><ChevronRight size={16} className="previous-icon" /></button><button className="play-button" title={music.active ? "暂停" : "播放"} onClick={() => onControl("toggle")}>{music.active ? <Pause size={17} /> : <Play size={17} />}</button><button title="下一首" onClick={() => onControl("next")}><ChevronRight size={16} /></button></div></section>; }

function FiveAxisCard({ state }: { state: FiveAxisState }) { const items = [{ key: "connection", label: "连接需求", color: "#F3C878", signed: false }, { key: "pride", label: "骄傲", color: "#A992FF", signed: true }, { key: "valence", label: "情绪基调", color: "#72D7E8", signed: true }, { key: "arousal", label: "唤醒度", color: "#F49BBE", signed: true }, { key: "immersion", label: "沉浸度", color: "#86D6A6", signed: false }]; return <section className="rail-card axis-card"><div className="rail-card-title"><span><Activity size={15} />AI 主动意识 · 五轴状态</span><strong>{state.mood || "中性"}</strong></div>{items.map((item) => { const value = Math.max(item.signed ? -1 : 0, Math.min(1, Number(state.axes[item.key] ?? 0))); const width = item.signed ? Math.abs(value) * 50 : value * 100; return <div className="axis-row" key={item.key}><span className="axis-label">{item.label}</span><div className={`axis-track ${item.signed ? "is-signed" : ""}`}><span className="axis-fill" style={{ width: `${width}%`, left: item.signed && value < 0 ? `${50 - width}%` : item.signed ? "50%" : "0", background: item.color }} />{item.signed && <i />}</div><span className="axis-value">{item.signed ? value.toFixed(2) : value.toFixed(2)}</span></div>; })}<div className="axis-motive">基于莲心当前情绪与活动状态实时更新</div></section>; }

function CompanionRail({ online, music, onControl, axis, avatar }: { online: boolean; music: MusicState; onControl: (action: string) => void; axis: FiveAxisState; avatar?: AvatarState }) { return <aside className="companion-rail"><StatusCard online={online} avatar={avatar} /><NowPlayingCard music={music} onControl={onControl} /><FiveAxisCard state={axis} /></aside>; }

function Overlay({ children, onClose }: { children: ReactNode; onClose: () => void }) { return <div className="overlay"><section className="overlay-panel"><button className="overlay-close" onClick={onClose}><X size={18} /></button>{children}</section></div>; }

function HistoryPanel({ onClose, onOpen }: { onClose: () => void; onOpen: (id: number) => void }) {
  const [items, setItems] = useState<Session[]>([]); const [query, setQuery] = useState(""); const [error, setError] = useState("");
  const load = () => lianxinApi.sessions(query).then((result) => { setItems(result.items); setError(""); }).catch((e) => setError(String(e)));
  useEffect(() => { void load(); }, [query]);
  const remove = (id: number) => void lianxinApi.deleteSession(id).then(load).catch((e) => setError(String(e)));
  const pin = (id: number) => void lianxinApi.updateSession(id, { togglePin: true }).then(load).catch((e) => setError(String(e)));
  return <Overlay onClose={onClose}><div className="panel-heading"><div><span className="eyebrow">莲心记录</span><h2>历史记录</h2></div><button className="secondary-button" onClick={() => void load()}><Activity size={15} />刷新</button></div><input className="panel-search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题、摘要或消息内容" />{error ? <div className="panel-error">无法连接历史记录：{error}</div> : <div className="session-list">{items.length ? items.map((item) => <div className="session-entry" key={item.id}><button className="session-open" onClick={() => { onOpen(item.id); onClose(); }}><div><strong>{item.is_pinned ? "📌 " : ""}{item.title || "未命名对话"}</strong><span>{item.summary || item.updated_at || ""}</span></div><ChevronRight size={16} /></button><div className="session-actions"><button title="置顶" onClick={() => pin(item.id)}>📌</button><button title="删除" onClick={() => remove(item.id)}><X size={14} /></button></div></div>) : <div className="empty-state"><Clock3 size={26} /><p>暂无历史会话</p></div>}</div>}</Overlay>;
}

function NotePanel({ onClose }: { onClose: () => void }) { const [content, setContent] = useState(""); const [saved, setSaved] = useState(false); useEffect(() => { void lianxinApi.note().then((result) => setContent(result.content)).catch(() => undefined); }, []); const save = () => void lianxinApi.saveNote(content).then(() => setSaved(true)).catch(() => setSaved(false)); return <Overlay onClose={onClose}><div className="panel-heading"><div><span className="eyebrow">莲心记录</span><h2>备忘本</h2></div><button className="secondary-button" onClick={save}><Check size={15} />{saved ? "已保存" : "保存"}</button></div><textarea className="note-editor" value={content} onChange={(event) => { setContent(event.target.value); setSaved(false); }} placeholder="写下想交给莲心记住的内容…" /></Overlay>; }

function TaskPanel({ onClose }: { onClose: () => void }) { const [data, setData] = useState<{ todos: unknown[]; liveTodos?: unknown[]; autoTasks: unknown[]; logs: unknown[]; workflowRuns?: any[]; progress: { completed: number; total: number; active: string } } | null>(null); const load = () => void lianxinApi.taskSnapshot().then(setData).catch(() => setData(null)); useEffect(() => { load(); const timer = window.setInterval(load, 2500); return () => window.clearInterval(timer); }, []); return <Overlay onClose={onClose}><div className="panel-heading"><div><span className="eyebrow">运行与任务</span><h2>任务运行中心</h2></div><button className="secondary-button" onClick={load}><Activity size={15} />刷新</button></div>{data ? <><div className="task-overview"><strong>{data.progress.completed}/{data.progress.total}</strong><span>当前会话任务完成度 · 每 2.5 秒同步</span><b>{data.progress.active || "当前没有运行中的任务"}</b></div><div className="task-data-grid"><div><span>持久任务</span><strong>{data.todos.length}</strong></div><div><span>自动任务</span><strong>{data.autoTasks.length}</strong></div><div><span>Workflow 运行</span><strong>{data.workflowRuns?.length ?? 0}</strong></div></div><h3>最近运行记录</h3><div className="log-list">{data.logs.length ? data.logs.slice(-8).reverse().map((log: any, index) => <div className="log-entry" key={index}><span className={log.success ? "log-ok" : "log-fail"}>{log.success ? "成功" : "失败"}</span><span>{log.message || "任务步骤"}</span><time>{log.timestamp || ""}</time></div>) : <p className="muted-copy">暂无任务运行记录。</p>}</div></> : <div className="panel-error">任务服务暂时不可用，请确认 Python 后端已启动。</div>}</Overlay>; }

function VoicePanel({ active, onClose, onToggle }: { active: boolean; onClose: () => void; onToggle: () => void }) { const [events, setEvents] = useState<Array<{ id: number; type: string; state?: string; content?: string; error?: string }>>([]); useEffect(() => { let cursor = 0; const poll = () => void lianxinApi.voiceEvents(cursor).then((result) => { if (result.items.length) { cursor = result.latest; setEvents((current) => [...current, ...result.items].slice(-20)); } }).catch(() => undefined); const timer = window.setInterval(poll, 800); poll(); return () => window.clearInterval(timer); }, []); return <Overlay onClose={onClose}><div className="panel-heading"><div><span className="eyebrow">能力与感知</span><h2>语音聊天</h2></div><button className={`secondary-button ${active ? "danger-action" : ""}`} onClick={onToggle}>{active ? "停止监听" : "开始监听"}</button></div><div className={`voice-state ${active ? "is-active" : ""}`}><Mic size={30} /><strong>{active ? "正在监听麦克风" : "语音聊天未启动"}</strong><span>{active ? "说话结束后会自动转录并发送给莲心" : "启动后才会访问麦克风"}</span></div><div className="voice-events">{events.length ? events.map((event) => <div className="log-entry" key={event.id}><span className="log-ok">{event.type.replace("voice.", "")}</span><span>{event.content || event.state || event.error || ""}</span></div>) : <p className="muted-copy">暂无语音事件。</p>}</div></Overlay>; }

function ProactivePanel({ onClose }: { onClose: () => void }) { const [enabled, setEnabled] = useState(false); const [loading, setLoading] = useState(true); useEffect(() => { void lianxinApi.proactiveState().then((state) => { setEnabled(state.desktopEnabled); setLoading(false); }).catch(() => setLoading(false)); }, []); const toggle = () => { const next = !enabled; setEnabled(next); void lianxinApi.toggleProactive(next).catch(() => setEnabled(!next)); }; return <Overlay onClose={onClose}><div className="panel-heading"><div><span className="eyebrow">运行与任务</span><h2>主动聊天</h2></div><button className="secondary-button" disabled={loading} onClick={toggle}>{enabled ? "已启用" : "已停用"}</button></div><div className="setting-row"><div><strong>桌面主动聊天</strong><span>沿用原有 ProactiveChatScheduler 的时间权重、冷却和活动延迟策略。</span></div><button className={`toggle ${enabled ? "is-on" : ""}`} onClick={toggle} aria-label="切换主动聊天"><span /></button></div><p className="muted-copy">默认保持停用。启用后，莲心会依照现有调度器策略主动发起聊天。</p></Overlay>; }

function ManagementPanel({ onClose, onVoice, onLegacy }: { onClose: () => void; onVoice: () => void; onLegacy: (feature: LegacyFeature) => void }) { const [modules, setModules] = useState<Array<{ id: string; label: string; available: boolean }>>([]); useEffect(() => { void lianxinApi.managementState().then((result) => setModules(result.modules)).catch(() => undefined); }, []); return <Overlay onClose={onClose}><div className="panel-heading"><div><span className="eyebrow">莲心系统</span><h2>管理中心</h2></div></div><div className="module-list">{modules.map((module) => { const legacy = legacyFeatureIds.has(module.id as LegacyFeature); return <button className="module-entry" key={`${module.id}-${module.label}`} onClick={() => module.id === "voice" ? onVoice() : legacy ? onLegacy(module.id as LegacyFeature) : undefined}><span><span className={`module-dot ${module.available ? "is-ready" : ""}`} />{module.label}</span><span>{legacy ? "原版窗口" : module.available ? "可用" : "未连接"}<ChevronRight size={15} /></span></button>; })}</div></Overlay>; }

function Workspace({ workspace, music }: { workspace: WorkspaceId; music: MusicState }) { const item = primaryNavigation.find((entry) => entry.id === workspace)!; if (workspace === "music") return <section className="workspace placeholder-workspace"><div className="placeholder-icon"><Music2 /></div><p className="eyebrow">莲心空间</p><h1>音乐</h1><p>{music.active ? `正在播放：${music.name || "未知歌曲"} · ${music.artist || "未知歌手"}` : "当前没有检测到网易云音乐播放状态。"}</p><button className="secondary-button"><Play size={15} />打开网易云音乐</button></section>; return <section className="workspace placeholder-workspace"><div className="placeholder-icon"><Icon name={item.icon} /></div><p className="eyebrow">莲心空间</p><h1>{item.label}</h1><p>这里将逐步接入莲心现有的真实能力与数据。</p><button className="secondary-button"><ArrowUp size={15} />返回对话</button></section>; }

export function App() {
  const [activeWorkspace, setActiveWorkspace] = useState<WorkspaceId>("chat"); const [managementOpen, setManagementOpen] = useState(false); const [messages, setMessages] = useState<Message[]>(demoMessages); const [backendOnline, setBackendOnline] = useState(false); const [busy, setBusy] = useState(false); const [panel, setPanel] = useState<PanelId>("none"); const [music, setMusic] = useState<MusicState>({}); const [axis, setAxis] = useState<FiveAxisState>({ axes: {}, mood: "中性" }); const [voiceActive, setVoiceActive] = useState(false); const [toolRounds, setToolRounds] = useState<ToolRoundState[]>([]); const [background, setBackground] = useState<BackgroundState>({ enabled: true, opacity: 0.22, fitMode: "cover", fingerprint: "" }); const [avatar, setAvatar] = useState<AvatarState>({ enabled: true, size: 60, gap: 10, border: true, fingerprint: "" });
  const workspaceLabel = useMemo(() => primaryNavigation.find((item) => item.id === activeWorkspace)?.label ?? "对话", [activeWorkspace]);
  useEffect(() => { const check = () => { void lianxinApi.status().then((status) => { setBackendOnline(status.online); if (status.sessionId) void lianxinApi.messages(status.sessionId).then((result) => { if (result.items.length) setMessages(result.items.map((item, index) => ({ id: String(item.id ?? index), role: item.role, content: item.content, time: item.timestamp?.slice(11, 16) || "" }))); }).catch(() => undefined); }).catch(() => setBackendOnline(false)); void lianxinApi.musicState().then((state) => setMusic(state as MusicState)).catch(() => undefined); void lianxinApi.fiveAxis().then(setAxis).catch(() => undefined); }; check(); const timer = window.setInterval(check, 5000); return () => window.clearInterval(timer); }, []);
  useEffect(() => {
    let disposed = false;
    const sync = async (includeData: boolean) => {
      try {
        const next = await lianxinApi.background(includeData);
        if (disposed) return;
        if (includeData || next.fingerprint !== background.fingerprint) {
          const full = includeData ? next : await lianxinApi.background(true);
          if (!disposed) setBackground(full);
        } else {
          setBackground((current) => ({ ...current, enabled: next.enabled, opacity: next.opacity, fitMode: next.fitMode }));
        }
      } catch { /* The bundled fallback remains visible while the API is offline. */ }
    };
    void sync(true);
    const timer = window.setInterval(() => void sync(false), 2500);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [background.fingerprint]);
  useEffect(() => {
    let disposed = false;
    const sync = async (includeData: boolean) => {
      try {
        const next = await lianxinApi.avatars(includeData);
        if (disposed) return;
        if (includeData || next.fingerprint !== avatar.fingerprint) {
          const full = includeData ? next : await lianxinApi.avatars(true);
          if (!disposed) setAvatar(full);
        } else {
          setAvatar((current) => ({ ...current, enabled: next.enabled, size: next.size, gap: next.gap, border: next.border }));
        }
      } catch { /* Keep the bundled avatar while the API is unavailable. */ }
    };
    void sync(true);
    const timer = window.setInterval(() => void sync(false), 2500);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [avatar.fingerprint]);
  const openSession = (id: number) => void lianxinApi.selectSession(id).then(() => lianxinApi.messages(id)).then((result) => { setActiveWorkspace("chat"); setMessages(result.items.map((item, index) => ({ id: String(item.id ?? index), role: item.role, content: item.content, time: item.timestamp?.slice(11, 16) || "" }))); }).catch(() => undefined);
  const handleSend = async (text: string) => {
    setMessages((current) => [...current, { id: `user-${Date.now()}`, role: "user", content: text, time: "现在" }]);
    setToolRounds([]);
    if (!backendOnline) return;
    setBusy(true);
    try {
      let reply = "";
      for await (const event of lianxinApi.streamChat(text)) {
        if (event.type === "delta") reply += event.content ?? "";
        if (event.type === "tool_round_start") {
          setToolRounds((current) => current.some((round) => round.round === event.round) ? current : [...current, { round: event.round ?? current.length + 1, calls: [] }]);
        }
        if (event.type === "tool_call") {
          const args = event.args ? JSON.stringify(event.args, null, 2) : "";
          setToolRounds((current) => {
            const roundNumber = event.round ?? current.at(-1)?.round ?? 1;
            const rounds = current.length ? [...current] : [{ round: roundNumber, calls: [] }];
            let index = rounds.findIndex((round) => round.round === roundNumber);
            if (index < 0) { rounds.push({ round: roundNumber, calls: [] }); index = rounds.length - 1; }
            rounds[index] = { ...rounds[index], calls: [...rounds[index].calls, { id: `${Date.now()}-${Math.random()}`, name: event.name ?? "未知工具", args, status: "running" }] };
            return rounds;
          });
        }
        if (event.type === "tool_result") {
          setToolRounds((current) => {
            const rounds = current.map((round) => ({ ...round, calls: [...round.calls] }));
            for (let index = rounds.length - 1; index >= 0; index -= 1) {
              const callIndex = rounds[index].calls.findIndex((call) => call.name === event.name && call.status === "running");
              if (callIndex >= 0) { rounds[index].calls[callIndex] = { ...rounds[index].calls[callIndex], status: event.isError ? "error" : "done", preview: event.preview, elapsedMs: event.elapsedMs }; break; }
            }
            return rounds;
          });
        }
        if (event.type === "completed") reply = event.message?.content ?? reply;
        if (event.type === "error") throw new Error(event.error || "聊天请求失败");
      }
      setMessages((current) => [...current, { id: `assistant-${Date.now()}`, role: "assistant", content: reply, time: "现在" }]);
    } catch (error) {
      setMessages((current) => [...current, { id: `error-${Date.now()}`, role: "assistant", content: `后端连接失败：${String(error)}`, time: "现在" }]);
    } finally { setBusy(false); }
  };
  const handleNewChat = () => { setMessages([]); setToolRounds([]); if (backendOnline) void lianxinApi.newSession().catch(() => undefined); };
  const toggleVoice = () => { if (!voiceActive) void lianxinApi.startVoice().then(() => setVoiceActive(true)).catch((error) => window.alert(String(error))); else void lianxinApi.stopVoice().then(() => setVoiceActive(false)); };
  const openLegacy = (feature: LegacyFeature) => { setPanel("none"); void lianxinApi.openLegacy(feature).catch((error) => window.alert(`原版窗口启动失败：${String(error)}`)); };
  const handleWorkspaceChange = (workspace: WorkspaceId) => {
    const feature = workspace === "time-capsule" ? "time-capsule" : workspace === "prism-memory" ? "prism-memory" : workspace === "study-room" ? "study-room" : undefined;
    if (feature) { openLegacy(feature); return; }
    setActiveWorkspace(workspace);
  };
  const controlMusic = (action: string) => void lianxinApi.musicControl(action).then((state) => setMusic(state as MusicState)).catch(() => undefined);
  const handleDrag = (event: MouseEvent<HTMLElement>) => {
    if (!desktopWindow || event.button !== 0 || (event.target as HTMLElement).closest("button, input, textarea, a")) return;
    void desktopWindow.startDragging().catch((error) => console.warn("窗口拖拽失败", error));
  };
  const handleResize = (event: MouseEvent<HTMLButtonElement>) => { event.stopPropagation(); if (desktopWindow) void desktopWindow.startResizeDragging("SouthEast").catch(() => undefined); };
  const handleMinimize = () => { if (desktopWindow) void desktopWindow.minimize().catch(() => undefined); };
  const handleMaximize = () => { if (desktopWindow) void desktopWindow.toggleMaximize().catch(() => undefined); };
  const handleClose = () => { if (desktopWindow) void desktopWindow.close().catch(() => undefined); };
  const wallpaperStyle = { opacity: background.enabled ? Math.max(0, Math.min(1, background.opacity)) : 0, ...(background.dataUrl ? { backgroundImage: `url(${background.dataUrl})`, backgroundSize: background.fitMode === "stretch" ? "100% 100%" : background.fitMode } : {}) };
  return <div className="app-shell"><div className="wallpaper" style={{ opacity: background.enabled ? 1 : 0 }}><div className="wallpaper-image" style={wallpaperStyle} /></div><div className="app-surface"><TopBar onNewChat={handleNewChat} onPanel={setPanel} onLegacy={openLegacy} onMinimize={handleMinimize} onMaximize={handleMaximize} onClose={handleClose} onDrag={handleDrag} /><div className="app-body"><SideNavigation activeWorkspace={activeWorkspace} onWorkspaceChange={handleWorkspaceChange} managementOpen={managementOpen} onManagementToggle={() => setManagementOpen((open) => !open)} onPanel={setPanel} onLegacy={openLegacy} avatar={avatar} /><main className="main-stage"><div className="workspace-title-mobile"><Menu size={17} />{workspaceLabel}</div>{activeWorkspace === "chat" ? <ChatWorkspace messages={messages} toolRounds={toolRounds} avatar={avatar} onSend={handleSend} busy={busy} voiceActive={voiceActive} onVoice={() => setPanel("voice")} /> : <Workspace workspace={activeWorkspace} music={music} />}</main><CompanionRail online={backendOnline} music={music} onControl={controlMusic} axis={axis} avatar={avatar} /></div></div><button className="resize-grip" title="调整窗口大小" onMouseDown={handleResize} aria-label="调整窗口大小" />{panel === "history" && <HistoryPanel onClose={() => setPanel("none")} onOpen={openSession} />}{panel === "note" && <NotePanel onClose={() => setPanel("none")} />}{panel === "tasks" && <TaskPanel onClose={() => setPanel("none")} />}{panel === "voice" && <VoicePanel active={voiceActive} onClose={() => setPanel("none")} onToggle={toggleVoice} />}{panel === "proactive" && <ProactivePanel onClose={() => setPanel("none")} />}{panel === "management" && <ManagementPanel onClose={() => setPanel("none")} onVoice={() => setPanel("voice")} onLegacy={openLegacy} />}</div>;
}
