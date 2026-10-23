import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type ReactNode,
} from "react";
import {
  Activity,
  AlarmClock,
  ArrowDown,
  ArrowUp,
  BookOpen,
  Bot,
  BrainCircuit,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  Command,
  FileText,
  FolderOpen,
  Gauge,
  Gem,
  Image,
  LayoutDashboard,
  Lightbulb,
  Maximize2,
  Menu,
  MessageCircle,
  Mic,
  Minimize2,
  Music2,
  Network,
  Pause,
  Play,
  Plus,
  Search,
  Send,
  Settings,
  Sparkles,
  UserRound,
  Video,
  Volume2,
  VolumeX,
  Waves,
  Wrench,
  X,
} from "lucide-react";
import { managementGroups, primaryNavigation, demoMessages } from "./data/demo";
import {
  shouldSendAsConversation,
  splitConversationText,
  splitSemanticText,
} from "./utils/textSegmentation";
import { getCurrentWindow } from "@tauri-apps/api/window";
import {
  lianxinApi,
  type AvatarState,
  type BilibiliInteractionState,
  type BilibiliSurfState,
  type BackgroundState,
  type ChatStreamEvent,
  type LegacyFeature,
  type PersonaState,
  type ExecutionTaskState,
  type ToolActivityEvent,
} from "./services/lianxinApi";
import type { Message, WorkspaceId } from "./types/ui";
import { ToolPanel } from "./components/ToolPanel";
import { ExecutionPanel } from "./components/ExecutionPanel";
import { ExecutionCard } from "./components/ExecutionCard";
import { TimeCapsuleWorkspace } from "./components/TimeCapsuleWorkspace";
import { MusicWorkspace } from "./components/MusicWorkspace";
import { VideoCallPanel } from "./components/VideoCallPanel";

type PanelId =
  | "none"
  | "history"
  | "note"
  | "tasks"
  | "voice"
  | "video-call"
  | "proactive"
  | "management"
  | "tools"
  | "constellation"
  | "ripple"
  | "persona"
  | "data-tide"
  | "study-room"
  | "time-capsule";
type Session = {
  id: number;
  title?: string;
  updated_at?: string;
  summary?: string;
  is_pinned?: number;
};
type MusicState = {
  stateVersion?: number;
  updatedAt?: number;
  active?: boolean;
  playing?: boolean;
  paused?: boolean;
  name?: string;
  artist?: string;
  album?: string;
  coverUrl?: string;
  progress?: number;
  duration?: number;
  volume?: number;
  mode?: string;
  current_index?: number;
  playlist?: Array<{
    id?: string;
    title?: string;
    artist?: string;
    duration?: number;
    index?: number;
  }>;
  lyrics?: Array<{ time?: number; text?: string }>;
  style?: string;
  instrumental?: boolean;
};
type FiveAxisState = { axes: Record<string, number>; mood: string };
type ToolCallState = {
  seq: number;
  name: string;
  argsPreview: string;
  status: "running" | "done" | "error";
  preview?: string;
  elapsedMs?: number;
};
// 工具信息栏：一轮对话里莲心先后调用的工具集合。
// anchorIndex 记录它应该插在消息流中的位置（该轮回复之前），创建后不再变化，
// 因此卡片位置固定，且只有重启莲心 / 新建会话时才会消失。
type ToolRoundState = {
  key: string;
  turn: number;
  round: number;
  anchorIndex: number;
  calls: ToolCallState[];
};
type Attachment = {
  kind: "image" | "file";
  fileName: string;
  dataUrl: string;
  size?: number;
};

function restoreMessage(
  item: {
    id?: number;
    role: "user" | "assistant";
    content: string;
    timestamp?: string;
    attachments?: Array<{
      kind: "image" | "file";
      fileName: string;
      url?: string;
      path?: string;
      description?: string;
    }>;
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
  },
  index: number,
): Message {
  const attachment = item.attachments?.[0];
  return {
    id: String(item.id ?? index),
    role: item.role,
    content: item.content,
    time: item.timestamp?.slice(11, 16) || "",
    ...(item.kind === "execution_task"
      ? {
          kind: "execution_task" as const,
          execution: {
            taskId: item.executionTaskId,
            threadId: item.executionThreadId,
            status: item.executionStatus,
            goal: item.executionGoal,
            rounds: item.executionRounds,
            elapsed: item.executionElapsed,
            reason: item.executionReason,
            summary: item.executionSummary,
            narrated: item.executionNarrated,
          },
        }
      : {}),
    attachments: item.attachments,
    ...(attachment?.kind === "image"
      ? {
          imageUrl: attachment.url,
          imageName: attachment.fileName,
          imageStatus: "success" as const,
          imageDescription: attachment.description,
        }
      : attachment?.kind === "file"
        ? { kind: "file" as const, fileName: attachment.fileName }
        : {}),
  };
}

const desktopWindow =
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window
    ? getCurrentWindow()
    : null;

function MessageAvatar({
  role,
  compact = false,
  avatar,
  onInteraction,
  interacting = false,
  talking = false,
}: {
  role: "user" | "assistant";
  compact?: boolean;
  avatar?: AvatarState;
  onInteraction?: (action: "tap" | "headpat") => void;
  interacting?: boolean;
  talking?: boolean;
}) {
  const sizeClass = compact ? "avatar-small" : "avatar-chat";
  const pressTimer = useRef<number | null>(null);
  const clearPress = () => {
    if (pressTimer.current !== null) {
      window.clearTimeout(pressTimer.current);
      pressTimer.current = null;
    }
  };
  const startPress = (event: MouseEvent<HTMLDivElement>) => {
    if (
      role !== "assistant" ||
      !onInteraction ||
      (event.button !== 0 && event.button !== 2)
    )
      return;
    clearPress();
    pressTimer.current = window.setTimeout(() => {
      pressTimer.current = null;
      onInteraction(event.button === 0 ? "tap" : "headpat");
    }, 650);
  };
  useEffect(() => clearPress, []);
  const configuredSize = avatar
    ? Math.max(
        30,
        Math.min(72, Math.round(avatar.size * (compact ? 0.63 : 0.63))),
      )
    : undefined;
  const avatarStyle = configuredSize
    ? {
        width: `${configuredSize}px`,
        height: `${configuredSize}px`,
        border: avatar?.border === false ? "0" : undefined,
      }
    : undefined;
  if (role === "assistant") {
    const talkingClass = talking ? "avatar-talking" : "";
    return (
      <div
        className={`avatar ${sizeClass} assistant-avatar ${interacting ? "avatar-interacting" : ""} ${talkingClass}`}
        style={avatarStyle}
        onMouseDown={startPress}
        onMouseUp={clearPress}
        onMouseLeave={clearPress}
        onContextMenu={(event) => event.preventDefault()}
      >
        <img
          src={avatar?.assistantDataUrl || "/lianxin-avatar.png"}
          alt="莲心聊天头像"
        />
      </div>
    );
  }
  return (
    <div
      className={`avatar ${sizeClass} user-avatar ${interacting ? "avatar-interacting" : ""}`}
      style={avatarStyle}
    >
      {avatar?.userDataUrl ? (
        <img src={avatar.userDataUrl} alt="我" />
      ) : (
        <span>我</span>
      )}
    </div>
  );
}

const managementFeatureMap: Record<string, LegacyFeature> = {
  历史记录: "history",
  备忘本: "note",
  闹钟与提醒: "alarm",
  任务运行中心: "workflow",
  后台职责中心: "duty",
  主动聊天: "proactive",
  涟漪情感系统: "ripple",
  人格枢控: "persona",
  星图系统: "memory-constellation",
  棱镜记忆系统: "prism-memory",
  莲心自习室: "study-room",
  时间胶囊: "time-capsule",
  数据潮汐: "data-tide",
  能力中枢: "capability",
  视觉理解: "vision",
  语音转录: "voice-stt",
  声音设置: "sound",
  全局设置: "settings",
  "API Key": "api",
  网络设置: "network",
  "QQ 聊天": "qq",
  微信聊天: "wechat",
};
const legacyFeatureIds = new Set(Object.values(managementFeatureMap));

const iconMap: Record<string, typeof MessageCircle> = {
  message: MessageCircle,
  music: Music2,
  clock: Clock3,
  diamond: Gem,
  book: BookOpen,
  world: Network,
};
const managementIconMap: Record<string, typeof Settings> = {
  闹钟与提醒: AlarmClock,
  任务运行中心: Gauge,
  主动聊天: MessageCircle,
  后台职责中心: Bot,
  涟漪情感系统: Waves,
  人格枢控: UserRound,
  星图系统: Network,
  能力中枢: BrainCircuit,
  视觉理解: Image,
  语音转录: Mic,
  声音设置: Volume2,
  全局设置: Settings,
  "API Key": Command,
  网络设置: Network,
  "QQ 聊天": MessageCircle,
  微信聊天: MessageCircle,
  数据潮汐: Activity,
};

function Icon({ name }: { name: string }) {
  const Component = iconMap[name] ?? Sparkles;
  return <Component size={19} strokeWidth={1.8} aria-hidden="true" />;
}

function TopBar({
  onNewChat,
  onPanel,
  onLegacy,
  onMinimize,
  onMaximize,
  onClose,
}: {
  onNewChat: () => void;
  onPanel: (panel: PanelId) => void;
  onLegacy: (feature: LegacyFeature) => void;
  onMinimize: () => void;
  onMaximize: () => void;
  onClose: () => void;
}) {
  return (
    <header className="topbar" data-tauri-drag-region>
      <div className="brand-block" data-tauri-drag-region>
        <div className="brand-mark">
          <Sparkles size={24} />
        </div>
        <div>
          <div className="brand-name">
            莲心 <span>AI</span>
          </div>
          <div className="brand-status">
            <span className="status-dot" /> 在线
          </div>
        </div>
      </div>
      <div className="search-box" role="search">
        <Search size={17} />
        <span>搜索对话、记忆和功能</span>
        <kbd>Ctrl K</kbd>
      </div>
      <nav className="top-actions">
        <button className="top-action" onClick={() => onLegacy("history")}>
          <Clock3 size={16} />
          历史记录
        </button>
        <button className="top-action" onClick={onNewChat}>
          <Plus size={17} />
          新建对话
        </button>
        <button className="top-action" onClick={() => onLegacy("video-call")}>
          <Video size={16} />
          视频通话
        </button>
        <button className="mode-button" onClick={() => onLegacy("galgame")}>
          <Sparkles size={16} />
          桌宠模式
        </button>
      </nav>
      <div className="window-actions">
        <button className="icon-button" title="最小化" onClick={onMinimize}>
          <Minimize2 size={16} />
        </button>
        <button
          className="icon-button"
          title="最大化 / 还原"
          onClick={onMaximize}
        >
          <Maximize2 size={16} />
        </button>
        <button
          className="icon-button close-button"
          title="关闭莲心"
          onClick={onClose}
        >
          <X size={17} />
        </button>
      </div>
    </header>
  );
}

function SideNavigation({
  activeWorkspace,
  onWorkspaceChange,
  managementOpen,
  onManagementToggle,
  onPanel,
  onLegacy,
  avatar,
}: {
  activeWorkspace: WorkspaceId;
  onWorkspaceChange: (workspace: WorkspaceId) => void;
  managementOpen: boolean;
  onManagementToggle: () => void;
  onPanel: (panel: PanelId) => void;
  onLegacy: (feature: LegacyFeature) => void;
  avatar?: AvatarState;
}) {
  return (
    <aside className="side-nav">
      <div className="nav-scroll">
        <div className="nav-section-label">莲心空间</div>
        {primaryNavigation.map((item) => (
          <button
            className={`nav-item ${item.id === activeWorkspace ? "is-selected" : ""}`}
            key={item.id}
            onClick={() => onWorkspaceChange(item.id)}
          >
            <Icon name={item.icon} />
            <span>{item.label}</span>
            {item.id === activeWorkspace && <span className="nav-indicator" />}
          </button>
        ))}
        <div className="nav-divider" />
        <button
          className={`nav-item management-trigger ${managementOpen ? "is-open" : ""}`}
          onClick={onManagementToggle}
        >
          <Settings size={19} />
          <span>管理中心</span>
          {managementOpen ? (
            <ChevronDown size={16} />
          ) : (
            <ChevronRight size={16} />
          )}
        </button>
        {managementOpen && (
          <div className="management-menu">
            {managementGroups.map((group) => (
              <div className="management-group" key={group.label}>
                <div className="management-label">{group.label}</div>
                {group.items.map((item) => {
                  const ItemIcon = managementIconMap[item] ?? Settings;
                  const feature = managementFeatureMap[item];
                  const action = feature
                    ? () => onLegacy(feature)
                    : item === "语音聊天"
                      ? () => onPanel("voice")
                      : undefined;
                  return (
                    <button
                      className="management-item"
                      key={item}
                      onClick={action}
                    >
                      <ItemIcon size={15} />
                      {item}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="profile-strip">
        <MessageAvatar role="assistant" compact avatar={avatar} />
        <div className="profile-copy">
          <strong>莲心</strong>
          <span>
            <span className="status-dot" />
            在线 · 你在我心里
          </span>
        </div>
        <Activity size={17} className="profile-activity" />
      </div>
    </aside>
  );
}

function TaskCard() {
  return (
    <div className="task-card">
      <div className="task-card-header">
        <span>
          <LayoutDashboard size={15} />
          正在处理当前请求
        </span>
        <span className="task-running">
          <Activity size={14} />
          运行中
        </span>
      </div>
      <div className="task-step is-done">
        <span className="step-icon">
          <Check size={12} />
        </span>
        分析用户问题
      </div>
      <div className="task-step is-done">
        <span className="step-icon">
          <Check size={12} />
        </span>
        整理当前上下文
      </div>
      <div className="task-step is-active">
        <span className="step-icon">
          <span />
        </span>
        生成回复
      </div>
    </div>
  );
}

function ImageMessageContent({ message }: { message: Message }) {
  const [expanded, setExpanded] = useState(false);
  const fromAssistant = message.role === "assistant";
  // 莲心侧气泡：正文就是她自己的那句话（同一消息里渲染），
  // 原始观察记录是给模型用的报告腔素材，不再塞进气泡（避免出戏）。
  const description = fromAssistant ? "" : message.imageDescription || "";
  const collapsible = description.length > 100;
  const visibleDescription =
    expanded || !collapsible ? description : `${description.slice(0, 100)}...`;
  return (
    <>
      <img
        src={message.imageUrl}
        alt={fromAssistant ? "莲心看到的画面" : message.imageName || "发送的图片"}
      />
      <span className="image-file-name">
        {fromAssistant ? "莲心看到的画面" : message.imageName || "图片"}
      </span>
      {message.imageStatus === "pending" && (
        <span className="image-analysis-state">正在分析图片…</span>
      )}
      {message.imageStatus === "error" && (
        <span className="image-analysis-state is-error">图片分析失败</span>
      )}
      {description && (
        <div className="image-description-wrap">
          <p className="image-description">{visibleDescription}</p>
          {collapsible && (
            <button
              type="button"
              className="image-description-toggle"
              onClick={() => setExpanded((value) => !value)}
            >
              {expanded ? "收起 ▴" : "展开 ▾"}
            </button>
          )}
        </div>
      )}
    </>
  );
}

function MessageItem({
  message,
  avatar,
  sessionId,
  onInteraction,
  interacting,
  userInteracting = false,
  talking = false,
  onQuote,
  onDelete,
  onContextMenu,
}: {
  message: Message;
  avatar?: AvatarState;
  sessionId?: number;
  onInteraction?: (action: "tap" | "headpat") => void;
  interacting?: boolean;
  userInteracting?: boolean;
  talking?: boolean;
  onQuote?: (message: Message) => void;
  onDelete?: (message: Message) => void;
  onContextMenu?: (event: MouseEvent<HTMLDivElement>, message: Message) => void;
}) {
  if (message.kind === "interaction")
    return (
      <div className="interaction-notice" role="status">
        {message.content}
      </div>
    );
  if (message.kind === "task")
    return (
      <div className="message-row assistant-row">
        <MessageAvatar role="assistant" avatar={avatar} />
        <div className="message-column">
          <div className="message-meta">
            <strong>莲心</strong>
            <span>{message.time}</span>
          </div>
          <TaskCard />
        </div>
      </div>
    );
  if (message.kind === "execution_task")
    return (
      <div className="message-row assistant-row">
        <MessageAvatar
          role="assistant"
          avatar={avatar}
          onInteraction={onInteraction}
          interacting={interacting}
        />
        <div className="message-column">
          <div className="message-meta">
            <strong>莲心</strong>
            <span>{message.time}</span>
          </div>
          <ExecutionCard
            data={message.execution}
            content={message.content}
            sessionId={sessionId}
          />
        </div>
      </div>
    );
  if (message.imageUrl)
    return (
      <div
        className={`message-row ${message.role === "user" ? "user-row" : "assistant-row"}`}
      >
        <MessageAvatar
          role={message.role}
          avatar={avatar}
          onInteraction={
            message.role === "assistant" ? onInteraction : undefined
          }
          interacting={
            message.role === "assistant" ? interacting : userInteracting
          }
        />
        <div className="message-column">
          <div className="message-meta">
            <strong>{message.role === "user" ? "你" : "莲心"}</strong>
            <span>{message.time}</span>
          </div>
          {message.content.trim() && (
            <div
              className={`message-bubble ${message.role === "user" ? "user-bubble" : ""}`}
            >
              {message.content.split("\n").map((line, index) => (
                <p key={`${message.id}-text-${index}`}>{line}</p>
              ))}
            </div>
          )}
          <div
            className={`message-bubble image-message-bubble ${message.role === "user" ? "user-bubble" : ""}`}
            onContextMenu={(event) => {
              event.preventDefault();
              onContextMenu?.(event, message);
            }}
          >
            <ImageMessageContent message={message} />
          </div>
        </div>
      </div>
    );
  if (message.kind === "file")
    return (
      <div
        className={`message-row ${message.role === "user" ? "user-row" : "assistant-row"}`}
      >
        <MessageAvatar
          role={message.role}
          avatar={avatar}
          interacting={message.role === "user" ? userInteracting : false}
        />
        <div className="message-column">
          <div className="message-meta">
            <strong>{message.role === "user" ? "你" : "莲心"}</strong>
            <span>{message.time}</span>
          </div>
          <div
            className={`message-bubble file-message-bubble ${message.role === "user" ? "user-bubble" : ""}`}
            onContextMenu={(event) => {
              event.preventDefault();
              onContextMenu?.(event, message);
            }}
          >
            <FolderOpen size={18} />
            <span>{message.fileName || "文件附件"}</span>
            {message.fileSize ? (
              <small>{Math.ceil(message.fileSize / 1024)} KB</small>
            ) : null}
          </div>
        </div>
      </div>
    );
  return (
    <div
      className={`message-row ${message.role === "user" ? "user-row" : "assistant-row"}`}
    >
      <MessageAvatar
        role={message.role}
        avatar={avatar}
        onInteraction={message.role === "assistant" ? onInteraction : undefined}
        interacting={
          message.role === "assistant" ? interacting : userInteracting
        }
      />
      <div className="message-column">
        <div className="message-meta">
          <strong>{message.role === "user" ? "你" : "莲心"}</strong>
          <span>{message.time}</span>
        </div>
        <div
          className={`message-bubble ${message.role === "user" ? "user-bubble" : ""}`}
          onContextMenu={(event) => {
            event.preventDefault();
            onContextMenu?.(event, message);
          }}
        >
          {message.content.split("\n").map((line, index) => (
            <p key={`${message.id}-${index}`}
              className={line.startsWith("莲心在 B 站视频《") ? "bili-comment-notice" : undefined}
              role={line.startsWith("莲心在 B 站视频《") ? "status" : undefined}
            >{line}{line.startsWith("莲心在 B 站视频《") && (() => {
              const target = message.content.match(/https:\/\/www\.bilibili\.com\/video\/BV[A-Za-z0-9]+/);
              return target ? <a href={target[0]} target="_blank" rel="noreferrer"> 打开视频</a> : null;
            })()}</p>
          ))}
        </div>
      </div>
    </div>
  );
}

function ToolRounds({ rounds }: { rounds: ToolRoundState[] }) {
  if (!rounds.length) return null;
  return (
    <div className="tool-rounds">
      {rounds.map((round) => (
        <section className="tool-round" key={round.key}>
          <div className="tool-round-heading">
            <span>
              <Wrench size={14} />
              莲心执行工具 · 第 {round.round} 轮
            </span>
            <span>
              {round.calls.every((call) => call.status !== "running")
                ? "已完成"
                : "执行中"}
            </span>
          </div>
          {round.calls.map((call) => (
            <div
              className={`tool-call ${call.status}`}
              key={`${round.key}:${call.seq}`}
            >
              <span className="tool-call-icon">
                {call.status === "running" ? (
                  <span className="tool-spinner" />
                ) : call.status === "error" ? (
                  <X size={13} />
                ) : (
                  <CheckCircle2 size={14} />
                )}
              </span>
              <div className="tool-call-copy">
                <strong>{call.name}</strong>
                <span>
                  {call.status === "running"
                    ? "正在执行…"
                    : call.status === "error"
                      ? "执行失败"
                      : `已完成${call.elapsedMs ? ` · ${(call.elapsedMs / 1000).toFixed(1)}s` : ""}`}
                </span>
                {call.status === "running" && call.argsPreview && (
                  <p>{call.argsPreview}</p>
                )}
                {call.preview && <p>{call.preview}</p>}
              </div>
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}

function ChatWorkspace({
  messages,
  segmentBubblesFor,
  toolRounds,
  avatar,
  sessionId,
  onSend,
  onCancel = () => {
    void lianxinApi.cancelChat();
  },
  onInteraction,
  interactionThinking,
  proactiveThinking,
  busy,
  voiceInputActive,
  onVoiceInput,
  voiceInputText,
  ttsSpeaking,
  ttsStage,
  ttsNotice,
  silentMode,
  onToggleMute,
  onOpenNote = () => undefined,
  onTools = () => window.dispatchEvent(new Event("open-tools")),
  runtimeTask,
  onRuntimeApprove,
  onRuntimeStop,
  runtimeDeciding,
}: {
  messages: Message[];
  segmentBubblesFor: (message: Message) => string[];
  toolRounds: ToolRoundState[];
  avatar?: AvatarState;
  sessionId?: number;
  onSend: (
    text: string,
    attachments: Attachment[],
    selection?: { forcedTool?: string; preferredTool?: string },
    quote?: { role: "user" | "assistant"; content: string },
  ) => void;
  onCancel?: () => void;
  onInteraction: (action: "tap" | "headpat") => void;
  interactionThinking: boolean;
  proactiveThinking: boolean;
  busy: boolean;
  voiceInputActive: boolean;
  onVoiceInput: () => void;
  voiceInputText: string;
  ttsSpeaking: boolean;
  ttsStage: string;
  ttsNotice: string;
  silentMode: boolean;
  onToggleMute: () => void;
  onOpenNote?: () => void;
  onTools?: () => void;
  runtimeTask?: ExecutionTaskState | null;
  onRuntimeApprove?: (
    requestId: string,
    decision: "accept" | "acceptForSession" | "decline",
  ) => void;
  onRuntimeStop?: () => void;
  runtimeDeciding?: boolean;
}) {
  const [draft, setDraft] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [quotedMessage, setQuotedMessage] = useState<{
    role: "user" | "assistant";
    content: string;
  } | null>(null);
  const [userInteracting, setUserInteracting] = useState(false);
  useEffect(() => {
    const handleCounterAction = () => {
      setUserInteracting(true);
      window.setTimeout(() => setUserInteracting(false), 700);
    };
    window.addEventListener("avatar-counter-action", handleCounterAction);
    return () =>
      window.removeEventListener("avatar-counter-action", handleCounterAction);
  }, []);
  const [autoSend, setAutoSend] = useState(false);
  const [toolSelection, setToolSelection] = useState<{
    forcedTool?: string;
    preferredTool?: string;
  }>(() => {
    try {
      return JSON.parse(
        window.localStorage.getItem("lianxin-tool-selection") || "{}",
      );
    } catch {
      return {};
    }
  });
  const [toolsOpen, setToolsOpen] = useState(false);
  useEffect(() => {
    const handler = (event: Event) =>
      setToolSelection((event as CustomEvent).detail || {});
    window.addEventListener("tool-selection", handler);
    return () => window.removeEventListener("tool-selection", handler);
  }, []);
  const [hiddenMessageIds, setHiddenMessageIds] = useState<Set<string>>(
    new Set(),
  );
  // 每个锚点位置上要渲染的工具信息栏（同一锚点多轮时按时间顺序排列）
  const toolBlocksByAnchor = useMemo(() => {
    const map = new Map<number, ToolRoundState[]>();
    for (const block of toolRounds) {
      const list = map.get(block.anchorIndex);
      if (list) list.push(block);
      else map.set(block.anchorIndex, [block]);
    }
    return map;
  }, [toolRounds]);
  const trailingToolBlocks = useMemo(
    () => toolRounds.filter((block) => block.anchorIndex >= messages.length),
    [toolRounds, messages.length],
  );
  const fileInput = useRef<HTMLInputElement | null>(null);
  const allFileInput = useRef<HTMLInputElement | null>(null);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const nearBottomRef = useRef(true);
  const [nearBottom, setNearBottom] = useState(true);
  const scrollToBottom = (behavior: ScrollBehavior = "auto") => {
    const el = viewportRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior });
  };
  const handleScroll = () => {
    const el = viewportRef.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
    nearBottomRef.current = near;
    setNearBottom(near);
  };
  useEffect(() => {
    if (nearBottomRef.current)
      requestAnimationFrame(() => scrollToBottom("auto"));
  }, [messages]);
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const onImageLoad = () => {
      if (nearBottomRef.current)
        requestAnimationFrame(() => scrollToBottom("auto"));
    };
    el.addEventListener("load", onImageLoad, true);
    return () => el.removeEventListener("load", onImageLoad, true);
  }, []);
  const addFiles = (files: FileList | File[]) =>
    Array.from(files).forEach((file) => {
      const kind = file.type.startsWith("image/") ? "image" : "file";
      const reader = new FileReader();
      reader.onload = () => {
        const item = {
          kind,
          fileName: file.name,
          dataUrl: String(reader.result || ""),
          size: file.size,
        } as Attachment;
        setAttachments((current) => [...current, item]);
        if (autoSend)
          window.setTimeout(() => {
            onSend("看看这个附件", [item]);
            nearBottomRef.current = true;
            setNearBottom(true);
            requestAnimationFrame(() => scrollToBottom("smooth"));
          }, 0);
      };
      reader.readAsDataURL(file);
    });
  const submit = () => {
    const text = draft.trim();
    if (!text && !attachments.length) return;
    onSend(
      text || "看看这张图片",
      attachments,
      toolSelection,
      quotedMessage || undefined,
    );
    setDraft("");
    setAttachments([]);
    setQuotedMessage(null);
    nearBottomRef.current = true;
    setNearBottom(true);
    requestAnimationFrame(() => scrollToBottom("smooth"));
  };
  const quote = (message: Message) => {
    setQuotedMessage({ role: message.role, content: message.content });
    setDraft((current) => current);
  };
  useEffect(() => {
    if (voiceInputText && voiceInputText.trim())
      setDraft(voiceInputText.trim());
  }, [voiceInputText]);
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    message: Message;
  } | null>(null);
  const openContextMenu = (
    event: MouseEvent<HTMLDivElement>,
    message: Message,
  ) => {
    setContextMenu({ x: event.clientX, y: event.clientY, message });
  };
  const onDeleteMessage = (message: Message) =>
    setHiddenMessageIds((current) => new Set(current).add(message.id));
  return (
    <section className="workspace chat-workspace">
      <div className="conversation-header">
        <div>
          <span className="conversation-live" />
          <strong>默认对话</strong>
          <span className="edit-title">✎</span>
        </div>
        <span className="conversation-meta">
          Python 核心 ·{" "}
          {busy || interactionThinking || proactiveThinking
            ? "正在回复"
            : "已连接"}
        </span>
      </div>
      <div
        className="message-viewport"
        ref={viewportRef}
        onScroll={handleScroll}
      >
        <div className="message-list">
          {messages.length ? (
            messages.map((message, index) => (
              <Fragment key={message.id}>
                <ToolRounds rounds={toolBlocksByAnchor.get(index) ?? []} />
                {hiddenMessageIds.has(message.id)
                  ? null
                  : segmentBubblesFor(message).map((segment, segmentIndex) => (
                      <MessageItem
                        key={`${message.id}#${segmentIndex}`}
                        message={
                          segmentIndex === 0
                            ? { ...message, content: segment }
                            : {
                                ...message,
                                id: `${message.id}#${segmentIndex}`,
                                content: segment,
                              }
                        }
                        avatar={avatar}
                        sessionId={sessionId}
                        onInteraction={onInteraction}
                        interacting={interactionThinking}
                        userInteracting={userInteracting}
                        onQuote={quote}
                        onDelete={(item) =>
                          setHiddenMessageIds((current) =>
                            new Set(current).add(item.id),
                          )
                        }
                        onContextMenu={openContextMenu}
                      />
                    ))}
              </Fragment>
            ))
          ) : (
            <div className="empty-state">
              <Sparkles size={28} />
              <h3>开始和莲心聊天</h3>
              <p>你的新会话会自动保存到历史记录。</p>
            </div>
          )}
          <ToolRounds rounds={trailingToolBlocks} />
          {ttsNotice && (
            <div className="tts-failure-notice">
              <span className="tts-failure-dot" />
              {ttsNotice}
            </div>
          )}
          {(busy || interactionThinking || proactiveThinking || ttsStage) && (
            <div className="typing-indicator">
              <span />
              <span />
              <span />
              {ttsStage || "莲心正在思考…"}
            </div>
          )}
        </div>
        {!nearBottom && (
          <button
            className="scroll-to-bottom"
            type="button"
            onClick={() => scrollToBottom("smooth")}
          >
            <ArrowDown size={15} />
            回到底部
          </button>
        )}
      </div>
      {runtimeTask ? (
        <ExecutionPanel
          state={runtimeTask}
          onApprove={onRuntimeApprove ?? (() => undefined)}
          onStop={onRuntimeStop ?? (() => undefined)}
          deciding={runtimeDeciding}
        />
      ) : null}
      <div className="composer-wrap">
        <div
          className="composer"
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            addFiles(event.dataTransfer.files);
          }}
          onPaste={(event) => {
            if (event.clipboardData.files.length) {
              event.preventDefault();
              addFiles(event.clipboardData.files);
            }
          }}
        >
          <div className="attachment-preview">
            {attachments.map((item, index) => (
              <div
                className="attachment-thumb"
                key={`${item.fileName}-${index}`}
              >
                {item.kind === "image" ? (
                  <img src={item.dataUrl} alt={item.fileName} />
                ) : (
                  <div className="file-thumb">
                    <FolderOpen size={18} />
                  </div>
                )}
                <button
                  type="button"
                  title="移除附件"
                  onClick={() =>
                    setAttachments((current) =>
                      current.filter((_, itemIndex) => itemIndex !== index),
                    )
                  }
                >
                  <X size={12} />
                </button>
              </div>
            ))}
          </div>
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
            placeholder="和莲心聊点什么吧…"
            rows={2}
          />
          <div className="composer-toolbar">
            <div className="composer-tools">
              <button
                type="button"
                className="composer-tool"
                onClick={() => fileInput.current?.click()}
              >
                <Image size={16} />
                图片
              </button>
              <input
                ref={fileInput}
                hidden
                type="file"
                accept="image/*"
                multiple
                onChange={(event) => {
                  if (event.target.files) addFiles(event.target.files);
                  event.currentTarget.value = "";
                }}
              />
              <button
                type="button"
                className="composer-tool"
                onClick={() => allFileInput.current?.click()}
              >
                <FolderOpen size={16} />
                文件
              </button>
              <input
                ref={allFileInput}
                hidden
                type="file"
                multiple
                onChange={(event) => {
                  if (event.target.files) addFiles(event.target.files);
                  event.currentTarget.value = "";
                }}
              />
              <button type="button" className="composer-tool" onClick={onTools}>
                <Wrench size={16} />
                工具
              </button>
              <label className="auto-send-control">
                <input
                  type="checkbox"
                  checked={autoSend}
                  onChange={(event) => setAutoSend(event.target.checked)}
                />
                自动发送
              </label>
              <button
                type="button"
                className="composer-tool"
                title="打开备忘本"
                onClick={onOpenNote}
              >
                <FileText size={16} />
                备忘本
              </button>
            </div>
            <div className="composer-actions">
              <button
                className={`icon-button speaker-button ${silentMode ? "speaker-muted" : ""}`}
                title={silentMode ? "取消静默" : ttsSpeaking ? "停止朗读并静音" : "静音朗读"}
                onClick={onToggleMute}
              >
                {silentMode ? <VolumeX size={18} /> : <Volume2 size={18} />}
              </button>
              <button
                className={`icon-button voice-button ${voiceInputActive ? "voice-active" : ""}`}
                title={voiceInputActive ? "停止录音" : "语音输入"}
                onClick={onVoiceInput}
              >
                <Mic size={18} />
              </button>
              <button
                className="send-button"
                onClick={submit}
                disabled={
                  !draft.trim() && !attachments.length
                }
                title={busy ? "加入回复队列" : "发送"}
              >
                <Send size={18} />
              </button>
              {busy && (
                <button
                  className="icon-button"
                  onClick={onCancel}
                  title="停止当前回复"
                  aria-label="停止当前回复"
                >
                  <X size={18} />
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
      {contextMenu && (
        <>
          <div
            className="context-menu-backdrop"
            onClick={() => setContextMenu(null)}
          />
          <div
            className="bubble-context-menu"
            style={{ left: contextMenu.x, top: contextMenu.y }}
            onClick={(event) => event.stopPropagation()}
          >
            <button
              type="button"
              onClick={() => {
                void navigator.clipboard?.writeText(
                  contextMenu.message.content,
                );
                setContextMenu(null);
              }}
            >
              复制
            </button>
            <button
              type="button"
              onClick={() => {
                quote(contextMenu.message);
                setContextMenu(null);
              }}
            >
              引用
            </button>
            {contextMenu.message.role === "assistant" && (
              <button
                type="button"
                onClick={() => {
                  void lianxinApi
                    .speak(contextMenu.message.content)
                    .catch(() => undefined);
                  setContextMenu(null);
                }}
              >
                朗读
              </button>
            )}
            <button
              type="button"
              onClick={() => {
                onDeleteMessage(contextMenu.message);
                setContextMenu(null);
              }}
            >
              删除
            </button>
          </div>
        </>
      )}
    </section>
  );
}

function AvatarInteractionCard({ avatar }: { avatar?: AvatarState }) {
  return (
    <section className="rail-card avatar-interaction-card">
      <div className="rail-card-title">
        <span>
          <Sparkles size={15} />
          莲心角色区
        </span>
        <span className="avatar-hint">角色立绘</span>
      </div>
      <div className="character-stage">
        <img
          src={avatar?.characterDataUrl || "/lianxin-avatar.png"}
          alt="莲心角色立绘"
        />
      </div>
    </section>
  );
}

function NowPlayingCard({
  music,
  onControl,
}: {
  music: MusicState;
  onControl: (action: string) => void;
}) {
  const progress = music.duration
    ? Math.min(100, ((music.progress ?? 0) / music.duration) * 100)
    : 0;
  return (
    <section className="rail-card">
      <div className="rail-card-title">
        <span>
          <Music2 size={15} />
          当前播放
        </span>
        <ChevronDown size={15} />
      </div>
      <div className="playing">
        <div className="album-art">
          {music.coverUrl ? (
            <img
              src={music.coverUrl}
              alt="cover"
              onError={(e) => {
                (e.target as HTMLImageElement).style.display = "none";
              }}
            />
          ) : music.active ? (
            <Play size={25} />
          ) : (
            <Music2 size={25} />
          )}
        </div>
        <div className="playing-copy">
          <strong>
            {music.active ? music.name || "未知歌曲" : "暂无播放"}
          </strong>
          <span>{music.artist || "网易云音乐"}</span>
          <div className="progress-line">
            <span style={{ width: `${progress}%` }} />
          </div>
        </div>
      </div>
      <div className="player-controls">
        <button title="上一首" onClick={() => onControl("previous")}>
          <ChevronRight size={16} className="previous-icon" />
        </button>
        <button
          className="play-button"
          title={music.active ? "暂停" : "播放"}
          onClick={() => onControl("toggle")}
        >
          {music.active ? <Pause size={17} /> : <Play size={17} />}
        </button>
        <button title="下一首" onClick={() => onControl("next")}>
          <ChevronRight size={16} />
        </button>
      </div>
    </section>
  );
}

function FiveAxisCard({ state }: { state: FiveAxisState }) {
  const items = [
    { key: "connection", label: "连接需求", color: "#F3C878", signed: false },
    { key: "pride", label: "骄傲", color: "#A992FF", signed: true },
    { key: "valence", label: "情绪基调", color: "#72D7E8", signed: true },
    { key: "arousal", label: "唤醒度", color: "#F49BBE", signed: true },
    { key: "immersion", label: "沉浸度", color: "#86D6A6", signed: false },
  ];
  return (
    <section className="rail-card axis-card">
      <div className="rail-card-title">
        <span>
          <Activity size={15} />
          AI 主动意识 · 五轴状态
        </span>
        <strong>{state.mood || "中性"}</strong>
      </div>
      {items.map((item) => {
        const value = Math.max(
          item.signed ? -1 : 0,
          Math.min(1, Number(state.axes[item.key] ?? 0)),
        );
        const width = item.signed ? Math.abs(value) * 50 : value * 100;
        return (
          <div className="axis-row" key={item.key}>
            <span className="axis-label">{item.label}</span>
            <div className={`axis-track ${item.signed ? "is-signed" : ""}`}>
              <span
                className="axis-fill"
                style={{
                  width: `${width}%`,
                  left:
                    item.signed && value < 0
                      ? `${50 - width}%`
                      : item.signed
                        ? "50%"
                        : "0",
                  background: item.color,
                }}
              />
              {item.signed && <i />}
            </div>
            <span className="axis-value">
              {item.signed ? value.toFixed(2) : value.toFixed(2)}
            </span>
          </div>
        );
      })}
      <div className="axis-motive">基于莲心当前情绪与活动状态实时更新</div>
    </section>
  );
}

function CompanionRail({
  music,
  onControl,
  axis,
  avatar,
}: {
  online?: boolean;
  music: MusicState;
  onControl: (action: string) => void;
  axis: FiveAxisState;
  avatar?: AvatarState;
}) {
  return (
    <aside className="companion-rail">
      <AvatarInteractionCard avatar={avatar} />
      <NowPlayingCard music={music} onControl={onControl} />
      <FiveAxisCard state={axis} />
    </aside>
  );
}

function Overlay({
  children,
  onClose,
}: {
  children: ReactNode;
  onClose: () => void;
}) {
  return (
    <div className="overlay">
      <section className="overlay-panel">
        <button className="overlay-close" onClick={onClose}>
          <X size={18} />
        </button>
        {children}
      </section>
    </div>
  );
}

function HistoryPanel({
  onClose,
  onOpen,
}: {
  onClose: () => void;
  onOpen: (id: number) => void;
}) {
  const [items, setItems] = useState<Session[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const load = () =>
    lianxinApi
      .sessions(query)
      .then((result) => {
        setItems(result.items);
        setError("");
      })
      .catch((e) => setError(String(e)));
  useEffect(() => {
    void load();
  }, [query]);
  const remove = (id: number) =>
    void lianxinApi
      .deleteSession(id)
      .then(load)
      .catch((e) => setError(String(e)));
  const pin = (id: number) =>
    void lianxinApi
      .updateSession(id, { togglePin: true })
      .then(load)
      .catch((e) => setError(String(e)));
  return (
    <Overlay onClose={onClose}>
      <div className="panel-heading">
        <div>
          <span className="eyebrow">莲心记录</span>
          <h2>历史记录</h2>
        </div>
        <button className="secondary-button" onClick={() => void load()}>
          <Activity size={15} />
          刷新
        </button>
      </div>
      <input
        className="panel-search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="搜索标题、摘要或消息内容"
      />
      {error ? (
        <div className="panel-error">无法连接历史记录：{error}</div>
      ) : (
        <div className="session-list">
          {items.length ? (
            items.map((item) => (
              <div className="session-entry" key={item.id}>
                <button
                  className="session-open"
                  onClick={() => {
                    onOpen(item.id);
                    onClose();
                  }}
                >
                  <div>
                    <strong>
                      {item.is_pinned ? "📌 " : ""}
                      {item.title || "未命名对话"}
                    </strong>
                    <span>{item.summary || item.updated_at || ""}</span>
                  </div>
                  <ChevronRight size={16} />
                </button>
                <div className="session-actions">
                  <button title="置顶" onClick={() => pin(item.id)}>
                    📌
                  </button>
                  <button title="删除" onClick={() => remove(item.id)}>
                    <X size={14} />
                  </button>
                </div>
              </div>
            ))
          ) : (
            <div className="empty-state">
              <Clock3 size={26} />
              <p>暂无历史会话</p>
            </div>
          )}
        </div>
      )}
    </Overlay>
  );
}

function NotePanel({ onClose }: { onClose: () => void }) {
  const [content, setContent] = useState("");
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    void lianxinApi
      .note()
      .then((result) => setContent(result.content))
      .catch(() => undefined);
  }, []);
  const save = () =>
    void lianxinApi
      .saveNote(content)
      .then(() => setSaved(true))
      .catch(() => setSaved(false));
  return (
    <Overlay onClose={onClose}>
      <div className="panel-heading">
        <div>
          <span className="eyebrow">莲心记录</span>
          <h2>备忘本</h2>
        </div>
        <button className="secondary-button" onClick={save}>
          <Check size={15} />
          {saved ? "已保存" : "保存"}
        </button>
      </div>
      <textarea
        className="note-editor"
        value={content}
        onChange={(event) => {
          setContent(event.target.value);
          setSaved(false);
        }}
        placeholder="写下想交给莲心记住的内容…"
      />
    </Overlay>
  );
}

function TaskPanel({ onClose }: { onClose: () => void }) {
  const [data, setData] = useState<{
    todos: unknown[];
    liveTodos?: unknown[];
    autoTasks: unknown[];
    logs: unknown[];
    workflowRuns?: any[];
    progress: { completed: number; total: number; active: string };
  } | null>(null);
  const load = () =>
    void lianxinApi
      .taskSnapshot()
      .then(setData)
      .catch(() => setData(null));
  useEffect(() => {
    load();
    const timer = window.setInterval(load, 2500);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <Overlay onClose={onClose}>
      <div className="panel-heading">
        <div>
          <span className="eyebrow">运行与任务</span>
          <h2>任务运行中心</h2>
        </div>
        <button className="secondary-button" onClick={load}>
          <Activity size={15} />
          刷新
        </button>
      </div>
      {data ? (
        <>
          <div className="task-overview">
            <strong>
              {data.progress.completed}/{data.progress.total}
            </strong>
            <span>当前会话任务完成度 · 每 2.5 秒同步</span>
            <b>{data.progress.active || "当前没有运行中的任务"}</b>
          </div>
          <div className="task-data-grid">
            <div>
              <span>持久任务</span>
              <strong>{data.todos.length}</strong>
            </div>
            <div>
              <span>自动任务</span>
              <strong>{data.autoTasks.length}</strong>
            </div>
            <div>
              <span>Workflow 运行</span>
              <strong>{data.workflowRuns?.length ?? 0}</strong>
            </div>
          </div>
          <h3>最近运行记录</h3>
          <div className="log-list">
            {data.logs.length ? (
              data.logs
                .slice(-8)
                .reverse()
                .map((log: any, index) => (
                  <div className="log-entry" key={index}>
                    <span className={log.success ? "log-ok" : "log-fail"}>
                      {log.success ? "成功" : "失败"}
                    </span>
                    <span>{log.message || "任务步骤"}</span>
                    <time>{log.timestamp || ""}</time>
                  </div>
                ))
            ) : (
              <p className="muted-copy">暂无任务运行记录。</p>
            )}
          </div>
        </>
      ) : (
        <div className="panel-error">
          任务服务暂时不可用，请确认 Python 后端已启动。
        </div>
      )}
    </Overlay>
  );
}

function VoicePanel({
  active,
  onClose,
  onToggle,
}: {
  active: boolean;
  onClose: () => void;
  onToggle: () => void;
}) {
  const [events, setEvents] = useState<
    Array<{
      id: number;
      type: string;
      state?: string;
      content?: string;
      error?: string;
    }>
  >([]);
  useEffect(() => {
    let cursor = 0;
    const poll = () =>
      void lianxinApi
        .voiceEvents(cursor)
        .then((result) => {
          if (result.items.length) {
            cursor = result.latest;
            setEvents((current) => [...current, ...result.items].slice(-20));
          }
        })
        .catch(() => undefined);
    const timer = window.setInterval(poll, 800);
    poll();
    return () => window.clearInterval(timer);
  }, []);
  return (
    <Overlay onClose={onClose}>
      <div className="panel-heading">
        <div>
          <span className="eyebrow">能力与感知</span>
          <h2>语音聊天</h2>
        </div>
        <button
          className={`secondary-button ${active ? "danger-action" : ""}`}
          onClick={onToggle}
        >
          {active ? "停止监听" : "开始监听"}
        </button>
      </div>
      <div className={`voice-state ${active ? "is-active" : ""}`}>
        <Mic size={30} />
        <strong>{active ? "正在监听麦克风" : "语音聊天未启动"}</strong>
        <span>
          {active ? "说话结束后会自动转录并发送给莲心" : "启动后才会访问麦克风"}
        </span>
      </div>
      <div className="voice-events">
        {events.length ? (
          events.map((event) => (
            <div className="log-entry" key={event.id}>
              <span className="log-ok">{event.type.replace("voice.", "")}</span>
              <span>{event.content || event.state || event.error || ""}</span>
            </div>
          ))
        ) : (
          <p className="muted-copy">暂无语音事件。</p>
        )}
      </div>
    </Overlay>
  );
}

function ProactivePanel({ onClose }: { onClose: () => void }) {
  const [enabled, setEnabled] = useState(false);
  const [running, setRunning] = useState(false);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);
  const [bilibili, setBilibili] = useState<BilibiliInteractionState | null>(null);
  const [bilibiliDraft, setBilibiliDraft] = useState({
    mode: "read_only" as "read_only" | "test",
    userUid: "",
    testVideo: "",
    allowOtherReplies: false,
    autoCommentEnabled: false,
    paused: false,
  });
  const [bilibiliSaving, setBilibiliSaving] = useState(false);
  const [bilibiliNotice, setBilibiliNotice] = useState("");
  const [uidLookup, setUidLookup] = useState<{ uid: string; name: string } | null>(null);
  const [videoLookup, setVideoLookup] = useState<{ bvid: string; title: string; author: string; commentOpen: boolean } | null>(null);
  const [bilibiliSurf, setBilibiliSurf] = useState<BilibiliSurfState | null>(null);
  const [surfTagDrafts, setSurfTagDrafts] = useState<Record<string, string>>({});
  const [newSurfTag, setNewSurfTag] = useState("");
  const [surfNotice, setSurfNotice] = useState("");
  const [surfSaving, setSurfSaving] = useState(false);
  const [bilibiliLoginKey, setBilibiliLoginKey] = useState("");
  const [bilibiliLoginQr, setBilibiliLoginQr] = useState("");
  const [bilibiliDraftText, setBilibiliDraftText] = useState("");
  const [bilibiliDraftBusy, setBilibiliDraftBusy] = useState(false);
  useEffect(() => {
    const sync = () => {
      void lianxinApi
        .proactiveState()
        .then((state) => {
          setEnabled(state.desktopEnabled);
          setLoading(false);
        })
        .catch(() => setLoading(false));
      void lianxinApi
        .proactiveStatus()
        .then((state) => setRunning(Boolean(state.running)))
        .catch(() => undefined);
    };
    sync();
    const timer = window.setInterval(sync, 3000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    void lianxinApi
      .bilibiliInteractionState()
      .then((state) => {
        setBilibili(state);
        setBilibiliDraft({
          mode: state.settings.mode,
          userUid: state.settings.userUid,
          testVideo: state.settings.testVideo,
          allowOtherReplies: state.settings.allowOtherReplies,
          autoCommentEnabled: state.settings.autoCommentEnabled,
          paused: state.settings.paused,
        });
        setBilibiliDraftText(state.draft?.text || "");
      })
      .catch(() => setBilibiliNotice("无法读取 B 站互动设置。"));
  }, []);
  const toggle = () => {
    const next = !enabled;
    setEnabled(next);
    void lianxinApi.toggleProactive(next).catch(() => setEnabled(!next));
  };
  const trigger = (mode: string) => {
    if (triggering) return;
    setTriggering(true);
    void lianxinApi
      .proactiveTrigger(mode)
      .catch(() => undefined)
      .finally(() => setTriggering(false));
  };
  const refreshBilibiliSurf = async () => {
    try {
      const state = await lianxinApi.bilibiliSurfState();
      setBilibiliSurf(state);
      setSurfTagDrafts((current) => Object.fromEntries(state.tags.map((tag) => [tag.keyword, current[tag.keyword] ?? tag.search_query ?? ""])));
    } catch {
      setSurfNotice("兴趣标签暂时无法读取。");
    }
  };
  useEffect(() => {
    void refreshBilibiliSurf();
  }, []);
  const saveSurfTag = async (payload: { keyword: string; search_query?: string; status?: "active" | "paused" }) => {
    setSurfSaving(true);
    setSurfNotice("");
    try {
      const state = await lianxinApi.saveBilibiliSurfTag(payload);
      if (!state.ok) {
        setSurfNotice(state.error || "兴趣标签未保存。");
        return;
      }
      setBilibiliSurf(state);
      setSurfTagDrafts((current) => Object.fromEntries(state.tags.map((tag) => [tag.keyword,
        tag.keyword === payload.keyword && payload.search_query !== undefined ? tag.search_query || "" : current[tag.keyword] ?? tag.search_query ?? "",
      ])));
      if (payload.search_query === undefined && payload.status === undefined) setNewSurfTag("");
      setSurfNotice("兴趣标签已保存；留空搜索词时使用标签原词。");
    } catch {
      setSurfNotice("兴趣标签保存失败，请检查莲心服务。");
    } finally {
      setSurfSaving(false);
    }
  };
  const saveBilibiliSettings = async () => {
    setBilibiliSaving(true);
    setBilibiliNotice("");
    try {
      const result = await lianxinApi.saveBilibiliInteractionSettings(bilibiliDraft);
      if (!result.ok || !result.state) {
        setBilibiliNotice(result.error || "设置未保存。");
        return;
      }
      setBilibili(result.state);
      setBilibiliNotice(result.state.settings.userUidConfirmed
        ? result.state.settings.autoCommentEnabled
          ? "设置已保存。自动评论已开启，仍须通过账号、证据和发送校验；回复功能未开放。"
          : "设置已保存。自动评论已关闭；仍可只读冲浪。"
        : "设置已保存。UID 尚未核验，互动仍保持关闭。");
    } catch {
      setBilibiliNotice("设置保存失败，请确认莲心服务正在运行。");
    } finally {
      setBilibiliSaving(false);
    }
  };
  const lookupBilibiliUid = async () => {
    setBilibiliSaving(true);
    setBilibiliNotice("");
    setUidLookup(null);
    try {
      const result = await lianxinApi.verifyBilibiliUserUid(bilibiliDraft.userUid);
      if (result.ok && result.uid && result.name) {
        setBilibiliDraft((current) => ({ ...current, userUid: result.uid! }));
        setUidLookup({ uid: result.uid, name: result.name });
      }
      else setBilibiliNotice(result.error || "无法核验 UID。");
    } catch {
      setBilibiliNotice("UID 查询失败，请检查网络后重试。");
    } finally {
      setBilibiliSaving(false);
    }
  };
  const confirmBilibiliUid = async () => {
    if (!uidLookup || uidLookup.uid !== bilibiliDraft.userUid.trim()) return;
    setBilibiliSaving(true);
    try {
      const saved = await lianxinApi.saveBilibiliInteractionSettings({ userUid: uidLookup.uid });
      if (!saved.ok) {
        setBilibiliNotice(saved.error || "UID 未保存，无法确认。");
        return;
      }
      const result = await lianxinApi.confirmBilibiliUserUid(uidLookup.uid);
      if (!result.ok || !result.state) {
        setBilibiliNotice(result.error || "UID 确认失败。");
        return;
      }
      setBilibili(result.state);
      setBilibiliNotice(`已确认 UID ${uidLookup.uid} 对应账号：${uidLookup.name}。这只是资料匹配确认，不是账号所有权认证。`);
    } catch {
      setBilibiliNotice("UID 确认失败，请重试。");
    } finally {
      setBilibiliSaving(false);
    }
  };
  const inspectBilibiliVideo = async () => {
    setBilibiliSaving(true);
    setVideoLookup(null);
    try {
      const result = await lianxinApi.inspectBilibiliTestVideo(bilibiliDraft.testVideo);
      if (result.ok && result.video) setVideoLookup(result.video);
      else setBilibiliNotice(result.error || "无法读取测试视频信息。");
    } catch {
      setBilibiliNotice("测试视频查询失败，请检查网络后重试。");
    } finally {
      setBilibiliSaving(false);
    }
  };
  const recheckBilibiliComment = async (rpid = "") => {
    if (bilibiliSaving) return;
    setBilibiliSaving(true);
    try {
      const result = await lianxinApi.recheckBilibiliComment(rpid);
      if (result.state) setBilibili(result.state);
      setBilibiliNotice(`${result.message || result.error || "复核未完成。"}\n${result.diagnostic || ""}`);
    } catch {
      setBilibiliNotice("只读复核请求失败，没有发送或重发评论。");
    } finally {
      setBilibiliSaving(false);
    }
  };
  const requestBilibiliTest = async () => {
    if (!bilibili || bilibiliSaving || bilibiliDraftBusy) return;
    const draft = bilibili.draft;
    if (!draft || bilibiliDraftText.trim() !== draft.text) {
      setBilibiliNotice("请先在评论正文框中输入内容，再点击“保存当前正文”；不需要先生成草稿。");
      return;
    }
    if (!window.confirm(`使用账号：${bilibili.account.name}（${bilibili.account.uid}）\n测试视频：${draft.bvid}\n提及 UID：${draft.mentionUid}\n\n${draft.text}\n\n确定公开发布到 B 站吗？`)) return;
    setBilibiliSaving(true);
    try {
      const result = await lianxinApi.requestBilibiliTestComment(bilibiliDraft.testVideo, draft.id, true);
      if (result.state) setBilibili(result.state);
      setBilibiliNotice(result.message || result.error || result.blockers?.join(" ") || "请核对发送结果。");
    } catch {
      setBilibiliNotice("测试请求失败；未确认发送状态，请勿重复操作。 ");
    } finally {
      setBilibiliSaving(false);
    }
  };
  const startBilibiliLogin = async () => {
    setBilibiliSaving(true);
    setBilibiliNotice("");
    try {
      const result = await lianxinApi.startBilibiliLogin();
      if (!result.ok || !result.key) {
        setBilibiliNotice(result.error || "无法生成 B 站登录二维码。");
        return;
      }
      setBilibiliLoginKey(result.key);
      setBilibiliLoginQr(result.qrDataUrl || "");
      setBilibiliNotice(result.qrDataUrl ? "请用 B 站 App 扫码；扫码后点击“检查扫码状态”。" : "二维码图片生成失败，请稍后重试。");
    } catch {
      setBilibiliNotice("登录二维码请求失败，请确认莲心服务正在运行。");
    } finally {
      setBilibiliSaving(false);
    }
  };
  const pollBilibiliLogin = async () => {
    if (!bilibiliLoginKey || bilibiliSaving) return;
    setBilibiliSaving(true);
    try {
      const result = await lianxinApi.pollBilibiliLogin(bilibiliLoginKey);
      if (result.state === "connected") {
        const state = await lianxinApi.bilibiliInteractionState();
        setBilibili(state);
        setBilibiliLoginKey("");
        setBilibiliLoginQr("");
        setBilibiliNotice(`已连接互动账号：${result.account?.name || "B 站账号"}。凭据保存在本机安全存储中。`);
      } else if (result.state === "waiting") {
        setBilibiliNotice("二维码仍在等待扫码。");
      } else if (result.state === "scanned") {
        setBilibiliNotice("已扫码，请在手机上确认登录。");
      } else {
        setBilibiliNotice(result.error || "登录尚未完成，请重新扫码。");
        if (result.state === "expired" || result.state === "no_session") {
          setBilibiliLoginKey("");
          setBilibiliLoginQr("");
        }
      }
    } catch {
      setBilibiliNotice("检查登录状态失败，请稍后重试。");
    } finally {
      setBilibiliSaving(false);
    }
  };
  const logoutBilibili = async () => {
    setBilibiliSaving(true);
    try {
      const result = await lianxinApi.logoutBilibili();
      if (result.ok && result.state) {
        setBilibili(result.state);
        setBilibiliNotice("已删除本机保存的 B 站互动授权。");
      } else setBilibiliNotice(result.error || "解除授权失败。");
    } catch {
      setBilibiliNotice("解除授权失败，请确认莲心服务正在运行。");
    } finally {
      setBilibiliSaving(false);
    }
  };
  const generateBilibiliDraft = async (text?: string) => {
    if (!bilibiliDraft.testVideo.trim() || bilibiliDraftBusy || bilibiliSaving) return;
    if (text !== undefined && !text.trim()) {
      setBilibiliNotice("请先输入要保存的评论正文。");
      return;
    }
    if (text === undefined && bilibiliDraftText.trim()
      && !window.confirm("生成新草稿会替换正文框中的内容。要保留自己写的文字，请取消并点击“保存当前正文”。\n\n确定替换吗？")) return;
    setBilibiliDraftBusy(true);
    setBilibiliNotice("");
    try {
      const result = await lianxinApi.createBilibiliCommentDraft(bilibiliDraft.testVideo, text ?? "");
      if (result.ok && result.draft) {
        setBilibili(result.state);
        setBilibiliDraftText(result.draft.text);
        setBilibiliNotice(text === undefined
          ? "草稿已生成；可以直接修改正文，修改后请点击“保存当前正文”。当前不会发送到 B 站。"
          : "当前正文已保存；请核对保存后的全文和 @，再确认发送。没有自动改写或发送。");
      } else {
        setBilibiliNotice(result.error || result.blockers?.join(" ") || "草稿未生成。");
      }
    } catch {
      setBilibiliNotice("草稿生成或正文保存失败，请确认莲心服务正在运行。");
    } finally {
      setBilibiliDraftBusy(false);
    }
  };
  const clearBilibiliDraft = async () => {
    if (bilibiliDraftBusy || bilibiliSaving) return;
    setBilibiliDraftBusy(true);
    try {
      const result = await lianxinApi.clearBilibiliCommentDraft();
      setBilibili(result.state);
      setBilibiliDraftText("");
      setBilibiliNotice("草稿已清除。");
    } catch {
      setBilibiliNotice("草稿清除失败，请稍后重试。");
    } finally {
      setBilibiliDraftBusy(false);
    }
  };
  return (
    <Overlay onClose={onClose}>
      <div className="panel-heading">
        <div>
          <span className="eyebrow">运行与任务</span>
          <h2>主动聊天</h2>
        </div>
        <button
          className="secondary-button"
          disabled={loading}
          onClick={toggle}
        >
          {enabled ? "已启用" : "已停用"}
        </button>
      </div>
      <div className="setting-row">
        <div>
          <strong>桌面主动聊天</strong>
          <span>
            沿用原有 ProactiveChatScheduler 的时间权重、冷却和活动延迟策略。
          </span>
        </div>
        <button
          className={`toggle ${enabled ? "is-on" : ""}`}
          onClick={toggle}
          aria-label="切换主动聊天"
        >
          <span />
        </button>
      </div>
      <div className="setting-row">
        <div>
          <strong>调试触发</strong>
          <span>
            {running
              ? "莲心正在生成主动消息…"
              : "手动触发一轮主动聊天，验证调度链路是否可用。"}
          </span>
        </div>
      </div>
      <div className="debug-actions">
        <button
          className="secondary-button"
          disabled={triggering || running}
          onClick={() => trigger("normal")}
        >
          {triggering ? "触发中…" : "立即触发（调试）"}
        </button>
        <button
          className="secondary-button"
          disabled={triggering || running}
          onClick={() => trigger("screenshot")}
        >
          截图观察
        </button>
        <button
          className="secondary-button"
          disabled={triggering || running}
          onClick={() => trigger("camera")}
        >
          摄像头观察
        </button>
        <button
          className="secondary-button"
          disabled={triggering || running}
          onClick={() => trigger("bilibili")}
        >
          测试兴趣标签冲浪
        </button>
      </div>
      <section className="bili-interaction-panel" aria-labelledby="bili-interaction-title">
        <h3 id="bili-interaction-title">B 站冲浪与互动</h3>
        <p className="muted-copy">
          每轮按一个兴趣标签搜索最多 3 个视频，按播放量选 1 个后读取简介、字幕与评论，结合当前人格决定是否评论。固定测试视频不参与自动冲浪。
        </p>
        <details className="bili-surf-settings">
          <summary>兴趣标签与最近一轮</summary>
          <div className="bili-surf-tag-list">
            {(bilibiliSurf?.tags || []).map((tag) => (
              <div className="bili-surf-tag" key={tag.keyword}>
                <div><strong>{tag.keyword}</strong><span>{tag.status === "paused" ? "已暂停" : tag.search_query ? `按“${tag.search_query}”搜索` : "按标签原词搜索"}</span></div>
                <input
                  aria-label={`${tag.keyword}的搜索词`}
                  maxLength={80}
                  disabled={surfSaving}
                  value={surfTagDrafts[tag.keyword] ?? ""}
                  placeholder={tag.keyword}
                  onChange={(event) => setSurfTagDrafts((current) => ({ ...current, [tag.keyword]: event.target.value }))}
                />
                <button className="secondary-button" disabled={surfSaving} onClick={() => void saveSurfTag({ keyword: tag.keyword, search_query: surfTagDrafts[tag.keyword] ?? "" })}>保存</button>
                <button className="secondary-button" disabled={surfSaving} onClick={() => void saveSurfTag({ keyword: tag.keyword, status: tag.status === "paused" ? "active" : "paused" })}>{tag.status === "paused" ? "恢复" : "暂停"}</button>
              </div>
            ))}
          </div>
          <div className="bili-surf-add">
            <input aria-label="新增兴趣标签" disabled={surfSaving} maxLength={40} value={newSurfTag} placeholder="添加兴趣标签" onChange={(event) => setNewSurfTag(event.target.value)} />
            <button className="secondary-button" disabled={surfSaving || !newSurfTag.trim()} onClick={() => void saveSurfTag({ keyword: newSurfTag.trim() })}>添加</button>
          </div>
          {surfNotice && <span className="bili-surf-notice">{surfNotice}</span>}
          <div className="bili-surf-latest">
            <strong>最近一轮</strong>
            <button className="secondary-button" disabled={surfSaving} onClick={() => void refreshBilibiliSurf()}>刷新结果（只读）</button>
            {bilibiliSurf?.latest ? (
              <span>{bilibiliSurf.latest.summary} · 标签「{bilibiliSurf.latest.tag || "未选标签"}」/ 查询「{bilibiliSurf.latest.query || "无"}」 · 返回 {bilibiliSurf.latest.returned_count} 条，详情 {bilibiliSurf.latest.enriched_count} 条 · {bilibiliSurf.latest.send_status === "sent_verified" ? "评论已提交并回读匹配" : bilibiliSurf.latest.send_status === "sent_unverified" ? "评论已提交，回读未确认" : bilibiliSurf.latest.send_status === "unknown" ? "发送状态未知，请勿重试" : bilibiliSurf.latest.send_status === "read_only" ? "只读：未发评论" : bilibiliSurf.latest.send_status === "comment_skipped" ? "评论生成未通过，未发送" : bilibiliSurf.latest.send_status === "not_attempted" ? "未进入发送阶段" : `发送状态：${bilibiliSurf.latest.send_status}`} · {new Date(bilibiliSurf.latest.timestamp * 1000).toLocaleString()}</span>
            ) : <span>还没有冲浪诊断记录。</span>}
          </div>
        </details>
        <div className="bili-interaction-grid">
          <label>
            用户的 B 站 UID
            <input
              inputMode="numeric"
              autoComplete="off"
              value={bilibiliDraft.userUid}
              onChange={(event) =>
                setBilibiliDraft((current) => ({ ...current, userUid: event.target.value }))
              }
              placeholder="填写主账号 UID"
            />
          </label>
        </div>
        <div className="bili-interaction-row">
          <div>
            <strong>自动评论并 @ 主号</strong>
            <span>开启并保存后，莲心按兴趣搜索并结合证据与当前人格决定是否评论，每轮最多一条；接口受理不保证大号可见。默认关闭。</span>
          </div>
          <button
            className={`toggle ${bilibiliDraft.autoCommentEnabled ? "is-on" : ""}`}
            onClick={() => setBilibiliDraft((current) => ({ ...current, autoCommentEnabled: !current.autoCommentEnabled }))}
            aria-label="自动评论并 @ 主号"
            aria-pressed={bilibiliDraft.autoCommentEnabled}
          ><span /></button>
        </div>
        <div className="bili-interaction-actions">
          <button className="secondary-button" disabled={!bilibiliDraft.userUid.trim() || bilibiliSaving} onClick={() => void lookupBilibiliUid()}>
            查询 UID 昵称
          </button>
          {uidLookup && uidLookup.uid === bilibiliDraft.userUid.trim() && (
            <button className="secondary-button" disabled={bilibiliSaving} onClick={() => void confirmBilibiliUid()}>
              确认是 {uidLookup.name}
            </button>
          )}
        </div>
        {(bilibili?.settings.userUidConfirmed || videoLookup) && (
          <div className="bili-capability-status">
            {bilibili?.settings.userUidConfirmed && (
              <span>已记录用户资料：{bilibili.settings.userName}（UID {bilibili.settings.userUid}）。资料匹配不代表账号所有权已验证。</span>
            )}
            {videoLookup && (
              <span>{videoLookup.title} · {videoLookup.author} · {videoLookup.bvid} · {videoLookup.commentOpen ? "评论开放" : "评论关闭"}</span>
            )}
          </div>
        )}
        <div className="bili-interaction-row">
          <div>
            <strong>互动账号</strong>
            <span>
              扫码只在本机完成，凭据写入 Windows 安全凭据存储，不会显示或写入普通配置文件。
            </span>
          </div>
          <div className="bili-interaction-actions">
            {bilibili?.account.connected ? (
              <>
                <span className="bili-status">已连接：{bilibili.account.name}</span>
                <button className="secondary-button" disabled={bilibiliSaving} onClick={() => void logoutBilibili()}>
                  解除授权
                </button>
              </>
            ) : (
              <button className="secondary-button" disabled={bilibiliSaving || !bilibili?.account.credentialStoreAvailable} onClick={() => void startBilibiliLogin()}>
                扫码授权互动账号
              </button>
            )}
            {bilibiliLoginKey && (
              <button className="secondary-button" disabled={bilibiliSaving} onClick={() => void pollBilibiliLogin()}>
                检查扫码状态
              </button>
            )}
          </div>
        </div>
        {bilibiliLoginQr && (
          <div className="bili-login-qr">
            <img src={bilibiliLoginQr} alt="B 站扫码登录二维码" />
            <span>二维码约 180 秒有效；扫码后请点击“检查扫码状态”。</span>
          </div>
        )}
        <details className="bili-manual-test">
          <summary>手动测试评论</summary>
          <p className="muted-copy">不影响自动冲浪目标；仅在人工确认后公开发送。</p>
          <div className="bili-interaction-grid">
          <label>
            手动测试模式
            <select
              value={bilibiliDraft.mode}
              onChange={(event) =>
                setBilibiliDraft((current) => ({
                  ...current,
                  mode: event.target.value as "read_only" | "test",
                }))
              }
            >
              <option value="read_only">禁止手动发送</option>
              <option value="test">指定测试区（手动一次）</option>
            </select>
          </label>
          <label className="bili-video-field">
            测试视频
            <input
              autoComplete="off"
              value={bilibiliDraft.testVideo}
              onChange={(event) =>
                setBilibiliDraft((current) => ({ ...current, testVideo: event.target.value }))
              }
              placeholder="BV 号或 bilibili.com/video 链接"
            />
          </label>
          </div>
          <div className="bili-interaction-actions">
          <button className="secondary-button" disabled={!bilibiliDraft.testVideo.trim() || bilibiliSaving} onClick={() => void inspectBilibiliVideo()}>
            查询测试视频
          </button>
          </div>
        <div className="bili-draft-panel">
          <div>
            <strong id="bili-comment-editor-title">评论正文（可直接手写 / 修改）</strong>
            <span id="bili-comment-editor-help">不用先生成草稿：直接输入 → 保存当前正文 → 确认发送测试评论。保存时会整理空白并自动补齐目标账号的 @，请以保存后的全文为准。保留同账号同视频防重；仅发送到指定测试视频，结果不明确时勿重发。</span>
          </div>
          <textarea
            value={bilibiliDraftText}
            onChange={(event) => setBilibiliDraftText(event.target.value)}
            aria-labelledby="bili-comment-editor-title"
            aria-describedby="bili-comment-editor-help bili-comment-editor-state"
            placeholder="在这里输入你想让莲心发送的话，也可以修改已有草稿。保存后确认，不会自动发送。"
            readOnly={bilibiliDraftBusy || bilibiliSaving}
            maxLength={500}
            rows={4}
          />
          <span id="bili-comment-editor-state" role="status">
            {!bilibiliDraftText.trim() ? "尚未填写正文；AI 生成草稿是可选项。"
              : bilibiliDraftText.trim() !== bilibili?.draft?.text ? "正文尚未保存：请先点击“保存当前正文”，否则不会发送新内容。"
                : bilibili?.draft?.status === "pending_confirmation" ? "当前正文已保存（仅本次运行），确认发送时会再次展示全文。"
                  : "此草稿已尝试发送，请先核对发送记录；如需另写内容，请修改后保存。"}
          </span>
          <div className="bili-interaction-actions">
            <button className="secondary-button" disabled={!bilibiliDraft.testVideo.trim() || bilibiliDraftBusy || bilibiliSaving} onClick={() => void generateBilibiliDraft()}>
              {bilibiliDraftBusy ? "处理中…" : "AI 生成草稿（可选）"}
            </button>
            <button className="secondary-button" disabled={!bilibiliDraft.testVideo.trim() || !bilibiliDraftText.trim() || bilibiliDraftBusy || bilibiliSaving} onClick={() => void generateBilibiliDraft(bilibiliDraftText)}>
              保存当前正文
            </button>
            <button className="secondary-button" disabled={(!bilibili?.draft && !bilibiliDraftText) || bilibiliDraftBusy || bilibiliSaving} onClick={() => void clearBilibiliDraft()}>
              清除草稿
            </button>
          </div>
        </div>
        <div className="bili-interaction-actions">
          <button
            className="secondary-button"
            disabled={!bilibili?.writeCapability.ready || bilibiliSaving || bilibiliDraftBusy}
            title={bilibili?.writeCapability.blockers.join(" ") || "写入能力尚未就绪"}
            onClick={() => void requestBilibiliTest()}
          >
            确认发送测试评论
          </button>
          <button className="secondary-button" disabled={bilibiliSaving} onClick={() => void recheckBilibiliComment()}>
            只读复核最近评论（不重发）
          </button>
        </div>
        </details>
        <div className="bili-interaction-row">
          <div>
            <strong>回复其他网友（尚未就绪）</strong>
            <span>仅限直接回复莲心自己的评论；回复通知读取能力尚未接入。</span>
          </div>
          <button
            className={`toggle ${bilibiliDraft.allowOtherReplies ? "is-on" : ""}`}
            onClick={() =>
              setBilibiliDraft((current) => ({
                ...current,
                allowOtherReplies: !current.allowOtherReplies,
              }))
            }
            disabled
            aria-label="允许回复其他网友"
            aria-pressed={bilibiliDraft.allowOtherReplies}
          >
            <span />
          </button>
        </div>
        <div className="bili-interaction-row">
          <div>
            <strong>暂停互动</strong>
            <span>暂停只影响未来的评论与回复，不关闭只读冲浪。</span>
          </div>
          <button
            className={`toggle ${bilibiliDraft.paused ? "is-on" : ""}`}
            onClick={() =>
              setBilibiliDraft((current) => ({ ...current, paused: !current.paused }))
            }
            aria-label="暂停 B 站互动"
            aria-pressed={bilibiliDraft.paused}
          >
            <span />
          </button>
        </div>
        <div className="bili-capability-status" role="status">
          <strong>手动测试发送：{bilibili?.writeCapability.ready ? "已就绪" : "已封锁"}</strong>
          <span>
            {bilibili?.writeCapability.blockers.join(" ") || "正在读取写入能力状态。"}
          </span>
          <span>主号昵称需先查询并确认；公开资料匹配不等于账号所有权认证。</span>
        </div>
        <div className="bili-interaction-actions">
          <button className="secondary-button" disabled={bilibiliSaving} onClick={() => void saveBilibiliSettings()}>
            {bilibiliSaving ? "保存中…" : "保存互动设置"}
          </button>
        </div>
        <details className="bili-manual-test">
          <summary>最近发送记录与只读复核</summary>
        <p className="muted-copy">小号回读匹配不代表大号可见或 @ 通知送达；未登录最新一页未查到也不代表未发送。复核已有记录即可，不必重新生成草稿或发送。</p>
        {bilibili?.records?.slice(0, 5).map((record, index) => (
          <div key={`${record.rpid}-${index}`} className="muted-copy">
            <span>{record.bvid} · 评论ID {record.rpid || "未知"} · {record.status === "sent_verified" ? "小号回读匹配（非公开可见证明）" : record.status === "sent_unverified" ? "B站已受理，回读未确认" : record.status}</span>
            <p>{record.diagnostic}</p>
            {record.rpid && <button className="secondary-button" disabled={bilibiliSaving} onClick={() => void recheckBilibiliComment(record.rpid)}>只读复核此评论</button>}
          </div>
        ))}
        </details>
        {bilibiliNotice && <p className="muted-copy bili-notice" aria-live="polite">{bilibiliNotice}</p>}
      </section>
      <p className="muted-copy">
        自动评论默认关闭。调试与自动调度共用兴趣标签冲浪链路；开启自动评论且校验通过后可能公开发送一条评论。
      </p>
    </Overlay>
  );
}

function ManagementPanel({
  onClose,
  onVoice,
  onLegacy,
}: {
  onClose: () => void;
  onVoice: () => void;
  onLegacy: (feature: LegacyFeature) => void;
}) {
  const [modules, setModules] = useState<
    Array<{ id: string; label: string; available: boolean }>
  >([]);
  useEffect(() => {
    void lianxinApi
      .managementState()
      .then((result) => setModules(result.modules))
      .catch(() => undefined);
  }, []);
  return (
    <Overlay onClose={onClose}>
      <div className="panel-heading">
        <div>
          <span className="eyebrow">莲心系统</span>
          <h2>管理中心</h2>
        </div>
      </div>
      <div className="module-list">
        {modules.map((module) => {
          const legacy = legacyFeatureIds.has(module.id as LegacyFeature);
          const embedded = [
            "memory-constellation",
            "ripple",
            "data-tide",
            "study-room",
            "time-capsule",
          ].includes(module.id);
          return (
            <button
              className="module-entry"
              key={`${module.id}-${module.label}`}
              onClick={() =>
                module.id === "voice"
                  ? onVoice()
                  : legacy
                    ? onLegacy(module.id as LegacyFeature)
                    : undefined
              }
            >
              <span>
                <span
                  className={`module-dot ${module.available ? "is-ready" : ""}`}
                />
                {module.label}
              </span>
              <span>
                {legacy
                  ? embedded
                    ? "新版界面"
                    : "原版窗口"
                  : module.available
                    ? "可用"
                    : "未连接"}
                <ChevronRight size={15} />
              </span>
            </button>
          );
        })}
      </div>
    </Overlay>
  );
}

function ConstellationPanel({ onClose }: { onClose: () => void }) {
  return (
    <div className="constellation-overlay">
      <div className="constellation-frame-wrap">
        <button className="overlay-close" onClick={onClose}>
          <X size={18} />
        </button>
        <iframe
          className="constellation-frame"
          src="http://127.0.0.1:8766/api/memory-constellation/html"
          title="星图系统"
          allowFullScreen
        />
      </div>
    </div>
  );
}

function RipplePanel({ onClose }: { onClose: () => void }) {
  return (
    <div className="constellation-overlay">
      <div className="constellation-frame-wrap">
        <button className="overlay-close" onClick={onClose}>
          <X size={18} />
        </button>
        <iframe
          className="constellation-frame"
          src="http://127.0.0.1:8766/api/ripple/html"
          title="涟漪情感系统"
          allowFullScreen
        />
      </div>
    </div>
  );
}

function DataTidePanel({ onClose }: { onClose: () => void }) {
  return (
    <div className="constellation-overlay">
      <div className="constellation-frame-wrap">
        <button className="overlay-close" onClick={onClose}>
          <X size={18} />
        </button>
        <iframe
          className="constellation-frame"
          src="http://127.0.0.1:8766/api/data-tide/html"
          title="数据潮汐"
          allowFullScreen
        />
      </div>
    </div>
  );
}

const personaFields = [
  "summary",
  "identity",
  "appearance",
  "personality",
  "speaking_style",
  "habits",
  "relationship",
  "user_address",
  "boundaries",
  "custom_instructions",
];
const personaLabels: Record<string, string> = {
  summary: "一句话简介",
  identity: "身份与背景",
  appearance: "外貌设定",
  personality: "性格设定",
  speaking_style: "说话风格",
  habits: "行为习惯",
  relationship: "与用户的关系",
  user_address: "对用户的称呼",
  boundaries: "关系边界",
  custom_instructions: "高级补充指令",
};
function PersonaPanel({ onClose }: { onClose: () => void }) {
  const [state, setState] = useState<PersonaState | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [status, setStatus] = useState("");
  const load = () =>
    void lianxinApi
      .personaState()
      .then((next) => {
        setState(next);
        setSelectedId((current) => current || String(next.snapshot.profile.id));
      })
      .catch((error) => setStatus(String(error)));
  useEffect(() => {
    load();
  }, []);
  useEffect(() => {
    const profile = state?.profiles.find(
      (item) => String(item.id) === selectedId,
    );
    if (profile)
      setDraft(
        Object.fromEntries(
          ["profile_name", "assistant_name", ...personaFields].map((field) => [
            field,
            String(profile[field] ?? ""),
          ]),
        ),
      );
  }, [state, selectedId]);
  const profile = state?.profiles.find(
    (item) => String(item.id) === selectedId,
  );
  const activeId = String(state?.snapshot.profile.id || "");
  const save = () => {
    if (!profile) return;
    void lianxinApi
      .personaSave({ id: selectedId, ...draft })
      .then((next) => {
        setState(next);
        setStatus("已保存草稿");
      })
      .catch((error) => setStatus(String(error)));
  };
  const activate = () =>
    void lianxinApi
      .personaActivate(selectedId, true)
      .then((next) => {
        setState(next);
        setStatus("已激活，从下一条新请求生效");
      })
      .catch((error) => setStatus(String(error)));
  const create = () => {
    const name = window.prompt("新人格档案名称", "新的人格");
    if (name?.trim())
      void lianxinApi
        .personaCreate(name.trim())
        .then((next) => {
          setState(next);
          setStatus("已创建人格档案");
        })
        .catch((error) => setStatus(String(error)));
  };
  return (
    <div className="constellation-overlay persona-overlay">
      <div className="persona-frame">
        <header className="persona-header">
          <div>
            <span className="eyebrow">莲心系统 · 换壳不换底</span>
            <h2>人格枢控</h2>
            <p>管理人格档案、表达方式与成长轨迹</p>
          </div>
          <div className="persona-header-actions">
            <span
              className={`persona-status ${state?.snapshot.enabled ? "is-on" : ""}`}
            >
              {state?.snapshot.enabled ? "人格系统已启用" : "人格系统已停用"}
            </span>
            <button
              className="secondary-button"
              onClick={() =>
                state &&
                void lianxinApi
                  .personaToggle(!state.snapshot.enabled)
                  .then(setState)
              }
            >
              切换启用
            </button>
            <button className="overlay-close" onClick={onClose}>
              <X size={18} />
            </button>
          </div>
        </header>
        <div className="persona-body">
          <aside className="persona-sidebar">
            <div className="persona-section-title">
              <strong>人格档案</strong>
              <button className="secondary-button" onClick={create}>
                新建
              </button>
            </div>
            <div className="persona-list">
              {(state?.profiles || []).map((item) => (
                <button
                  className={`persona-list-item ${String(item.id) === selectedId ? "is-selected" : ""}`}
                  key={String(item.id)}
                  onClick={() => setSelectedId(String(item.id))}
                >
                  <span>
                    {String(item.id) === activeId ? "● " : ""}
                    {String(item.profile_name || "未命名人格")}
                  </span>
                  <small>{String(item.assistant_name || "莲心")}</small>
                </button>
              ))}
            </div>
            {profile && profile.id !== "default-lianxin" && (
              <button
                className="persona-danger"
                onClick={() =>
                  void lianxinApi.personaDelete(selectedId).then((next) => {
                    setState(next);
                    setSelectedId(String(next.snapshot.profile.id));
                  })
                }
              >
                删除当前档案
              </button>
            )}
          </aside>
          <section className="persona-editor">
            <div className="persona-editor-head">
              <div>
                <span className="eyebrow">档案编辑</span>
                <h3>{String(profile?.profile_name || "选择一个人格")}</h3>
              </div>
              <div className="persona-actions">
                <button
                  className="secondary-button"
                  onClick={save}
                  disabled={!profile}
                >
                  保存草稿
                </button>
                <button
                  className="secondary-button persona-activate"
                  onClick={activate}
                  disabled={!profile || activeId === selectedId}
                >
                  保存并激活
                </button>
              </div>
            </div>
            {profile ? (
              <div className="persona-form">
                <label>
                  档案名称
                  <input
                    value={draft.profile_name || ""}
                    onChange={(event) =>
                      setDraft({ ...draft, profile_name: event.target.value })
                    }
                  />
                </label>
                <label>
                  助手名称
                  <input
                    value={draft.assistant_name || ""}
                    onChange={(event) =>
                      setDraft({ ...draft, assistant_name: event.target.value })
                    }
                  />
                </label>
                {personaFields.map((field) => (
                  <label key={field}>
                    {personaLabels[field]}
                    <textarea
                      value={draft[field] || ""}
                      onChange={(event) =>
                        setDraft({ ...draft, [field]: event.target.value })
                      }
                      rows={
                        field === "summary" || field === "user_address" ? 2 : 4
                      }
                    />
                  </label>
                ))}
              </div>
            ) : (
              <div className="persona-empty">正在读取人格档案…</div>
            )}
          </section>
          <aside className="persona-preview">
            <div className="persona-preview-head">
              <div>
                <span className="eyebrow">最终 Prompt</span>
                <h3>效果预览</h3>
              </div>
              <span>{state?.preview.estimated_tokens || 0} tokens</span>
            </div>
            <div className="persona-layers">
              {(state?.preview.layers || []).map((layer) => (
                <span key={layer}>{layer}</span>
              ))}
            </div>
            <pre>
              {state?.preview.text || "选择人格后显示最终 Prompt 分层预览"}
            </pre>
            <div className="persona-growth">
              <strong>成长轨迹</strong>
              <span>
                {state?.growth.summary
                  ? `当前生效 ${state.growth.summary.counts?.applied || 0} 项 · 本周变化 ${state.growth.summary.weekly_changes || 0} 次`
                  : "暂无成长数据"}
              </span>
            </div>
          </aside>
        </div>
        {status && <div className="persona-toast">{status}</div>}
      </div>
    </div>
  );
}

function StudyRoomPanel({ onClose }: { onClose: () => void }) {
  return (
    <div className="constellation-overlay">
      <div className="constellation-frame-wrap">
        <button className="overlay-close" onClick={onClose}>
          <X size={18} />
        </button>
        <iframe
          className="constellation-frame"
          src="http://127.0.0.1:8766/api/study-room/html"
          title="莲心自习室"
          allowFullScreen
        />
      </div>
    </div>
  );
}

function TimeCapsulePanel({ onClose }: { onClose: () => void }) {
  return (
    <div className="constellation-overlay">
      <div className="constellation-frame-wrap">
        <button className="overlay-close" onClick={onClose}>
          <X size={18} />
        </button>
        <iframe
          className="constellation-frame"
          src="http://127.0.0.1:8766/api/time-capsule/html"
          title="时间胶囊"
          allowFullScreen
        />
      </div>
    </div>
  );
}

function Workspace({
  workspace,
  music,
  onControl,
  onRefreshMusic,
  musicError,
  onDismissMusicError,
}: {
  workspace: WorkspaceId;
  music: MusicState;
  onControl: (action: string, payload?: Record<string, unknown>) => void;
  onRefreshMusic?: () => void;
  musicError?: string;
  onDismissMusicError?: () => void;
}) {
  if (workspace === "music")
    return (
      <MusicWorkspace
        music={music}
        onControl={onControl}
        onRefresh={onRefreshMusic}
        errorMsg={musicError}
        onDismissError={onDismissMusicError}
      />
    );
  const item = primaryNavigation.find((entry) => entry.id === workspace)!;
  return (
    <section className="workspace placeholder-workspace">
      <div className="placeholder-icon">
        <Icon name={item.icon} />
      </div>
      <p className="eyebrow">莲心空间</p>
      <h1>{item.label}</h1>
      <p>这里将逐步接入莲心现有的真实能力与数据。</p>
      <button className="secondary-button">
        <ArrowUp size={15} />
        返回对话
      </button>
    </section>
  );
}
function pickFresherMusicState(next: MusicState, current: MusicState): MusicState {
  const nextAt = Number(next.updatedAt ?? 0);
  const currentAt = Number(current.updatedAt ?? 0);
  // 8765 的 stateVersion 在服务重启后会归零，只比它会把新状态全挡在门外
  // （音乐面板会永久停在旧状态，包括假的"正在播放"）。改用后端每次都会刷新的
  // updatedAt 判新鲜度，仅当两者完全相同时才退回 stateVersion。
  if (nextAt > 0 && currentAt > 0 && nextAt !== currentAt) return nextAt > currentAt ? next : current;
  return (next.stateVersion ?? 0) >= (current.stateVersion ?? 0) ? next : current;
}
export function App() {
  const [activeWorkspace, setActiveWorkspace] = useState<WorkspaceId>("chat");
  const [managementOpen, setManagementOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>(demoMessages);
  const [interactionMessages, setInteractionMessages] = useState<Message[]>([]);
  const [backendOnline, setBackendOnline] = useState(false);
  const [busy, setBusy] = useState(false);
  const [interactionThinking, setInteractionThinking] = useState(false);
  const [proactiveThinking, setProactiveThinking] = useState(false);
  const [panel, setPanel] = useState<PanelId>("none");
  const [music, setMusic] = useState<MusicState>({});
  const [musicError, setMusicError] = useState("");
  const [axis, setAxis] = useState<FiveAxisState>({ axes: {}, mood: "中性" });
  const [voiceActive, setVoiceActive] = useState(false);
  const [voiceInputActive, setVoiceInputActive] = useState(false);
  const [voiceInputText, setVoiceInputText] = useState("");
  const [toolBlocks, setToolBlocks] = useState<ToolRoundState[]>([]);
  // 执行运行时（阶段 2）：任务进度 + 审批卡片
  const [runtimeTask, setRuntimeTask] = useState<ExecutionTaskState | null>(null);
  const [runtimeDeciding, setRuntimeDeciding] = useState(false);
  const runtimeSigRef = useRef("");
  const toolCursorRef = useRef(0);
  const sessionIdRef = useRef<number | null>(null);
  const messagesRef = useRef<Message[]>([]);
  const [background, setBackground] = useState<BackgroundState>({
    enabled: true,
    opacity: 0.22,
    fitMode: "cover",
    fingerprint: "",
  });
  const [avatar, setAvatar] = useState<AvatarState>({
    enabled: true,
    size: 60,
    gap: 10,
    border: true,
    fingerprint: "",
  });
  const [ttsSpeaking, setTtsSpeaking] = useState(false);
  const [ttsStage, setTtsStage] = useState("");
  // 语音合成失败提示行（单引擎语义：失败只提示一行，不自动换声线）
  const [ttsNotice, setTtsNotice] = useState("");
  const ttsNoticeIdRef = useRef(0);
  const [silentMode, setSilentMode] = useState(false);
  // 分段发送：节奏参数由后端设置项提供，分段计划按「回复正文」缓存。
  const [segmentPause, setSegmentPause] = useState({
    chatMin: 0.45,
    chatMax: 1.1,
    semanticMin: 3,
    semanticMax: 7,
  });
  const [segmentPlans, setSegmentPlans] = useState<Record<string, string[]>>({});
  const [segmentShown, setSegmentShown] = useState<Record<string, number>>({});
  const segmentTokenRef = useRef(0);
  useEffect(() => {
    const sync = () =>
      void lianxinApi
        .ttsStatus()
        .then((state) => {
          setTtsSpeaking(state.speaking);
          setTtsStage(state.stage?.text ?? "");
          const notice = state.notice;
          if (notice && notice.id && notice.id !== ttsNoticeIdRef.current) {
            ttsNoticeIdRef.current = notice.id;
            setTtsNotice(notice.text || "莲心的语音合成暂时失败了");
          }
        })
        .catch(() => undefined);
    sync();
    const timer = window.setInterval(sync, 500);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    const sync = () =>
      void lianxinApi
        .settingsPanels()
        .then((state) => {
          setSilentMode(Boolean(state.global.silentMode || state.sound.silentMode));
          const positive = (value: unknown, fallback: number) => {
            const parsed = typeof value === "number" ? value : Number(value);
            return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
          };
          setSegmentPause({
            chatMin: positive(state.global.segmentPauseChatMin, 0.45),
            chatMax: positive(state.global.segmentPauseChatMax, 1.1),
            semanticMin: positive(state.global.segmentPauseSemanticMin, 3),
            semanticMax: positive(state.global.segmentPauseSemanticMax, 7),
          });
        })
        .catch(() => undefined);
    sync();
    const timer = window.setInterval(sync, 1000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    const sync = () =>
      void lianxinApi
        .proactiveStatus()
        .then((state) => setProactiveThinking(Boolean(state.running)))
        .catch(() => undefined);
    sync();
    const timer = window.setInterval(sync, 2000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    document.documentElement.classList.toggle("tts-active", ttsSpeaking);
    // 语音恢复正常后，撤掉上一轮留下的合成失败提示
    if (ttsSpeaking) setTtsNotice("");
  }, [ttsSpeaking]);
  const workspaceLabel = useMemo(
    () =>
      primaryNavigation.find((item) => item.id === activeWorkspace)?.label ??
      "对话",
    [activeWorkspace],
  );
  useEffect(() => {
    let first = true;
    const check = () => {
      void lianxinApi
        .status()
        .then((status) => {
          setBackendOnline(status.online);
          sessionIdRef.current = status.sessionId ?? null;
          if (first && status.sessionId) {
            console.info("[IMAGE_FLOW] status", {
              sessionId: status.sessionId,
              online: status.online,
            });
            void lianxinApi
              .messages(status.sessionId)
              .then((result) => {
                const attachments = result.items.reduce(
                  (count, item) => count + (item.attachments?.length ?? 0),
                  0,
                );
                console.info("[IMAGE_FLOW] restore", {
                  sessionId: status.sessionId,
                  items: result.items.length,
                  attachments,
                });
                if (result.items.length)
                  setMessages(result.items.map(restoreMessage));
              })
              .catch((error) =>
                console.warn("[IMAGE_FLOW] restore failed", error),
              );
          }
          first = false;
        })
        .catch((error) => {
          console.warn("[IMAGE_FLOW] status failed", error);
          setBackendOnline(false);
        });
      void lianxinApi
        .musicState()
        .then((state) => setMusic((current) => {
          const next = state as MusicState;
          return pickFresherMusicState(next, current);
        }))
        .catch(() => undefined);
      void lianxinApi
        .fiveAxis()
        .then(setAxis)
        .catch(() => undefined);
    };
    check();
    const timer = window.setInterval(check, 5000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    let disposed = false;
    let sessionId: number | null = null;
    let cursor = 0;
    const sync = async () => {
      if (busy) return;
      try {
        const status = await lianxinApi.status();
        if (!status.sessionId || disposed) return;
        const changed = sessionId !== status.sessionId;
        if (changed) {
          sessionId = status.sessionId;
          cursor = 0;
        }
        const result = await lianxinApi.messages(status.sessionId, cursor);
        if (disposed) return;
        if (changed && !result.items.length) {
          setMessages([]);
          return;
        }
        if (!result.items.length) return;
        const restored = result.items.map(restoreMessage);
        cursor = Math.max(
          cursor,
          ...result.items.map((item) => Number(item.id || 0)),
        );
        setMessages((current) => {
          const byId = new Map(current.map((message) => [message.id, message]));
          if (changed) byId.clear();
          for (const message of restored) byId.set(message.id, message);
          return Array.from(byId.values()).sort(
            (left, right) => Number(left.id) - Number(right.id),
          );
        });
      } catch {
        /* The existing optimistic chat remains visible while the bridge is unavailable. */
      }
    };
    void sync();
    const timer = window.setInterval(() => void sync(), 1200);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [busy]);
  // 工具信息栏数据源：不论谁发起的对话（主界面 / 桌宠 / 主动消息），
  // 工具调用都会写进后端共享事件流，这里轮询增量合并。
  const applyToolEvents = useCallback((events: ToolActivityEvent[]) => {
    setToolBlocks((current) => {
      const blocks = current.map((block) => ({
        ...block,
        calls: [...block.calls],
      }));
      const indexByKey = new Map(
        blocks.map((block, index) => [block.key, index]),
      );
      let changed = false;
      for (const event of events) {
        const session = sessionIdRef.current;
        if (
          event.sessionId &&
          session &&
          Number(event.sessionId) !== Number(session)
        )
          continue;
        const key = `${event.sessionId ?? 0}:${event.turn ?? 0}:${event.round ?? 0}`;
        let index = indexByKey.get(key);
        if (index === undefined) {
          const snapshot = messagesRef.current;
          // 还没等到本轮回复时挂在队尾；回复已经先到时插到它上面。
          let anchorIndex = snapshot.length;
          if (
            snapshot.length &&
            snapshot[snapshot.length - 1].role === "assistant"
          )
            anchorIndex = snapshot.length - 1;
          blocks.push({
            key,
            turn: Number(event.turn ?? 0),
            round: Number(event.round ?? 0),
            anchorIndex,
            calls: [],
          });
          index = blocks.length - 1;
          indexByKey.set(key, index);
        }
        const calls = blocks[index].calls;
        if (event.kind === "result") {
          const callIndex = calls.findIndex((call) => call.seq === event.seq);
          const patch = {
            status: (event.status === "error" ? "error" : "done") as
              | "done"
              | "error",
            preview: event.preview,
            elapsedMs: event.elapsedMs,
          };
          if (callIndex >= 0)
            calls[callIndex] = { ...calls[callIndex], ...patch };
          else
            calls.push({
              seq: Number(event.seq ?? 0),
              name: event.name ?? "未知工具",
              argsPreview: "",
              ...patch,
            });
        } else {
          calls.push({
            seq: Number(event.seq ?? 0),
            name: event.name ?? "未知工具",
            argsPreview: event.argsPreview ?? "",
            status: "running",
          });
        }
        changed = true;
      }
      return changed ? blocks : current;
    });
  }, []);
  useEffect(() => {
    if (!backendOnline) return;
    let disposed = false;
    const poll = async () => {
      try {
        const result = await lianxinApi.toolEvents(toolCursorRef.current);
        if (disposed || !result.items.length) return;
        toolCursorRef.current = Math.max(
          toolCursorRef.current,
          ...result.items.map((item) => Number(item.id || 0)),
        );
        applyToolEvents(result.items);
      } catch {
        /* 后端尚未就绪时保持安静 */
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 800);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [backendOnline, applyToolEvents]);
  // 执行运行时：只在"状态真的变了"时更新，避免每秒无意义重渲染。
  useEffect(() => {
    if (!backendOnline) return;
    let disposed = false;
    const poll = async () => {
      try {
        const state = await lianxinApi.runtimeState();
        if (disposed) return;
        const signature = JSON.stringify([
          state.running,
          state.task?.rounds ?? 0,
          state.pendingApprovals.map((item) => item.requestId).join(","),
          state.lastResult?.finishedAt ?? 0,
          state.lastResult?.status ?? "",
        ]);
        if (signature === runtimeSigRef.current) return;
        runtimeSigRef.current = signature;
        setRuntimeTask(state);
      } catch {
        /* 后端尚未就绪时保持安静 */
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1500);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [backendOnline]);
  const approveRuntime = useCallback(
    async (
      requestId: string,
      decision: "accept" | "acceptForSession" | "decline",
    ) => {
      if (!requestId) return;
      setRuntimeDeciding(true);
      try {
        await lianxinApi.runtimeApprove(
          requestId,
          decision,
          decision === "acceptForSession",
        );
        runtimeSigRef.current = "";
        setRuntimeTask(await lianxinApi.runtimeState());
      } catch {
        /* 审批失败保持安静，下一轮轮询会重新拉状态 */
      } finally {
        setRuntimeDeciding(false);
      }
    },
    [],
  );
  const stopRuntime = useCallback(async () => {
    try {
      await lianxinApi.runtimeStop("user");
      runtimeSigRef.current = "";
      setRuntimeTask(await lianxinApi.runtimeState());
    } catch {
      /* 忽略 */
    }
  }, []);
  useEffect(() => {
    document.documentElement.style.setProperty(
      "--lx-chat-mask",
      String(background.chatOpacity ?? 0.75),
    );
  }, [background.chatOpacity]);
  useEffect(() => {
    const onClick = (event: globalThis.MouseEvent) => {
      const button = (event.target as HTMLElement).closest("button");
      if (!button) return;
      const label = `${button.title} ${button.textContent || ""} ${button.className}`;
      if (/发送|send-button/.test(label)) return;
      let sound = "ButtonAll.mp3";
      if (/工具|图片|文件|附件|composer-tool/.test(label))
        sound = "ToolBox1.mp3";
      else if (/备忘/.test(label)) sound = "MemoBook.mp3";
      else if (/音乐|music-|播放|上一首|下一首/.test(label))
        sound = "ButtonMusic.mp3";
      else if (/拍一拍|摸一摸/.test(label)) sound = "拍一拍.mp3";
      void lianxinApi.playSound(sound).catch(() => undefined);
    };
    document.addEventListener("click", onClick, true);
    return () => document.removeEventListener("click", onClick, true);
  }, []);
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
          setBackground((current) => ({
            ...current,
            enabled: next.enabled,
            opacity: next.opacity,
            chatOpacity: next.chatOpacity,
            fitMode: next.fitMode,
          }));
        }
      } catch {
        /* The bundled fallback remains visible while the API is offline. */
      }
    };
    void sync(true);
    const timer = window.setInterval(() => void sync(false), 800);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
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
          setAvatar((current) => ({
            ...current,
            enabled: next.enabled,
            size: next.size,
            gap: next.gap,
            border: next.border,
          }));
        }
      } catch {
        /* Keep the bundled avatar while the API is unavailable. */
      }
    };
    void sync(true);
    const timer = window.setInterval(() => void sync(false), 2500);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [avatar.fingerprint]);
  const openSession = (id: number) => {
    fastForwardSegments();
    return void lianxinApi
      .selectSession(id)
      .then(() => lianxinApi.messages(id))
      .then((result) => {
        setActiveWorkspace("chat");
        setInteractionMessages([]);
        setToolBlocks([]);
        setMessages(result.items.map(restoreMessage));
      })
      .catch(() => undefined);
  };
  // 分段计划的键：折叠空白后比较，避免落库副本与本地乐观消息因空白差异失配。
  const segmentKeyOf = (content: string) =>
    (content || "").replace(/\s+/g, " ").trim();

  const segmentSleep = (ms: number) =>
    new Promise<void>((resolve) => window.setTimeout(resolve, ms));

  // 一条回复该不该分段、按哪种节奏分。不足两段时返回 null，走原来的整条发送。
  const buildSegmentPlan = (value: string) => {
    const text = (value || "").trim();
    if (!text) return null;
    const conversational = shouldSendAsConversation(text);
    const parts = conversational
      ? splitConversationText(text)
      : splitSemanticText(text);
    if (parts.length < 2) return null;
    return { key: segmentKeyOf(text), parts, conversational };
  };

  const waitForSpeechIdle = async (token: number, budgetMs: number) => {
    const deadline = Date.now() + budgetMs;
    while (Date.now() < deadline) {
      if (token !== segmentTokenRef.current) return;
      await segmentSleep(200);
      try {
        const state = await lianxinApi.ttsStatus();
        if (!state.speaking) return;
      } catch {
        return;
      }
    }
  };

  // 逐段上屏 + 逐段朗读，段间停顿读设置项。token 变化即中断。
  const revealSegments = async (plan: {
    key: string;
    parts: string[];
    conversational: boolean;
  }) => {
    const token = segmentTokenRef.current + 1;
    segmentTokenRef.current = token;
    setSegmentPlans((current) => ({ ...current, [plan.key]: plan.parts }));
    setSegmentShown((current) => ({ ...current, [plan.key]: 1 }));
    const bounds = plan.conversational
      ? [segmentPause.chatMin, segmentPause.chatMax]
      : [segmentPause.semanticMin, segmentPause.semanticMax];
    const lowMs = Math.max(
      100,
      Math.round(Math.min(bounds[0], bounds[1]) * 1000),
    );
    const highMs = Math.max(
      lowMs,
      Math.round(Math.max(bounds[0], bounds[1]) * 1000),
    );
    for (let index = 0; index < plan.parts.length; index += 1) {
      if (token !== segmentTokenRef.current) return;
      const segment = plan.parts[index];
      let speaking = false;
      try {
        const result = await lianxinApi.speak(segment);
        speaking = Boolean(result?.speaking);
      } catch {
        speaking = false;
      }
      if (index === plan.parts.length - 1) return;
      if (speaking) {
        await waitForSpeechIdle(
          token,
          Math.min(60000, 1500 + segment.length * 220),
        );
      }
      if (token !== segmentTokenRef.current) return;
      await segmentSleep(
        lowMs + Math.round(Math.random() * Math.max(0, highMs - lowMs)),
      );
      if (token !== segmentTokenRef.current) return;
      setSegmentShown((current) => ({ ...current, [plan.key]: index + 2 }));
    }
  };

  // 用户催到了（新消息 / 取消）：剩余段落立刻全部上屏，不丢内容。
  const fastForwardSegments = () => {
    segmentTokenRef.current += 1;
    setSegmentShown(() => {
      const next: Record<string, number> = {};
      for (const [key, parts] of Object.entries(segmentPlans))
        next[key] = parts.length;
      return next;
    });
  };

  // 渲染用：把一条 assistant 消息摊成若干条气泡。带附件/图片的整条保留。
  const segmentBubblesFor = (message: Message): string[] => {
    const content = message.content ?? "";
    if (message.role !== "assistant") return [content];
    if (message.kind && message.kind !== "text") return [content];
    if (message.imageUrl || message.attachments?.length) return [content];
    const key = segmentKeyOf(content);
    const parts = segmentPlans[key];
    if (!parts) return [content];
    const shown = segmentShown[key] ?? parts.length;
    return parts.slice(0, Math.max(1, Math.min(shown, parts.length)));
  };

  const sendQueueRef = useRef(Promise.resolve());
  const pendingSendsRef = useRef(0);

  const processSend = async (
    text: string,
    attachments: Attachment[] = [],
    selection: { forcedTool?: string; preferredTool?: string } = {},
    quote?: { role: "user" | "assistant"; content: string },
  ) => {
    const messageTime = "现在";
    const imageIds = attachments.map(
      (_, index) => `image-${Date.now()}-${index}`,
    );
    void lianxinApi.playSound("ButtonAll.mp3").catch(() => undefined);
    void lianxinApi.stopSpeaking().catch(() => undefined);
    setMessages((current) => [
      ...current,
      {
        id: `user-${Date.now()}`,
        role: "user",
        content: text,
        time: messageTime,
      },
      ...attachments.map((item, index) =>
        item.kind === "image"
          ? {
              id: imageIds[index],
              role: "user" as const,
              content: "",
              time: messageTime,
              imageUrl: item.dataUrl,
              imageName: item.fileName,
              imageStatus: "pending" as const,
            }
          : {
              id: imageIds[index],
              role: "user" as const,
              content: "",
              time: messageTime,
              kind: "file" as const,
              fileName: item.fileName,
              fileSize: item.size,
            },
      ),
    ]);
    if (!backendOnline) return;
    try {
      let reply = "";
      for await (const event of lianxinApi.streamChat(
        text,
        attachments,
        selection,
        quote,
      )) {
        if (event.type === "delta") reply += event.content ?? "";
        if (
          event.type === "image_analysis_result" &&
          typeof event.index === "number"
        ) {
          const imageId = imageIds[event.index];
          setMessages((current) =>
            current.map((message) =>
              message.id === imageId
                ? {
                    ...message,
                    imageStatus: event.error ? "error" : "success",
                    imageDescription: event.description,
                  }
                : message,
            ),
          );
        }
        if (event.type === "completed") reply = event.message?.content ?? reply;
        if (event.type === "error")
          throw new Error(event.error || "聊天请求失败");
      }
      setMessages((current) => [
        ...current,
        {
          id: `assistant-${Date.now()}`,
          role: "assistant",
          content: reply,
          time: "现在",
        },
      ]);
      if (reply.trim()) {
        void lianxinApi.playSound("lianxinSend.mp3").catch(() => undefined);
        const plan = buildSegmentPlan(reply);
        if (plan) await revealSegments(plan);
        else void lianxinApi.speak(reply).catch(() => undefined);
      }
    } catch (error) {
      setMessages((current) => [
        ...current,
        {
          id: `error-${Date.now()}`,
          role: "assistant",
          content: `后端连接失败：${String(error)}`,
          time: "现在",
        },
      ]);
    }
  };
  const handleSend = (
    text: string,
    attachments: Attachment[] = [],
    selection: { forcedTool?: string; preferredTool?: string } = {},
    quote?: { role: "user" | "assistant"; content: string },
  ) => {
    // 新一轮用户消息：把上一轮还没播完的分段立刻补全，避免内容被跳过。
    fastForwardSegments();
    pendingSendsRef.current += 1;
    setBusy(true);
    sendQueueRef.current = sendQueueRef.current
      .then(() => processSend(text, attachments, selection, quote))
      .catch((error) => {
        console.error("[CHAT_QUEUE] failed", error);
      })
      .finally(() => {
        pendingSendsRef.current -= 1;
        if (pendingSendsRef.current <= 0) {
          pendingSendsRef.current = 0;
          setBusy(false);
        }
      });
  };
  useEffect(() => {
    let cursor = 0;
    const poll = () =>
      void lianxinApi
        .voiceEvents(cursor)
        .then((result) => {
          cursor = result.latest;
          for (const event of result.items) {
            if (event.type === "voice.input") {
              if (event.content) setVoiceInputText(event.content);
              setVoiceInputActive(false);
            }
          }
        })
        .catch(() => undefined);
    const timer = window.setInterval(poll, 700);
    return () => window.clearInterval(timer);
  }, [backendOnline]);
  useEffect(() => {
    const open = () => setPanel("tools");
    window.addEventListener("open-tools", open);
    return () => window.removeEventListener("open-tools", open);
  }, []);
  const handleCancel = () => {
    fastForwardSegments();
    void lianxinApi.cancelChat();
    setBusy(false);
  };
  // 工具信息栏永不随对话清空，只有重启莲心或新建/切换会话时才消失。
  messagesRef.current = messages;
  const handleNewChat = () => {
    fastForwardSegments();
    setMessages([]);
    setInteractionMessages([]);
    setToolBlocks([]);
    if (backendOnline) void lianxinApi.newSession().catch(() => undefined);
  };
  const toggleVoice = () => {
    if (!voiceActive)
      void lianxinApi
        .startVoice()
        .then(() => setVoiceActive(true))
        .catch((error) => window.alert(String(error)));
    else void lianxinApi.stopVoice().then(() => setVoiceActive(false));
  };
  const toggleVoiceInput = () => {
    if (!voiceInputActive)
      void lianxinApi
        .startVoiceInput()
        .then(() => setVoiceInputActive(true))
        .catch((error) => window.alert(String(error)));
    else
      void lianxinApi
        .stopVoiceInput()
        .then(() => setVoiceInputActive(false))
        .catch(() => undefined);
  };
  const toggleMute = () => {
    const next = !silentMode;
    setSilentMode(next);
    if (next) void lianxinApi.stopSpeaking().catch(() => undefined);
    void lianxinApi.saveSettingsPanels({ global: { silentMode: next }, sound: { silentMode: next } }).catch(() => setSilentMode(!next));
  };
  const openLegacy = (feature: LegacyFeature) => {
    if (feature === "video-call") {
      setPanel("video-call");
      return;
    }
    if (feature === "memory-constellation") {
      setPanel("constellation");
      return;
    }
    if (feature === "ripple") {
      setPanel("ripple");
      return;
    }
    if (feature === "persona") {
      setPanel("persona");
      return;
    }
    if (feature === "data-tide") {
      setPanel("data-tide");
      return;
    }
    if (feature === "study-room") {
      setPanel("study-room");
      return;
    }
    if (feature === "time-capsule") {
      setPanel("time-capsule");
      return;
    }
    setPanel("none");
    void lianxinApi
      .openLegacy(feature)
      .catch((error) => window.alert(`原版窗口启动失败：${String(error)}`));
  };
  const handleWorkspaceChange = (workspace: WorkspaceId) => {
    const feature =
      workspace === "time-capsule"
        ? "time-capsule"
        : workspace === "prism-memory"
          ? "prism-memory"
          : workspace === "study-room"
            ? "study-room"
            : undefined;
    if (feature) {
      openLegacy(feature);
      return;
    }
    setActiveWorkspace(workspace);
  };
  const controlMusic = (action: string, payload?: Record<string, unknown>) => {
    setMusic((prev) => {
      const next = { ...prev };
      if (action === "toggle") next.active = !next.active;
      else if (action === "play") next.active = true;
      else if (action === "pause") next.active = false;
      else if (
        action === "next" ||
        action === "previous" ||
        action === "select"
      )
        next.active = true;
      return next;
    });
    void lianxinApi
      .musicControl(action, payload)
      .then((state) => {
        setMusic((current) => {
          const next = state as MusicState;
          return pickFresherMusicState(next, current);
        });
        setMusicError("");
      })
      .catch((error) =>
        setMusicError((() => {
          const raw = String((error as Error)?.message ?? "音乐控制失败");
          if (/VIP|copyright|会员|版权/i.test(raw)) return "这首歌曲需要 VIP 或暂无版权，已尝试跳过";
          if (/timed out|timeout|超时/i.test(raw)) return "音乐服务响应超时，请稍后重试";
          if (/offline|未启动|连接/i.test(raw)) return "网易云播放器未连接，请先启动音乐服务";
          if (/queue|队列/i.test(raw)) return "播放队列暂未同步完成，请稍后重试";
          return raw;
        })()),
      );
  };
  useEffect(() => {
    setMessages((current) => {
      const compacted: Message[] = [];
      for (const message of current) {
        const previous = compacted.at(-1);
        if (
          message.kind === "interaction" &&
          previous?.kind === "interaction" &&
          previous.content === message.content
        )
          continue;
        compacted.push(message);
      }
      return compacted.length === current.length ? current : compacted;
    });
  }, [messages.length]);
  const handleAvatarInteraction = (action: "tap" | "headpat") => {
    if (interactionThinking || busy) return;
    const actionLabel =
      action === "headpat" ? "\u6478\u4e86\u6478" : "\u62cd\u4e86\u62cd";
    setMessages((current) => [
      ...current,
      {
        id: `interaction-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        role: "assistant",
        kind: "interaction",
        content: `\u4f60${actionLabel}\u83b2\u5fc3`,
        time: "\u73b0\u5728",
      },
    ]);
    setInteractionThinking(true);
    void lianxinApi
      .avatarAction(action)
      .then((result) => {
        if (!result.accepted) return;
        if (result.counterAction === action) {
          const counterLabel = action === "headpat" ? "摸了摸" : "拍了拍";
          window.setTimeout(() => {
            window.dispatchEvent(new CustomEvent("avatar-counter-action"));
            setMessages((current) => [
              ...current,
              {
                id: `counter-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                role: "assistant",
                kind: "interaction",
                content: `莲心反手${counterLabel}了你`,
                time: "现在",
              },
            ]);
          }, 2000);
        }
        const response = result.response;
        if (response) {
          const appendResponse = () =>
            setMessages((current) => [
              ...current,
              {
                id: `avatar-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                role: "assistant",
                content: response,
                time: "现在",
              },
            ]);
          window.setTimeout(
            appendResponse,
            result.counterAction === action ? 2000 : 0,
          );
        }
      })
      .catch(() => undefined)
      .finally(() => setInteractionThinking(false));
  };
  const startWindowDragging = () => {
    if (desktopWindow)
      void desktopWindow
        .startDragging()
        .catch((error) => console.warn("窗口拖拽失败", error));
  };
  const handleResize = (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    if (desktopWindow)
      void desktopWindow
        .startResizeDragging("SouthEast")
        .catch(() => undefined);
  };
  const handleMinimize = () => {
    if (desktopWindow) void desktopWindow.minimize().catch(() => undefined);
  };
  const handleMaximize = () => {
    if (desktopWindow)
      void desktopWindow.toggleMaximize().catch(() => undefined);
  };
  const handleClose = () => {
    if (desktopWindow) void desktopWindow.close().catch(() => undefined);
  };
  useEffect(() => {
    const handleEmbeddedWindowMessage = (event: MessageEvent) => {
      const data = event.data as { source?: string; action?: string } | null;
      if (!data || data.source !== "lianxin-embedded-window") return;
      if (data.action === "minimize") handleMinimize();
      else if (data.action === "maximize") handleMaximize();
      else if (data.action === "close") setPanel("none");
      else if (data.action === "drag") startWindowDragging();
    };
    window.addEventListener("message", handleEmbeddedWindowMessage);
    return () =>
      window.removeEventListener("message", handleEmbeddedWindowMessage);
  });
  const wallpaperStyle = {
    opacity: background.enabled
      ? Math.max(0, Math.min(1, background.opacity))
      : 0,
    ...(background.dataUrl
      ? {
          backgroundImage: `url(${background.dataUrl})`,
          backgroundSize:
            background.fitMode === "stretch" ? "100% 100%" : background.fitMode,
        }
      : {}),
  };
  const selectedTools = (() => {
    try {
      return JSON.parse(
        window.localStorage.getItem("lianxin-tool-selection") || "{}",
      );
    } catch {
      return {};
    }
  })();
  if (activeWorkspace === "time-capsule")
    return (
      <div className="app-shell">
        <div
          className="wallpaper"
          style={{ opacity: background.enabled ? 1 : 0 }}
        >
          <div className="wallpaper-image" style={wallpaperStyle} />
        </div>
        <div className="app-surface">
          <TopBar
            onNewChat={handleNewChat}
            onPanel={setPanel}
            onLegacy={openLegacy}
            onMinimize={handleMinimize}
            onMaximize={handleMaximize}
            onClose={handleClose}
          />
          <div className="app-body">
            <SideNavigation
              activeWorkspace={activeWorkspace}
              onWorkspaceChange={handleWorkspaceChange}
              managementOpen={managementOpen}
              onManagementToggle={() => setManagementOpen((open) => !open)}
              onPanel={setPanel}
              onLegacy={openLegacy}
              avatar={avatar}
            />
            <main className="main-stage">
              <TimeCapsuleWorkspace />
            </main>
            <CompanionRail
              online={backendOnline}
              music={music}
              onControl={controlMusic}
              axis={axis}
              avatar={avatar}
            />
          </div>
        </div>
      </div>
    );
  if (panel === "video-call")
    return (
      <VideoCallPanel
        onClose={() => setPanel("none")}
        userAvatar={avatar.userDataUrl}
      />
    );
  if (panel === "tools")
    return (
      <ToolPanel
        selected={selectedTools}
        onSelect={(selection) => {
          window.localStorage.setItem(
            "lianxin-tool-selection",
            JSON.stringify(selection),
          );
          window.dispatchEvent(
            new CustomEvent("tool-selection", { detail: selection }),
          );
        }}
        onClose={() => setPanel("none")}
      />
    );
  return (
    <div className="app-shell">
      <div
        className="wallpaper"
        style={{ opacity: background.enabled ? 1 : 0 }}
      >
        <div className="wallpaper-image" style={wallpaperStyle} />
      </div>
      <div className="app-surface">
        <TopBar
          onNewChat={handleNewChat}
          onPanel={setPanel}
          onLegacy={openLegacy}
          onMinimize={handleMinimize}
          onMaximize={handleMaximize}
          onClose={handleClose}
        />
        <div className="app-body">
          <SideNavigation
            activeWorkspace={activeWorkspace}
            onWorkspaceChange={handleWorkspaceChange}
            managementOpen={managementOpen}
            onManagementToggle={() => setManagementOpen((open) => !open)}
            onPanel={setPanel}
            onLegacy={openLegacy}
            avatar={avatar}
          />
          <main className="main-stage">
            <div className="workspace-title-mobile">
              <Menu size={17} />
              {workspaceLabel}
            </div>
            {activeWorkspace === "chat" ? (
              <ChatWorkspace
                messages={messages}
                segmentBubblesFor={segmentBubblesFor}
                toolRounds={toolBlocks}
                avatar={avatar}
                sessionId={sessionIdRef.current ?? undefined}
                onSend={handleSend}
                onInteraction={handleAvatarInteraction}
                interactionThinking={interactionThinking}
                proactiveThinking={proactiveThinking}
                busy={busy}
                voiceInputActive={voiceInputActive}
                onVoiceInput={toggleVoiceInput}
                voiceInputText={voiceInputText}
                ttsSpeaking={ttsSpeaking}
                ttsStage={ttsStage}
                ttsNotice={ttsNotice}
                silentMode={silentMode}
                onToggleMute={toggleMute}
                onOpenNote={() => openLegacy("note")}
                runtimeTask={runtimeTask}
                onRuntimeApprove={(requestId, decision) =>
                  void approveRuntime(requestId, decision)
                }
                onRuntimeStop={() => void stopRuntime()}
                runtimeDeciding={runtimeDeciding}
              />
            ) : (
              <Workspace
                workspace={activeWorkspace}
                music={music}
                onControl={controlMusic}
                onRefreshMusic={() => { void lianxinApi.musicState().then((state) => setMusic((current) => { const next = state as MusicState; return pickFresherMusicState(next, current); })); }}
                musicError={musicError}
                onDismissMusicError={() => setMusicError("")}
              />
            )}
          </main>
          <CompanionRail
            online={backendOnline}
            music={music}
            onControl={controlMusic}
            axis={axis}
            avatar={avatar}
          />
        </div>
      </div>
      <button
        className="resize-grip"
        title="调整窗口大小"
        onMouseDown={handleResize}
        aria-label="调整窗口大小"
      />
      {panel === "history" && (
        <HistoryPanel onClose={() => setPanel("none")} onOpen={openSession} />
      )}
      {panel === "note" && <NotePanel onClose={() => setPanel("none")} />}
      {panel === "tasks" && <TaskPanel onClose={() => setPanel("none")} />}
      {panel === "voice" && (
        <VoicePanel
          active={voiceActive}
          onClose={() => setPanel("none")}
          onToggle={toggleVoice}
        />
      )}
      {panel === "proactive" && (
        <ProactivePanel onClose={() => setPanel("none")} />
      )}
      {panel === "constellation" && (
        <ConstellationPanel onClose={() => setPanel("none")} />
      )}
      {panel === "ripple" && <RipplePanel onClose={() => setPanel("none")} />}
      {panel === "persona" && <PersonaPanel onClose={() => setPanel("none")} />}
      {panel === "data-tide" && (
        <DataTidePanel onClose={() => setPanel("none")} />
      )}
      {panel === "study-room" && (
        <StudyRoomPanel onClose={() => setPanel("none")} />
      )}
      {panel === "time-capsule" && (
        <TimeCapsulePanel onClose={() => setPanel("none")} />
      )}
      {panel === "management" && (
        <ManagementPanel
          onClose={() => setPanel("none")}
          onVoice={() => setPanel("voice")}
          onLegacy={openLegacy}
        />
      )}
    </div>
  );
}
