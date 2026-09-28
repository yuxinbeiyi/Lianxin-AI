import { useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";
import {
  Activity, AlarmClock, ArrowUp, BookOpen, Bot, BrainCircuit, Camera, Check,
  CheckCircle2, ChevronDown, ChevronRight, Clock3, Command, FileText, FolderOpen,
  Gauge, Gem, Image, LayoutDashboard, Lightbulb, Maximize2, Menu,
  MessageCircle, Mic, Minimize2, Music2, Network, Pause, Play, Plus,
  Search, Send, Settings, Sparkles, UserRound, Video, Volume2, Waves, Wrench, X,
} from "lucide-react";
import { managementGroups, primaryNavigation, demoMessages } from "./data/demo";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { lianxinApi, type AvatarState, type BackgroundState, type ChatStreamEvent, type LegacyFeature } from "./services/lianxinApi";
import type { Message, WorkspaceId } from "./types/ui";
import { ToolPanel } from "./components/ToolPanel";
import { TimeCapsuleWorkspace } from "./components/TimeCapsuleWorkspace";
import { MusicWorkspace } from "./components/MusicWorkspace";

type PanelId = "none" | "history" | "note" | "tasks" | "voice" | "proactive" | "management" | "tools";
type Session = { id: number; title?: string; updated_at?: string; summary?: string; is_pinned?: number };
type MusicState = { active?: boolean; playing?: boolean; paused?: boolean; name?: string; artist?: string; album?: string; coverUrl?: string; progress?: number; duration?: number; volume?: number; mode?: string; current_index?: number; playlist?: Array<{ id?: string; title?: string; artist?: string; duration?: number; index?: number }>; lyrics?: Array<{ time?: number; text?: string }>; style?: string; instrumental?: boolean };
type FiveAxisState = { axes: Record<string, number>; mood: string };
type ToolCallState = { id: string; name: string; args: string; status: "running" | "done" | "error"; preview?: string; elapsedMs?: number };
type ToolRoundState = { round: number; calls: ToolCallState[] };
type Attachment = { kind: "image" | "file"; fileName: string; dataUrl: string; size?: number };

function restoreMessage(item: { id?: number; role: "user" | "assistant"; content: string; timestamp?: string; attachments?: Array<{ kind: "image" | "file"; fileName: string; url?: string; path?: string }> }, index: number): Message {
  const attachment = item.attachments?.[0];
  return { id: String(item.id ?? index), role: item.role, content: item.content, time: item.timestamp?.slice(11, 16) || "", attachments: item.attachments, ...(attachment?.kind === "image" ? { imageUrl: attachment.url, imageName: attachment.fileName, imageStatus: "success" as const } : attachment?.kind === "file" ? { kind: "file" as const, fileName: attachment.fileName } : {}) };
}

const desktopWindow = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window ? getCurrentWindow() : null;

function MessageAvatar({ role, compact = false, avatar, onInteraction, interacting = false }: { role: "user" | "assistant"; compact?: boolean; avatar?: AvatarState; onInteraction?: (action: "tap" | "headpat") => void; interacting?: boolean }) {
  const sizeClass = compact ? "avatar-small" : "avatar-chat";
  const pressTimer = useRef<number | null>(null);
  const clearPress = () => { if (pressTimer.current !== null) { window.clearTimeout(pressTimer.current); pressTimer.current = null; } };
  const startPress = (event: MouseEvent<HTMLDivElement>) => {
    if (role !== "assistant" || !onInteraction || (event.button !== 0 && event.button !== 2)) return;
    clearPress();
    pressTimer.current = window.setTimeout(() => { pressTimer.current = null; onInteraction(event.button === 0 ? "tap" : "headpat"); }, 650);
  };
  useEffect(() => clearPress, []);
  const configuredSize = avatar ? Math.max(30, Math.min(72, Math.round(avatar.size * (compact ? 0.63 : 0.63)))) : undefined;
  const avatarStyle = configuredSize ? { width: `${configuredSize}px`, height: `${configuredSize}px`, border: avatar?.border === false ? "0" : undefined } : undefined;
  if (role === "assistant") {
    return <div className={`avatar ${sizeClass} assistant-avatar ${interacting ? "avatar-interacting" : ""}`} style={avatarStyle} onMouseDown={startPress} onMouseUp={clearPress} onMouseLeave={clearPress} onContextMenu={(event) => event.preventDefault()}><img src={avatar?.assistantDataUrl || "/lianxin-avatar.png"} alt="莲心聊天头像" /></div>;
  }
  return <div className={`avatar ${sizeClass} user-avatar`} style={avatarStyle}>{avatar?.userDataUrl ? <img src={avatar.userDataUrl} alt="我" /> : <span>我</span>}</div>;
}

const managementFeatureMap: Record<string, LegacyFeature> = {
  "历史记录": "history", "备忘本": "note", "闹钟与提醒": "alarm", "任务运行中心": "workflow",
  "后台职责中心": "duty", "主动聊天": "proactive", "涟漪情感系统": "ripple", "人格枢控": "persona",
  "星图系统": "memory-constellation", "棱镜记忆系统": "prism-memory", "莲心自习室": "study-room",
  "时间胶囊": "time-capsule", "数据潮汐": "data-tide", "能力中枢": "capability", "视觉理解": "vision",
  "语音转录": "voice-stt", "摄像头": "camera", "声音设置": "sound", "全局设置": "settings", "API Key": "api",
  "网络设置": "network", "QQ 聊天": "qq", "微信聊天": "wechat",
};
const legacyFeatureIds = new Set(Object.values(managementFeatureMap));

const iconMap: Record<string, typeof MessageCircle> = {
  message: MessageCircle, music: Music2, clock: Clock3, diamond: Gem, book: BookOpen, world: Network,
};
const managementIconMap: Record<string, typeof Settings> = {
  "闹钟与提醒": AlarmClock, "任务运行中心": Gauge, "主动聊天": MessageCircle, "后台职责中心": Bot,
  "涟漪情感系统": Waves, "人格枢控": UserRound, "星图系统": Network,
  "能力中枢": BrainCircuit, "视觉理解": Image, "语音转录": Mic, "摄像头": Camera,
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
    <nav className="top-actions"><button className="top-action" onClick={() => onLegacy("history")}><Clock3 size={16} />历史记录</button><button className="top-action" onClick={onNewChat}><Plus size={17} />新建对话</button><button className="top-action" onClick={() => onLegacy("video-call")}><Video size={16} />视频通话</button><button className="mode-button" onClick={() => onLegacy("galgame")}><Sparkles size={16} />Galgame 模式</button><button className="icon-button" title="全局设置" onClick={() => onPanel("management")}><Settings size={18} /></button></nav>
    <div className="window-actions"><button className="icon-button" title="最小化" onClick={onMinimize}><Minimize2 size={16} /></button><button className="icon-button" title="最大化 / 还原" onClick={onMaximize}><Maximize2 size={16} /></button><button className="icon-button close-button" title="关闭莲心" onClick={onClose}><X size={17} /></button></div>
  </header>;
}

function SideNavigation({ activeWorkspace, onWorkspaceChange, managementOpen, onManagementToggle, onPanel, onLegacy, avatar }: { activeWorkspace: WorkspaceId; onWorkspaceChange: (workspace: WorkspaceId) => void; managementOpen: boolean; onManagementToggle: () => void; onPanel: (panel: PanelId) => void; onLegacy: (feature: LegacyFeature) => void; avatar?: AvatarState }) {
  return <aside className="side-nav"><div className="nav-scroll"><div className="nav-section-label">莲心空间</div>{primaryNavigation.map((item) => <button className={`nav-item ${item.id === activeWorkspace ? "is-selected" : ""}`} key={item.id} onClick={() => onWorkspaceChange(item.id)}><Icon name={item.icon} /><span>{item.label}</span>{item.id === activeWorkspace && <span className="nav-indicator" />}</button>)}<div className="nav-divider" /><button className={`nav-item management-trigger ${managementOpen ? "is-open" : ""}`} onClick={onManagementToggle}><Settings size={19} /><span>管理中心</span>{managementOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</button>{managementOpen && <div className="management-menu">{managementGroups.map((group) => <div className="management-group" key={group.label}><div className="management-label">{group.label}</div>{group.items.map((item) => { const ItemIcon = managementIconMap[item] ?? Settings; const feature = managementFeatureMap[item]; const action = feature ? () => onLegacy(feature) : item === "语音聊天" ? () => onPanel("voice") : undefined; return <button className="management-item" key={item} onClick={action}><ItemIcon size={15} />{item}</button>; })}</div>)}</div>}</div><div className="profile-strip"><MessageAvatar role="assistant" compact avatar={avatar} /><div className="profile-copy"><strong>莲心</strong><span><span className="status-dot" />在线 · 你在我心里</span></div><Activity size={17} className="profile-activity" /></div></aside>;
}

function TaskCard() { return <div className="task-card"><div className="task-card-header"><span><LayoutDashboard size={15} />正在处理当前请求</span><span className="task-running"><Activity size={14} />运行中</span></div><div className="task-step is-done"><span className="step-icon"><Check size={12} /></span>分析用户问题</div><div className="task-step is-done"><span className="step-icon"><Check size={12} /></span>整理当前上下文</div><div className="task-step is-active"><span className="step-icon"><span /></span>生成回复</div></div>; }

function MessageItem({ message, avatar, onInteraction, interacting, onQuote, onDelete }: { message: Message; avatar?: AvatarState; onInteraction?: (action: "tap" | "headpat") => void; interacting?: boolean; onQuote?: (message: Message) => void; onDelete?: (message: Message) => void }) {
  if (message.kind === "task") return <div className="message-row assistant-row"><MessageAvatar role="assistant" avatar={avatar} /><div className="message-column"><div className="message-meta"><strong>莲心</strong><span>{message.time}</span></div><TaskCard /></div></div>;
  if (message.imageUrl) return <div className={`message-row ${message.role === "user" ? "user-row" : "assistant-row"}`}><MessageAvatar role={message.role} avatar={avatar} onInteraction={message.role === "assistant" ? onInteraction : undefined} interacting={interacting && message.role === "assistant"} /><div className="message-column"><div className="message-meta"><strong>{message.role === "user" ? "你" : "莲心"}</strong><span>{message.time}</span></div><div className={`message-bubble image-message-bubble ${message.role === "user" ? "user-bubble" : ""}`}><img src={message.imageUrl} alt={message.imageName || "发送的图片"} /><span className="image-file-name">{message.imageName || "图片"}</span>{message.imageStatus === "pending" && <span className="image-analysis-state">正在分析图片…</span>}{message.imageStatus === "error" && <span className="image-analysis-state is-error">图片分析失败</span>}{message.imageDescription && <p className="image-description">{message.imageDescription}</p>}</div></div></div>;
  if (message.kind === "file") return <div className={`message-row ${message.role === "user" ? "user-row" : "assistant-row"}`}><MessageAvatar role={message.role} avatar={avatar} /><div className="message-column"><div className="message-meta"><strong>{message.role === "user" ? "你" : "莲心"}</strong><span>{message.time}</span></div><div className={`message-bubble file-message-bubble ${message.role === "user" ? "user-bubble" : ""}`}><FolderOpen size={18} /><span>{message.fileName || "文件附件"}</span>{message.fileSize ? <small>{Math.ceil(message.fileSize / 1024)} KB</small> : null}</div></div></div>;
  return <div className={`message-row ${message.role === "user" ? "user-row" : "assistant-row"}`}><MessageAvatar role={message.role} avatar={avatar} onInteraction={message.role === "assistant" ? onInteraction : undefined} interacting={interacting && message.role === "assistant"} /><div className="message-column"><div className="message-meta"><strong>{message.role === "user" ? "你" : "莲心"}</strong><span>{message.time}</span></div><div className={`message-bubble ${message.role === "user" ? "user-bubble" : ""}`} onContextMenu={(event) => { event.preventDefault(); const action = window.prompt("输入 c 复制、q 引用、d 删除，直接取消关闭"); if (action?.toLowerCase() === "c") void navigator.clipboard?.writeText(message.content); if (action?.toLowerCase() === "q") onQuote?.(message); if (action?.toLowerCase() === "d") onDelete?.(message); }}>{message.content.split("\n").map((line, index) => <p key={`${message.id}-${index}`}>{line}</p>)}<div className="message-actions"><button type="button" title="复制" onClick={() => void navigator.clipboard?.writeText(message.content)}>复制</button><button type="button" title="引用" onClick={() => onQuote?.(message)}>引用</button>{message.role === "assistant" && <button type="button" title="朗读" onClick={() => { const utterance = new SpeechSynthesisUtterance(message.content); utterance.lang = "zh-CN"; window.speechSynthesis.cancel(); window.speechSynthesis.speak(utterance); }}>朗读</button>}<button type="button" title="删除" onClick={() => onDelete?.(message)}>删除</button></div></div></div></div>;
}

function ToolRounds({ rounds }: { rounds: ToolRoundState[] }) {
  if (!rounds.length) return null;
  return <div className="tool-rounds">{rounds.map((round) => <section className="tool-round" key={round.round}><div className="tool-round-heading"><span><Wrench size={14} />莲心执行工具 · 第 {round.round} 轮</span><span>{round.calls.every((call) => call.status !== "running") ? "已完成" : "执行中"}</span></div>{round.calls.map((call) => <div className={`tool-call ${call.status}`} key={call.id}><span className="tool-call-icon">{call.status === "running" ? <span className="tool-spinner" /> : call.status === "error" ? <X size={13} /> : <CheckCircle2 size={14} />}</span><div className="tool-call-copy"><strong>{call.name}</strong><span>{call.status === "running" ? "正在执行…" : call.status === "error" ? "执行失败" : `已完成${call.elapsedMs ? ` · ${(call.elapsedMs / 1000).toFixed(1)}s` : ""}`}</span>{call.preview && <p>{call.preview}</p>}</div></div>)}</section>)}</div>;
}

function ChatWorkspace({ messages, toolRounds, avatar, onSend, onCancel = () => { void lianxinApi.cancelChat(); }, onInteraction, interactionThinking, busy, voiceActive, onVoice, onOpenNote = () => undefined, onTools = () => window.dispatchEvent(new Event("open-tools")) }: { messages: Message[]; toolRounds: ToolRoundState[]; avatar?: AvatarState; onSend: (text: string, attachments: Attachment[], selection?: { forcedTool?: string; preferredTool?: string }) => void; onCancel?: () => void; onInteraction: (action: "tap" | "headpat") => void; interactionThinking: boolean; busy: boolean; voiceActive: boolean; onVoice: () => void; onOpenNote?: () => void; onTools?: () => void }) {
  const [draft, setDraft] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [autoSend, setAutoSend] = useState(false);
  const [toolSelection, setToolSelection] = useState<{ forcedTool?: string; preferredTool?: string }>(() => { try { return JSON.parse(window.localStorage.getItem("lianxin-tool-selection") || "{}"); } catch { return {}; } });
  const [toolsOpen, setToolsOpen] = useState(false);
  useEffect(() => { const handler = (event: Event) => setToolSelection((event as CustomEvent).detail || {}); window.addEventListener("tool-selection", handler); return () => window.removeEventListener("tool-selection", handler); }, []);
  const [hiddenMessageIds, setHiddenMessageIds] = useState<Set<string>>(new Set());
  const fileInput = useRef<HTMLInputElement | null>(null);
  const allFileInput = useRef<HTMLInputElement | null>(null);
  const addFiles = (files: FileList | File[]) => Array.from(files).forEach((file) => { const kind = file.type.startsWith("image/") ? "image" : "file"; const reader = new FileReader(); reader.onload = () => { const item = { kind, fileName: file.name, dataUrl: String(reader.result || ""), size: file.size } as Attachment; setAttachments((current) => [...current, item]); if (autoSend && !busy) window.setTimeout(() => onSend("看看这个附件", [item]), 0); }; reader.readAsDataURL(file); });
  const submit = () => { const text = draft.trim(); if ((!text && !attachments.length) || busy) return; onSend(text || "看看这张图片", attachments, toolSelection); setDraft(""); setAttachments([]); };
  const quote = (message: Message) => setDraft((current) => `${current ? `${current}\n` : ""}[引用] ${message.role === "user" ? "你" : "莲心"}：${message.content.slice(0, 200)}\n我的回复：`);
  return <section className="workspace chat-workspace"><div className="conversation-header"><div><span className="conversation-live" /><strong>默认对话</strong><span className="edit-title">✎</span></div><span className="conversation-meta">Python 核心 · {busy || interactionThinking ? "正在回复" : "已连接"}</span></div><div className="message-viewport"><div className="message-list">{messages.length ? messages.filter((message) => !hiddenMessageIds.has(message.id)).map((message) => <MessageItem key={message.id} message={message} avatar={avatar} onInteraction={onInteraction} interacting={interactionThinking} onQuote={quote} onDelete={(item) => setHiddenMessageIds((current) => new Set(current).add(item.id))} />) : <div className="empty-state"><Sparkles size={28} /><h3>开始和莲心聊天</h3><p>你的新会话会自动保存到历史记录。</p></div>}<ToolRounds rounds={toolRounds} />{(busy || interactionThinking) && <div className="typing-indicator"><span /><span /><span />莲心正在思考…</div>}</div></div><div className="composer-wrap"><div className="composer" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); addFiles(event.dataTransfer.files); }} onPaste={(event) => { if (event.clipboardData.files.length) { event.preventDefault(); addFiles(event.clipboardData.files); } }}><div className="attachment-preview">{attachments.map((item, index) => <div className="attachment-thumb" key={`${item.fileName}-${index}`}>{item.kind === "image" ? <img src={item.dataUrl} alt={item.fileName} /> : <div className="file-thumb"><FolderOpen size={18} /></div>}<button type="button" title="移除附件" onClick={() => setAttachments((current) => current.filter((_, itemIndex) => itemIndex !== index))}><X size={12} /></button></div>)}</div><textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); submit(); } }} placeholder="和莲心聊点什么吧…" rows={2} /><div className="composer-toolbar"><div className="composer-tools"><button type="button" className="composer-tool" onClick={() => fileInput.current?.click()}><Image size={16} />图片</button><input ref={fileInput} hidden type="file" accept="image/*" multiple onChange={(event) => { if (event.target.files) addFiles(event.target.files); event.currentTarget.value = ""; }} /><button type="button" className="composer-tool" onClick={() => allFileInput.current?.click()}><FolderOpen size={16} />文件</button><input ref={allFileInput} hidden type="file" multiple onChange={(event) => { if (event.target.files) addFiles(event.target.files); event.currentTarget.value = ""; }} /><button type="button" className="composer-tool" onClick={onTools}><Wrench size={16} />工具</button><button type="button" className="composer-tool" title="先选中消息后引用" onClick={() => setDraft((current) => `${current ? `${current}\n` : ""}[引用] `)}><Command size={16} />引用</button><label className="auto-send-control"><input type="checkbox" checked={autoSend} onChange={(event) => setAutoSend(event.target.checked)} />自动发送</label><button type="button" className="composer-tool" title="打开备忘本" onClick={onOpenNote}><FileText size={16} />备忘本</button></div><div className="composer-actions"><button className={`icon-button voice-button ${voiceActive ? "voice-active" : ""}`} title="语音聊天" onClick={onVoice}><Mic size={18} /></button><button className="send-button" onClick={busy ? onCancel : submit} disabled={interactionThinking || (!busy && !draft.trim() && !attachments.length)} title={busy ? "停止回复" : "发送"}>{busy ? <X size={18} /> : <Send size={18} />}</button></div></div></div></div></section>;
}

function AvatarInteractionCard({ avatar }: { avatar?: AvatarState }) {
  return <section className="rail-card avatar-interaction-card"><div className="rail-card-title"><span><Sparkles size={15} />莲心角色区</span><span className="avatar-hint">角色立绘</span></div><div className="character-stage"><img src={avatar?.characterDataUrl || "/lianxin-avatar.png"} alt="莲心角色立绘" /></div></section>;
}

function NowPlayingCard({ music, onControl }: { music: MusicState; onControl: (action: string) => void }) { const progress = music.duration ? Math.min(100, ((music.progress ?? 0) / music.duration) * 100) : 0; return <section className="rail-card"><div className="rail-card-title"><span><Music2 size={15} />当前播放</span><ChevronDown size={15} /></div><div className="playing"><div className="album-art">{music.coverUrl ? <img src={music.coverUrl} alt="cover" onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} /> : (music.active ? <Play size={25} /> : <Music2 size={25} />)}</div><div className="playing-copy"><strong>{music.active ? music.name || "未知歌曲" : "暂无播放"}</strong><span>{music.artist || "网易云音乐"}</span><div className="progress-line"><span style={{ width: `${progress}%` }} /></div></div></div><div className="player-controls"><button title="上一首" onClick={() => onControl("previous")}><ChevronRight size={16} className="previous-icon" /></button><button className="play-button" title={music.active ? "暂停" : "播放"} onClick={() => onControl("toggle")}>{music.active ? <Pause size={17} /> : <Play size={17} />}</button><button title="下一首" onClick={() => onControl("next")}><ChevronRight size={16} /></button></div></section>; }

function FiveAxisCard({ state }: { state: FiveAxisState }) { const items = [{ key: "connection", label: "连接需求", color: "#F3C878", signed: false }, { key: "pride", label: "骄傲", color: "#A992FF", signed: true }, { key: "valence", label: "情绪基调", color: "#72D7E8", signed: true }, { key: "arousal", label: "唤醒度", color: "#F49BBE", signed: true }, { key: "immersion", label: "沉浸度", color: "#86D6A6", signed: false }]; return <section className="rail-card axis-card"><div className="rail-card-title"><span><Activity size={15} />AI 主动意识 · 五轴状态</span><strong>{state.mood || "中性"}</strong></div>{items.map((item) => { const value = Math.max(item.signed ? -1 : 0, Math.min(1, Number(state.axes[item.key] ?? 0))); const width = item.signed ? Math.abs(value) * 50 : value * 100; return <div className="axis-row" key={item.key}><span className="axis-label">{item.label}</span><div className={`axis-track ${item.signed ? "is-signed" : ""}`}><span className="axis-fill" style={{ width: `${width}%`, left: item.signed && value < 0 ? `${50 - width}%` : item.signed ? "50%" : "0", background: item.color }} />{item.signed && <i />}</div><span className="axis-value">{item.signed ? value.toFixed(2) : value.toFixed(2)}</span></div>; })}<div className="axis-motive">基于莲心当前情绪与活动状态实时更新</div></section>; }

function CompanionRail({ music, onControl, axis, avatar }: { online?: boolean; music: MusicState; onControl: (action: string) => void; axis: FiveAxisState; avatar?: AvatarState }) { return <aside className="companion-rail"><AvatarInteractionCard avatar={avatar} /><NowPlayingCard music={music} onControl={onControl} /><FiveAxisCard state={axis} /></aside>; }

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

function Workspace({ workspace, music, onControl, musicError, onDismissMusicError }: { workspace: WorkspaceId; music: MusicState; onControl: (action: string, payload?: Record<string, unknown>) => void; musicError?: string; onDismissMusicError?: () => void }) { if (workspace === "music") return <MusicWorkspace music={music} onControl={onControl} errorMsg={musicError} onDismissError={onDismissMusicError} />; const item = primaryNavigation.find((entry) => entry.id === workspace)!; return <section className="workspace placeholder-workspace"><div className="placeholder-icon"><Icon name={item.icon} /></div><p className="eyebrow">莲心空间</p><h1>{item.label}</h1><p>这里将逐步接入莲心现有的真实能力与数据。</p><button className="secondary-button"><ArrowUp size={15} />返回对话</button></section>; }
export function App() {
  const [activeWorkspace, setActiveWorkspace] = useState<WorkspaceId>("chat"); const [managementOpen, setManagementOpen] = useState(false); const [messages, setMessages] = useState<Message[]>(demoMessages); const [interactionMessages, setInteractionMessages] = useState<Message[]>([]); const [backendOnline, setBackendOnline] = useState(false); const [busy, setBusy] = useState(false); const [interactionThinking, setInteractionThinking] = useState(false); const [panel, setPanel] = useState<PanelId>("none"); const [music, setMusic] = useState<MusicState>({}); const [musicError, setMusicError] = useState(""); const [axis, setAxis] = useState<FiveAxisState>({ axes: {}, mood: "中性" }); const [voiceActive, setVoiceActive] = useState(false); const [toolRounds, setToolRounds] = useState<ToolRoundState[]>([]); const [background, setBackground] = useState<BackgroundState>({ enabled: true, opacity: 0.22, fitMode: "cover", fingerprint: "" }); const [avatar, setAvatar] = useState<AvatarState>({ enabled: true, size: 60, gap: 10, border: true, fingerprint: "" });
  const workspaceLabel = useMemo(() => primaryNavigation.find((item) => item.id === activeWorkspace)?.label ?? "对话", [activeWorkspace]);
  useEffect(() => { const check = () => { void lianxinApi.status().then((status) => { setBackendOnline(status.online); if (status.sessionId) void lianxinApi.messages(status.sessionId).then((result) => { if (result.items.length) setMessages(result.items.map((item, index) => ({ id: String(item.id ?? index), role: item.role, content: item.content, time: item.timestamp?.slice(11, 16) || "" }))); }).catch(() => undefined); }).catch(() => setBackendOnline(false)); void lianxinApi.musicState().then((state) => setMusic(state as MusicState)).catch(() => undefined); void lianxinApi.fiveAxis().then(setAxis).catch(() => undefined); }; check(); const timer = window.setInterval(check, 5000); return () => window.clearInterval(timer); }, []);
  useEffect(() => { document.documentElement.style.setProperty("--lx-chat-mask", String(background.chatOpacity ?? 0.75)); }, [background.chatOpacity]);
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
          setBackground((current) => ({ ...current, enabled: next.enabled, opacity: next.opacity, chatOpacity: next.chatOpacity, fitMode: next.fitMode }));
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
  const openSession = (id: number) => void lianxinApi.selectSession(id).then(() => lianxinApi.messages(id)).then((result) => { setActiveWorkspace("chat"); setInteractionMessages([]); setMessages(result.items.map(restoreMessage)); }).catch(() => undefined);
  const handleSend = async (text: string, attachments: Attachment[] = [], selection: { forcedTool?: string; preferredTool?: string } = {}) => {
    const messageTime = "现在";
    const imageIds = attachments.map((_, index) => `image-${Date.now()}-${index}`);
    setMessages((current) => [...current, { id: `user-${Date.now()}`, role: "user", content: text, time: messageTime }, ...attachments.map((item, index) => item.kind === "image" ? ({ id: imageIds[index], role: "user" as const, content: "", time: messageTime, imageUrl: item.dataUrl, imageName: item.fileName, imageStatus: "pending" as const }) : ({ id: imageIds[index], role: "user" as const, content: "", time: messageTime, kind: "file" as const, fileName: item.fileName, fileSize: item.size }))]);
    setToolRounds([]);
    if (!backendOnline) return;
    setBusy(true);
    try {
      let reply = "";
      for await (const event of lianxinApi.streamChat(text, attachments, selection)) {
        if (event.type === "delta") reply += event.content ?? "";
        if (event.type === "image_analysis_result" && typeof event.index === "number") {
          const imageId = imageIds[event.index];
          setMessages((current) => current.map((message) => message.id === imageId ? { ...message, imageStatus: event.error ? "error" : "success", imageDescription: event.description } : message));
        }
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
  useEffect(() => {
    let cursor = 0;
    const poll = () => void lianxinApi.voiceEvents(cursor).then((result) => {
      cursor = result.latest;
      for (const event of result.items) {
        if (event.type === "voice.transcript" && event.content?.trim()) void handleSend(event.content.trim());
      }
    }).catch(() => undefined);
    const timer = window.setInterval(poll, 700);
    return () => window.clearInterval(timer);
  }, [backendOnline]);
  useEffect(() => { const open = () => setPanel("tools"); window.addEventListener("open-tools", open); return () => window.removeEventListener("open-tools", open); }, []);
  const handleCancel = () => { void lianxinApi.cancelChat(); setBusy(false); };
  const handleNewChat = () => { setMessages([]); setInteractionMessages([]); setToolRounds([]); if (backendOnline) void lianxinApi.newSession().catch(() => undefined); };
  const toggleVoice = () => { if (!voiceActive) void lianxinApi.startVoice().then(() => setVoiceActive(true)).catch((error) => window.alert(String(error))); else void lianxinApi.stopVoice().then(() => setVoiceActive(false)); };
  const openLegacy = (feature: LegacyFeature) => { setPanel("none"); void lianxinApi.openLegacy(feature).catch((error) => window.alert(`原版窗口启动失败：${String(error)}`)); };
  const handleWorkspaceChange = (workspace: WorkspaceId) => {
    const feature = workspace === "time-capsule" ? "time-capsule" : workspace === "prism-memory" ? "prism-memory" : workspace === "study-room" ? "study-room" : undefined;
    if (feature) { openLegacy(feature); return; }
    setActiveWorkspace(workspace);
  };
  const controlMusic = (action: string, payload?: Record<string, unknown>) => void lianxinApi.musicControl(action, payload).then((state) => { setMusic(state as MusicState); setMusicError(""); }).catch((error) => setMusicError(String((error as Error)?.message ?? "音乐控制失败")));
  const handleAvatarInteraction = (action: "tap" | "headpat") => {
    if (interactionThinking || busy) return;
    setInteractionThinking(true);
    void lianxinApi.avatarAction(action).then((result) => {
      if (!result.accepted) return;
      const response = result.response;
      if (response) setMessages((current) => [...current, { id: `avatar-${Date.now()}`, role: "assistant", content: response, time: "现在" }]);
    }).catch(() => undefined).finally(() => setInteractionThinking(false));
  };
  const handleDrag = (event: MouseEvent<HTMLElement>) => {
    if (!desktopWindow || event.button !== 0 || (event.target as HTMLElement).closest("button, input, textarea, a")) return;
    void desktopWindow.startDragging().catch((error) => console.warn("窗口拖拽失败", error));
  };
  const handleResize = (event: MouseEvent<HTMLButtonElement>) => { event.stopPropagation(); if (desktopWindow) void desktopWindow.startResizeDragging("SouthEast").catch(() => undefined); };
  const handleMinimize = () => { if (desktopWindow) void desktopWindow.minimize().catch(() => undefined); };
  const handleMaximize = () => { if (desktopWindow) void desktopWindow.toggleMaximize().catch(() => undefined); };
  const handleClose = () => { if (desktopWindow) void desktopWindow.close().catch(() => undefined); };
  const wallpaperStyle = { opacity: background.enabled ? Math.max(0, Math.min(1, background.opacity)) : 0, ...(background.dataUrl ? { backgroundImage: `url(${background.dataUrl})`, backgroundSize: background.fitMode === "stretch" ? "100% 100%" : background.fitMode } : {}) };
  const selectedTools = (() => { try { return JSON.parse(window.localStorage.getItem("lianxin-tool-selection") || "{}"); } catch { return {}; } })();
  if (activeWorkspace === "time-capsule") return <div className="app-shell"><div className="wallpaper" style={{ opacity: background.enabled ? 1 : 0 }}><div className="wallpaper-image" style={wallpaperStyle} /></div><div className="app-surface"><TopBar onNewChat={handleNewChat} onPanel={setPanel} onLegacy={openLegacy} onMinimize={handleMinimize} onMaximize={handleMaximize} onClose={handleClose} onDrag={handleDrag} /><div className="app-body"><SideNavigation activeWorkspace={activeWorkspace} onWorkspaceChange={handleWorkspaceChange} managementOpen={managementOpen} onManagementToggle={() => setManagementOpen((open) => !open)} onPanel={setPanel} onLegacy={openLegacy} avatar={avatar} /><main className="main-stage"><TimeCapsuleWorkspace /></main><CompanionRail online={backendOnline} music={music} onControl={controlMusic} axis={axis} avatar={avatar} /></div></div></div>;
  if (panel === "tools") return <ToolPanel selected={selectedTools} onSelect={(selection) => { window.localStorage.setItem("lianxin-tool-selection", JSON.stringify(selection)); window.dispatchEvent(new CustomEvent("tool-selection", { detail: selection })); }} onClose={() => setPanel("none")} />;
  return <div className="app-shell"><div className="wallpaper" style={{ opacity: background.enabled ? 1 : 0 }}><div className="wallpaper-image" style={wallpaperStyle} /></div><div className="app-surface"><TopBar onNewChat={handleNewChat} onPanel={setPanel} onLegacy={openLegacy} onMinimize={handleMinimize} onMaximize={handleMaximize} onClose={handleClose} onDrag={handleDrag} /><div className="app-body"><SideNavigation activeWorkspace={activeWorkspace} onWorkspaceChange={handleWorkspaceChange} managementOpen={managementOpen} onManagementToggle={() => setManagementOpen((open) => !open)} onPanel={setPanel} onLegacy={openLegacy} avatar={avatar} /><main className="main-stage"><div className="workspace-title-mobile"><Menu size={17} />{workspaceLabel}</div>{activeWorkspace === "chat" ? <ChatWorkspace messages={messages} toolRounds={toolRounds} avatar={avatar} onSend={handleSend} onInteraction={handleAvatarInteraction} interactionThinking={interactionThinking} busy={busy} voiceActive={voiceActive} onVoice={() => setPanel("voice")} /> : <Workspace workspace={activeWorkspace} music={music} onControl={controlMusic} musicError={musicError} onDismissMusicError={() => setMusicError("")} />}</main><CompanionRail online={backendOnline} music={music} onControl={controlMusic} axis={axis} avatar={avatar} /></div></div><button className="resize-grip" title="调整窗口大小" onMouseDown={handleResize} aria-label="调整窗口大小" />{panel === "history" && <HistoryPanel onClose={() => setPanel("none")} onOpen={openSession} />}{panel === "note" && <NotePanel onClose={() => setPanel("none")} />}{panel === "tasks" && <TaskPanel onClose={() => setPanel("none")} />}{panel === "voice" && <VoicePanel active={voiceActive} onClose={() => setPanel("none")} onToggle={toggleVoice} />}{panel === "proactive" && <ProactivePanel onClose={() => setPanel("none")} />}{panel === "management" && <ManagementPanel onClose={() => setPanel("none")} onVoice={() => setPanel("voice")} onLegacy={openLegacy} />}</div>;
}
