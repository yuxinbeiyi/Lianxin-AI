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
import { primaryNavigation, demoMessages } from "./data/demo";
import { managementGroups } from "./managementGroups";
import {
  shouldSendAsConversation,
  splitConversationText,
} from "./utils/textSegmentation";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { playSpeechSequence } from "./utils/speechSequence";
import { ExecutionNotices } from "./components/ExecutionNotices";
import { MemoryActivationCard } from "./components/MemoryActivationCard";
import { createMemoryActivationToggle } from "./utils/memoryActivation";
import { showExecutionNotice } from "./utils/executionNotices";
import { executionScope, executionSignature, mergePersistedMessages } from "./utils/executionControls";
import {
  lianxinApi,
  type AvatarState,
  type BackgroundState,
  type ChatStreamEvent,
  type LegacyFeature,
  type ExecutionTaskState,
  type ToolActivityEvent,
  type ChatRequestMetadata,
  type MemoryActivationDebug,
} from "./services/lianxinApi";
import type { Message, WorkspaceId } from "./types/ui";
import { ToolPanel } from "./components/ToolPanel";
import { PersonaPanel as PersonaPanelView } from "./components/PersonaPanel";
import { HistoryPanel } from "./components/HistoryPanel";
import { GlobalSettingsPanel } from "./components/GlobalSettingsPanel";
import { ApiConfigPanel } from "./components/ApiConfigPanel";
import { ProactivePanel } from "./components/ProactivePanel";
import { CapabilityPanel } from "./components/CapabilityPanel";
import { SttPanel } from "./components/SttPanel";
import { SoundPanel } from "./components/SoundPanel";
import { NetworkPanel } from "./components/NetworkPanel";
import { listen } from "@tauri-apps/api/event";
import { applyPreferredTool, CAPABILITY_TOOL_EVENT } from "./utils/capabilityWindow";
import { DecisionPanel } from "./components/DecisionPanel";
import { ExecutionPanel } from "./components/ExecutionPanel";
import { ExecutionCard } from "./components/ExecutionCard";
import { TimeCapsuleWorkspace } from "./components/TimeCapsuleWorkspace";
import { MusicWorkspace } from "./components/MusicWorkspace";
import { VideoCallPanel } from "./components/VideoCallPanel";
import { BilibiliMessageLine } from "./components/BilibiliMessageLine";
import { VisionPanel } from "./components/VisionPanel";
import { openVisionWindow } from "./utils/visionWindow";
import { isSettingsWindowId, openSettingsWindow } from "./utils/settingsWindow";
import { SocialChatPanel } from "./components/SocialChatPanel";

import { AlarmPanel } from "./components/AlarmPanel";
import { AlarmNotices } from "./components/AlarmNotices";

type PanelId =
  | "none"
  | "history"
  | "settings"
  | "api"
  | "note"
  | "voice"
  | "video-call"
  | "proactive"
  | "capability"
  | "voice-stt"
  | "sound"
  | "network"
  | "alarm"
  | "qq"
  | "wechat"
  | "management"
  | "tools"
  | "decision"
  | "constellation"
  | "ripple"
  | "persona"
  | "data-tide"
  | "study-room"
  | "time-capsule"
  | "vision";
type MusicState = {
  roaming?: { active?: boolean; error?: string };
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
  category?: "tool" | "decision" | "memory_activation";
  activation?: MemoryActivationDebug;
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
    executionTurnId?: string;
    executionStatus?: string;
    executionGoal?: string;
    executionRounds?: number;
    executionElapsed?: number;
    executionReason?: string;
    executionApprovalReason?: string;
    executionSummary?: string;
    executionNarrated?: boolean;
    executionGoalProgress?: import("./services/lianxinApi").ExecutionGoalProgress | null;
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
            turnId: item.executionTurnId,
            status: item.executionStatus,
            goal: item.executionGoal,
            rounds: item.executionRounds,
            elapsed: item.executionElapsed,
            reason: item.executionReason,
            approvalReason: item.executionApprovalReason,
            summary: item.executionSummary,
            narrated: item.executionNarrated,
            goalProgress: item.executionGoalProgress,
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
  闹钟与提醒: "alarm",
  历史记录: "history",
  备忘本: "note",
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
  主动聊天: MessageCircle,
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
  searchQuery,
  searchMatchCount,
  searchActiveIndex,
  onSearchQueryChange,
  onSearchPrevious,
  onSearchNext,
  onSearchClear,
}: {
  onNewChat: () => void;
  onPanel: (panel: PanelId) => void;
  onLegacy: (feature: LegacyFeature) => void;
  onMinimize: () => void;
  onMaximize: () => void;
  onClose: () => void;
  searchQuery: string;
  searchMatchCount: number;
  searchActiveIndex: number;
  onSearchQueryChange: (value: string) => void;
  onSearchPrevious: () => void;
  onSearchNext: () => void;
  onSearchClear: () => void;
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
        <input
          type="search"
          value={searchQuery}
          placeholder={"\u641c\u7d22\u5f53\u524d\u5bf9\u8bdd"}
          aria-label={"\u641c\u7d22\u5f53\u524d\u5bf9\u8bdd"}
          data-tauri-drag-region="false"
          onChange={(event) => onSearchQueryChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              if (event.shiftKey) onSearchPrevious();
              else onSearchNext();
            } else if (event.key === "Escape") {
              event.preventDefault();
              onSearchClear();
            }
          }}
        />
        {searchQuery.trim() ? (
          <>
            <span className="search-count">
              {searchMatchCount ? `${searchActiveIndex + 1} / ${searchMatchCount}` : "\u65e0\u7ed3\u679c"}
            </span>
            <button className="search-nav-button" type="button" title={"\u4e0a\u4e00\u4e2a\u5339\u914d"} onClick={onSearchPrevious} disabled={!searchMatchCount}>
              <ArrowUp size={13} />
            </button>
            <button className="search-nav-button" type="button" title={"\u4e0b\u4e00\u4e2a\u5339\u914d"} onClick={onSearchNext} disabled={!searchMatchCount}>
              <ArrowDown size={13} />
            </button>
            <button className="search-nav-button" type="button" title={"\u6e05\u9664\u641c\u7d22"} onClick={onSearchClear}>
              <X size={13} />
            </button>
          </>
        ) : (
          <kbd>Ctrl K</kbd>
        )}
      </div>
      <nav className="top-actions">
        <button className="top-action" onClick={() => onPanel("history")}>
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
        <button className="mode-button" onClick={() => onLegacy("live2d")}>
          <Sparkles size={16} />
          莲心 Live2D
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

function formatPresenceDuration(totalSeconds: number | null): string {
  if (totalSeconds === null) return "今日陪伴暂不可用";
  const minutes = Math.max(0, Math.floor(totalSeconds / 60));
  if (minutes < 60) return `今日陪伴 ${minutes}分钟`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return `今日陪伴 ${hours}小时${remainder ? ` ${remainder}分钟` : ""}`;
}

function PresenceSummary() {
  const [now, setNow] = useState(() => new Date());
  const [todaySeconds, setTodaySeconds] = useState<number | null>(null);

  const refresh = useCallback(() => {
    void lianxinApi
      .dataTideState()
      .then((state) => setTodaySeconds(Number(state.metrics?.today_seconds ?? 0)))
      .catch(() => setTodaySeconds(null));
  }, []);

  useEffect(() => {
    const clock = window.setInterval(() => setNow(new Date()), 1000);
    const stats = window.setInterval(refresh, 60000);
    refresh();
    window.addEventListener("focus", refresh);
    return () => {
      window.clearInterval(clock);
      window.clearInterval(stats);
      window.removeEventListener("focus", refresh);
    };
  }, [refresh]);

  return (
    <div className="presence-summary" aria-label="莲心陪伴信息">
      <strong className="presence-time">
        {now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}
      </strong>
      <span className="presence-date">
        {now.toLocaleDateString("zh-CN", { year: "numeric", month: "long", day: "numeric" })}
      </span>
      <span className="presence-duration">{formatPresenceDuration(todaySeconds)}</span>
    </div>
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
        <button className="nav-item" onClick={() => onPanel("decision")}>
          <BrainCircuit size={19} strokeWidth={1.8} aria-hidden="true" />
          <span>决策中枢</span>
        </button>
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
        <PresenceSummary />
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
  searchActive = false,
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
  searchActive?: boolean;
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
        data-message-id={String(message.id).split("#")[0]}
        className={`message-row ${message.role === "user" ? "user-row" : "assistant-row"} ${searchActive ? "search-match is-search-active" : ""}`}
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
                <p key={`${message.id}-text-${index}`}><BilibiliMessageLine text={line} /></p>
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
        data-message-id={String(message.id).split("#")[0]}
        className={`message-row ${message.role === "user" ? "user-row" : "assistant-row"} ${searchActive ? "search-match is-search-active" : ""}`}
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
      data-message-id={String(message.id).split("#")[0]}
      className={`message-row ${message.role === "user" ? "user-row" : "assistant-row"} ${searchActive ? "search-match is-search-active" : ""}`}
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
            ><BilibiliMessageLine text={line} /></p>
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
        round.category === "memory_activation" && round.activation ? (
          <MemoryActivationCard key={round.key} data={round.activation} />
        ) :
        <section className="tool-round" key={round.key}>
          <div className="tool-round-heading">
            <span>
              {round.category === "decision" ? <BrainCircuit size={14} /> : <Wrench size={14} />}
              {round.category === "decision" ? "莲心调用决策模型" : `莲心执行工具 · 第 ${round.round} 轮`}
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
                    ? round.category === "decision" ? "正在请求决策…" : "正在执行…"
                    : call.status === "error"
                      ? `${round.category === "decision" ? "调用失败" : "执行失败"}${call.elapsedMs ? ` · ${(call.elapsedMs / 1000).toFixed(1)}s` : ""}`
                      : `${round.category === "decision" ? "响应已校验" : "已完成"}${call.elapsedMs ? ` · ${(call.elapsedMs / 1000).toFixed(1)}s` : ""}`}
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
  backendOnline,
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
  onRuntimeSteer,
  runtimeDeciding,
  searchTargetId,
  composerResetKey,
}: {
  messages: Message[];
  segmentBubblesFor: (message: Message) => string[];
  backendOnline: boolean;
  toolRounds: ToolRoundState[];
  avatar?: AvatarState;
  sessionId?: number;
  onSend: (
    text: string,
    attachments: Attachment[],
    selection?: { forcedTool?: string; preferredTool?: string },
    quote?: { role: "user" | "assistant"; content: string },
    requestMetadata?: ChatRequestMetadata,
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
  onRuntimeSteer?: (text: string) => Promise<boolean>;
  runtimeDeciding?: boolean;
  searchTargetId?: string;
  composerResetKey: number;
}) {
  const [draft, setDraft] = useState("");
  const [memoryActivationArmed, setMemoryActivationArmed] = useState(false);
  const memoryActivationToggle = useRef(createMemoryActivationToggle());
  const fileReadEpoch = useRef(0);
  const composerScope = `${sessionId ?? 0}:${composerResetKey}`;
  const composerScopeRef = useRef(composerScope);
  composerScopeRef.current = composerScope;
  const clearMemoryActivation = () => {
    memoryActivationToggle.current.clear();
    setMemoryActivationArmed(false);
  };
  const consumeMemoryActivation = () => {
    const metadata = memoryActivationToggle.current.consume(composerScope);
    setMemoryActivationArmed(false);
    return metadata;
  };
  const resetComposerActivation = () => {
    clearMemoryActivation();
    fileReadEpoch.current += 1;
  };
  useEffect(resetComposerActivation, [sessionId, composerResetKey, backendOnline]);
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
        window.sessionStorage.getItem("lianxin-tool-selection") || "{}",
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
  const handleExecutionNoticesChange = useCallback(() => {
    if (!nearBottomRef.current) return;
    requestAnimationFrame(() => {
      const viewport = viewportRef.current;
      if (viewport) viewport.scrollTo({ top: viewport.scrollHeight, behavior: "auto" });
    });
  }, []);
  useEffect(() => {
    if (!searchTargetId) return;
    const target = viewportRef.current?.querySelector<HTMLElement>(
      `[data-message-id="${CSS.escape(searchTargetId)}"]`,
    );
    target?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [searchTargetId]);
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
      const fileScope = composerScope;
      const fileEpoch = fileReadEpoch.current;
      reader.onload = () => {
        if (fileScope !== composerScopeRef.current || fileEpoch !== fileReadEpoch.current) return;
        const item = {
          kind,
          fileName: file.name,
          dataUrl: String(reader.result || ""),
          size: file.size,
        } as Attachment;
        setAttachments((current) => [...current, item]);
        if (autoSend)
          window.setTimeout(() => {
            if (fileScope !== composerScopeRef.current || fileEpoch !== fileReadEpoch.current) return;
            onSend("看看这个附件", [item], undefined, undefined, consumeMemoryActivation());
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
      consumeMemoryActivation(),
    );
    fileReadEpoch.current += 1;
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
                         searchActive={String(message.id) === searchTargetId}
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
          <ExecutionNotices sessionId={sessionId} online={backendOnline} onChange={handleExecutionNoticesChange} />
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
          key={runtimeTask.task?.taskId}
          state={runtimeTask}
          onApprove={onRuntimeApprove ?? (() => undefined)}
          onStop={onRuntimeStop ?? (() => undefined)}
          onSteer={onRuntimeSteer}
          deciding={runtimeDeciding}
        />
      ) : null}
      <div className="composer-wrap">
        {(toolSelection.forcedTool || toolSelection.preferredTool) && (
          <div className="attachment-preview">
            <span>
              仅下一条消息：{toolSelection.forcedTool ? "强制使用" : "建议使用"}
              {toolSelection.forcedTool || toolSelection.preferredTool}
              {toolSelection.forcedTool &&
                ["look_at_camera", "capture_from_camera"].includes(toolSelection.forcedTool)
                ? "（发送即授权本轮拍照）"
                : ""}
            </span>
            <button
              type="button"
              className="composer-tool"
              onClick={() => {
                window.sessionStorage.removeItem("lianxin-tool-selection");
                window.dispatchEvent(new CustomEvent("tool-selection", { detail: {} }));
              }}
            >
              取消工具选择
            </button>
          </div>
        )}
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
                  onClick={() => {
                    if (attachments.length === 1 && !draft.trim()) resetComposerActivation();
                    setAttachments((current) =>
                      current.filter((_, itemIndex) => itemIndex !== index),
                    );
                  }}
                >
                  <X size={12} />
                </button>
              </div>
            ))}
          </div>
          <textarea
            value={draft}
            onChange={(event) => {
              setDraft(event.target.value);
              if (!event.target.value.trim()) resetComposerActivation();
            }}
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
              <button
                type="button"
                className={`composer-tool memory-activation-toggle ${memoryActivationArmed ? "armed" : ""}`}
                aria-pressed={memoryActivationArmed}
                disabled={!backendOnline}
                title={memoryActivationArmed ? "下一条消息尝试瞬忆；点击取消，不保证找到或提及记忆" : "仅让下一条消息尝试瞬忆，不保证找到或提及记忆"}
                onClick={() => {
                  if (memoryActivationArmed) clearMemoryActivation();
                  else {
                    memoryActivationToggle.current.arm(composerScope);
                    setMemoryActivationArmed(true);
                  }
                }}
              >
                <BrainCircuit size={16} />{memoryActivationArmed ? "瞬忆 · 下一条" : "瞬忆"}
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
                  onClick={() => { resetComposerActivation(); onCancel(); }}
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
        <button data-settings-close className="overlay-close" onClick={onClose}>
          <X size={18} />
        </button>
        {children}
      </section>
    </div>
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
            "alarm",
            "settings",
            "api",
            "proactive",
            "memory-constellation",
            "ripple",
            "data-tide",
            "study-room",
            "time-capsule",
            "vision",
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

export function ConstellationPanel({ onClose }: { onClose: () => void }) {
  return (
    <div className="constellation-overlay">
      <div className="constellation-frame-wrap">
        <button data-settings-close className="overlay-close" onClick={onClose}>
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

export function RipplePanel({ onClose }: { onClose: () => void }) {
  return (
    <div className="constellation-overlay">
      <div className="constellation-frame-wrap">
        <button data-settings-close className="overlay-close" onClick={onClose}>
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

export function DataTidePanel({ onClose }: { onClose: () => void }) {
  return (
    <div className="constellation-overlay">
      <div className="constellation-frame-wrap">
        <button data-settings-close className="overlay-close" onClick={onClose}>
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
  const [searchQuery, setSearchQuery] = useState("");
  const [searchActiveIndex, setSearchActiveIndex] = useState(0);
  const searchMatches = useMemo(
    () => {
      const query = searchQuery.trim().toLocaleLowerCase();
      if (!query) return [] as string[];
      return messages
        .filter((message) => message.content.toLocaleLowerCase().includes(query))
        .map((message) => String(message.id));
    },
    [messages, searchQuery],
  );
  useEffect(() => {
    setSearchActiveIndex((current) => Math.min(current, Math.max(0, searchMatches.length - 1)));
  }, [searchMatches.length]);
  const changeSearchQuery = useCallback((value: string) => {
    setSearchQuery(value);
    setSearchActiveIndex(0);
  }, []);
  const clearSearch = useCallback(() => {
    setSearchQuery("");
    setSearchActiveIndex(0);
  }, []);
  const moveSearch = useCallback((direction: 1 | -1) => {
    if (!searchMatches.length) return;
    setSearchActiveIndex((current) => (current + direction + searchMatches.length) % searchMatches.length);
  }, [searchMatches.length]);
  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        document.querySelector<HTMLInputElement>(".search-box input")?.focus();
      }
    };
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, []);
  const [interactionMessages, setInteractionMessages] = useState<Message[]>([]);
  const [backendOnline, setBackendOnline] = useState(false);
  const [busy, setBusy] = useState(false);
  const [interactionThinking, setInteractionThinking] = useState(false);
  const [proactiveThinking, setProactiveThinking] = useState(false);
  const [panel, setPanel] = useState<PanelId>("none");
  useEffect(() => {
    if (!("__TAURI_INTERNALS__" in window)) return;
    let live = true;
    let unlisten: (() => void) | undefined;
    void listen<{ name: string }>(CAPABILITY_TOOL_EVENT, event => {
      if (!live || typeof event.payload?.name !== "string") return;
      applyPreferredTool(event.payload.name);
      setActiveWorkspace("chat");
      setPanel("none");
    }).then(stop => { if (live) unlisten = stop; else stop(); })
      .catch(error => console.warn("能力中枢工具交接监听不可用", error));
    return () => { live = false; unlisten?.(); };
  }, []);
  const [music, setMusic] = useState<MusicState>({});
  const [musicError, setMusicError] = useState("");
  const [axis, setAxis] = useState<FiveAxisState>({ axes: {}, mood: "中性" });
  const [voiceActive, setVoiceActive] = useState(false);
  const [voiceInputActive, setVoiceInputActive] = useState(false);
  const [voiceInputText, setVoiceInputText] = useState("");
  const [toolBlocks, setToolBlocks] = useState<ToolRoundState[]>([]);
  const [composerResetKey, setComposerResetKey] = useState(0);
  // 执行运行时（阶段 2）：任务进度 + 审批卡片
  const [runtimeTask, setRuntimeTask] = useState<ExecutionTaskState | null>(null);
  const [runtimeDeciding, setRuntimeDeciding] = useState(false);
  const runtimeSigRef = useRef("");
  const toolCursorRef = useRef(0);
  const sessionIdRef = useRef<number | null>(null);
  const sessionChangingRef = useRef(false);
  const sendEpochRef = useRef(0);
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
  const [segmentPlans, setSegmentPlans] = useState<Record<string, string[]>>({});
  const [segmentShown, setSegmentShown] = useState<Record<string, number>>({});
  const segmentTokenRef = useRef(0);
  const turnGenerationRef = useRef(0);
  const speechStopRef = useRef<Promise<number | null>>(Promise.resolve(null));
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
          if (!sessionChangingRef.current) {
            if (sessionIdRef.current !== (status.sessionId ?? null)) {
              sendEpochRef.current += 1;
              setComposerResetKey((value) => value + 1);
              setToolBlocks([]);
            }
            sessionIdRef.current = status.sessionId ?? null;
          }
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
        if (event.kind === "memory_activation" && !event.activation) continue;
        const key = `${event.sessionId ?? 0}:${event.turn ?? 0}:${event.round ?? 0}${event.category === "memory_activation" ? ":memory_activation" : event.category === "decision" ? `:decision:${event.seq}` : ""}`;
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
            category: event.category,
            turn: Number(event.turn ?? 0),
            round: Number(event.round ?? 0),
            anchorIndex,
            calls: [],
          });
          index = blocks.length - 1;
          indexByKey.set(key, index);
        }
        const calls = blocks[index].calls;
        if (event.kind === "memory_activation") {
          blocks[index].activation = event.activation;
          changed = true;
          continue;
        }
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
        const terminal = state.lastResult;
        const terminalSession = Number(terminal?.sessionId || 0);
        const terminalTaskId = String(terminal?.taskId || "");
        if (terminalSession && terminalTaskId && terminalSession === sessionIdRef.current &&
            !messagesRef.current.some((message) => message.kind === "execution_task" &&
              message.execution?.taskId === terminalTaskId)) {
          const history = await lianxinApi.messages(terminalSession);
          if (disposed || terminalSession !== sessionIdRef.current) return;
          const restored = history.items.map(restoreMessage);
          if (restored.some((message) => message.kind === "execution_task" &&
              message.execution?.taskId === terminalTaskId)) {
            setMessages((current) => mergePersistedMessages(current, restored));
          }
        }
        const signature = executionSignature(state);
        if (signature === runtimeSigRef.current) return;
        runtimeSigRef.current = signature;
        setRuntimeTask(state);
      } catch {
        /* 后端尚未就绪时保持安静 */
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1500);
    window.addEventListener("lianxin-runtime-changed", poll);
    return () => {
      disposed = true;
      window.clearInterval(timer);
      window.removeEventListener("lianxin-runtime-changed", poll);
    };
  }, [backendOnline]);
  const approveRuntime = useCallback(
    async (
      requestId: string,
      decision: "accept" | "acceptForSession" | "decline",
    ) => {
      if (!requestId) return;
      const scope = executionScope(runtimeTask, sessionIdRef.current);
      if (!scope) return;
      setRuntimeDeciding(true);
      try {
        const result = await lianxinApi.runtimeApprove(
          scope,
          requestId,
          decision,
          decision === "acceptForSession",
        );
        if (!result.ok) showExecutionNotice(result.error || "审批未被确认，请核对任务状态。",
          "runtime_approve", scope.sessionId, scope.taskId);
        runtimeSigRef.current = "";
        setRuntimeTask(await lianxinApi.runtimeState());
      } catch {
        showExecutionNotice("审批回执未确认，请先核对任务状态，勿重复授权。", "runtime_approve", scope.sessionId, scope.taskId);
      } finally {
        setRuntimeDeciding(false);
      }
    },
    [runtimeTask],
  );
  const stopRuntime = useCallback(async () => {
    const scope = executionScope(runtimeTask, sessionIdRef.current);
    if (!scope) return;
    setRuntimeDeciding(true);
    try {
      const result = await lianxinApi.runtimeStop(scope, "用户停止任务");
      if (!result.ok) showExecutionNotice(result.error || "未能确认停止，请核对任务状态。",
        "runtime_stop", scope.sessionId, scope.taskId);
      runtimeSigRef.current = "";
      setRuntimeTask(await lianxinApi.runtimeState());
    } catch {
      showExecutionNotice("停止请求未确认，请核对执行面板。", "runtime_stop", scope.sessionId, scope.taskId);
    } finally {
      setRuntimeDeciding(false);
    }
  }, [runtimeTask]);
  const steerRuntime = useCallback(async (text: string): Promise<boolean> => {
    const scope = executionScope(runtimeTask, sessionIdRef.current);
    if (!scope) return false;
    try {
      const result = await lianxinApi.runtimeSteer(text, scope);
      showExecutionNotice(result.ok ? "补充要求已提交到当前执行轮。" : result.error || "补充要求未被确认。",
        "runtime_steer", scope.sessionId, scope.taskId, result.ok ? "info" : "warning");
      window.dispatchEvent(new Event("lianxin-runtime-changed"));
      return result.ok;
    } catch {
      showExecutionNotice("补充要求的接收结果未确认，请先核对任务进度，勿重复提交。",
        "runtime_steer", scope.sessionId, scope.taskId);
      return false;
    }
  }, [runtimeTask]);
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
          setBackground((current) =>
            current.enabled === next.enabled &&
            current.opacity === next.opacity &&
            current.chatOpacity === next.chatOpacity &&
            current.fitMode === next.fitMode
              ? current
              : {
                  ...current,
                  enabled: next.enabled,
                  opacity: next.opacity,
                  chatOpacity: next.chatOpacity,
                  fitMode: next.fitMode,
                },
          );
        }
      } catch {
        /* The bundled fallback remains visible while the API is offline. */
      }
    };
    const refreshBackground = () => void sync(true);
    window.addEventListener("lianxin-background-refresh", refreshBackground);
    void sync(true);
    const timer = window.setInterval(() => void sync(false), 800);
    return () => {
      disposed = true;
      window.clearInterval(timer);
      window.removeEventListener("lianxin-background-refresh", refreshBackground);
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
          setAvatar((current) =>
            current.enabled === next.enabled &&
            current.size === next.size &&
            current.gap === next.gap &&
            current.border === next.border
              ? current
              : {
                  ...current,
                  enabled: next.enabled,
                  size: next.size,
                  gap: next.gap,
                  border: next.border,
                },
          );
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
  const openSession = async (id: number) => {
    if (sessionChangingRef.current || busy) throw new Error("正在回复或切换会话，请稍后重试");
    sendEpochRef.current += 1;
    setComposerResetKey((value) => value + 1);
    sessionChangingRef.current = true;
    turnGenerationRef.current += 1;
    cancelSegments();
    return lianxinApi
      .selectSession(id)
      .then(() => {
        sessionIdRef.current = id;
        return lianxinApi.messages(id);
      })
      .then((result) => {
        setActiveWorkspace("chat");
        setInteractionMessages([]);
        setToolBlocks([]);
        setMessages(result.items.map(restoreMessage));
      })
      .finally(() => { sessionChangingRef.current = false; });
  };
  const refreshHistoryConversation = async () => {
    if (sessionChangingRef.current) throw new Error("会话已修改，正在切换聊天，请稍后刷新");
    sessionChangingRef.current = true;
    try {
      const status = await lianxinApi.status();
      const result = await lianxinApi.messages(status.sessionId);
      sendEpochRef.current += 1;
      setComposerResetKey(value => value + 1);
      turnGenerationRef.current += 1;
      cancelSegments();
      sessionIdRef.current = status.sessionId;
      setInteractionMessages([]);
      setToolBlocks([]);
      setMessages(result.items.map(restoreMessage));
    } catch (error) {
      throw new Error(`历史操作已完成，但聊天刷新失败，请重开面板核对，勿重复导入。${String(error)}`);
    } finally {
      sessionChangingRef.current = false;
    }
  };
  // 分段计划的键：折叠空白后比较，避免落库副本与本地乐观消息因空白差异失配。
  const segmentKeyOf = (content: string) =>
    (content || "").replace(/\s+/g, " ").trim();

  const buildSegmentPlan = (value: string, userPrompt: string) => {
    const text = (value || "").trim();
    if (!text || !shouldSendAsConversation(text, userPrompt)) return null;
    const parts = splitConversationText(text);
    if (parts.length < 2) return null;
    return { key: segmentKeyOf(text), parts };
  };

  const revealSegments = async (plan: { key: string; parts: string[] }, generation: number) => {
    const token = segmentTokenRef.current + 1;
    segmentTokenRef.current = token;

    await playSpeechSequence(plan.parts, generation, {
      prepare: lianxinApi.prepareSpeech,
      play: lianxinApi.playPreparedSpeech,
      speak: lianxinApi.speak,
      reveal: (count) => setSegmentShown((current) => ({ ...current, [plan.key]: count })),
      notify: () => lianxinApi.playSound("lianxinSend.mp3"),
      isCurrent: () => token === segmentTokenRef.current,
      beforePlay: () => new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve())),
    });
  };

  const cancelSegments = () => {
    segmentTokenRef.current += 1;
    speechStopRef.current = speechStopRef.current
      .then(() => lianxinApi.stopSpeaking())
      .then((result) => result.generation)
      .catch(() => null);
  };

  const segmentBubblesFor = (message: Message): string[] => {
    const content = message.content ?? "";
    if (message.role !== "assistant") return [content];
    if (message.kind && message.kind !== "text") return [content];
    if (message.imageUrl || message.attachments?.length) return [content];
    const key = segmentKeyOf(content);
    const parts = segmentPlans[key];
    if (!parts) return [content];
    const shown = segmentShown[key] ?? parts.length;
    return parts.slice(0, Math.max(0, Math.min(shown, parts.length)));
  };

  const sendQueueRef = useRef(Promise.resolve());
  const pendingSendsRef = useRef(0);

  const processSend = async (
    text: string,
    attachments: Attachment[] = [],
    selection: { forcedTool?: string; preferredTool?: string } = {},
    quote?: { role: "user" | "assistant"; content: string },
    turnGeneration = turnGenerationRef.current,
    requestMetadata?: ChatRequestMetadata,
  ) => {
    const requestSessionId = sessionIdRef.current;
    const requestEpoch = sendEpochRef.current;
    const messageTime = "现在";
    const imageIds = attachments.map(
      (_, index) => `image-${Date.now()}-${index}`,
    );
    void lianxinApi.playSound("ButtonAll.mp3").catch(() => undefined);
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
    if (!backendOnline) {
      showExecutionNotice("后端尚未连接，本条消息未发送。", "backend_offline",
                          requestSessionId ?? undefined, `chat-${turnGeneration}`);
      return;
    }
    try {
      let reply = "";
      for await (const event of lianxinApi.streamChat(
        text,
        attachments,
        selection,
        quote,
        requestMetadata,
      )) {
        if (sendEpochRef.current !== requestEpoch || sessionIdRef.current !== requestSessionId) return;
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
      if (!reply.trim() || sessionIdRef.current !== requestSessionId || sendEpochRef.current !== requestEpoch) return;
      setMessages((current) => [
        ...current,
        {
          id: `assistant-${Date.now()}`,
          role: "assistant",
          content: reply,
          time: "现在",
        },
      ]);
      if (reply.trim() && turnGeneration === turnGenerationRef.current) {
        const plan = !attachments.length ? buildSegmentPlan(reply, text) : null;
        if (plan) {
          setSegmentPlans((current) => ({ ...current, [plan.key]: plan.parts }));
          setSegmentShown((current) => ({ ...current, [plan.key]: 0 }));
        } else void lianxinApi.playSound("lianxinSend.mp3").catch(() => undefined);
        const generation = await speechStopRef.current;
        if (turnGeneration !== turnGenerationRef.current) return;
        if (plan && generation !== null) void revealSegments(plan, generation);
        else if (generation !== null) void lianxinApi.speak(reply, generation).catch(() => undefined);
        else if (plan) {
          setSegmentShown((current) => ({ ...current, [plan.key]: plan.parts.length }));
          void lianxinApi.playSound("lianxinSend.mp3").catch(() => undefined);
        }
      }
    } catch (error) {
      console.warn("[CHAT_REQUEST] failed", error);
      showExecutionNotice("本次请求未能完成，请检查服务连接；如涉及发送或修改操作，请先核对结果。",
                          "chat_request_failed", requestSessionId ?? undefined, `chat-${turnGeneration}`);
    }
  };
  const handleSend = (
    text: string,
    attachments: Attachment[] = [],
    selection: { forcedTool?: string; preferredTool?: string } = {},
    quote?: { role: "user" | "assistant"; content: string },
    requestMetadata?: ChatRequestMetadata,
  ) => {
    if (sessionChangingRef.current) {
      showExecutionNotice("正在切换会话，本条消息未发送，请切换完成后重新发送。", "chat_session_changing");
      return;
    }
    window.sessionStorage.removeItem("lianxin-tool-selection");
    window.localStorage.removeItem("lianxin-tool-selection");
    window.dispatchEvent(new CustomEvent("tool-selection", { detail: {} }));
    const turnGeneration = ++turnGenerationRef.current;
    cancelSegments();
    pendingSendsRef.current += 1;
    setBusy(true);
    const queuedSession = sessionIdRef.current;
    const queuedEpoch = sendEpochRef.current;
    sendQueueRef.current = sendQueueRef.current
      .then(() => {
        if (sendEpochRef.current !== queuedEpoch || sessionIdRef.current !== queuedSession || sessionChangingRef.current) return;
        return processSend(text, attachments, selection, quote, turnGeneration, requestMetadata);
      })
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
    sendEpochRef.current += 1;
    setComposerResetKey((value) => value + 1);
    turnGenerationRef.current += 1;
    cancelSegments();
    void lianxinApi.cancelChat();
    setBusy(false);
  };
  // 工具信息栏永不随对话清空，只有重启莲心或新建/切换会话时才消失。
  messagesRef.current = messages;
  const handleNewChat = () => {
    if (sessionChangingRef.current) return;
    sendEpochRef.current += 1;
    setComposerResetKey((value) => value + 1);
    turnGenerationRef.current += 1;
    cancelSegments();
    setMessages([]);
    setInteractionMessages([]);
    setToolBlocks([]);
    if (backendOnline) {
      sessionChangingRef.current = true;
      void lianxinApi.newSession().then((result) => {
        sessionIdRef.current = result.id;
        setComposerResetKey((value) => value + 1);
      }).catch(() => undefined).finally(() => { sessionChangingRef.current = false; });
    }
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
    if (next) cancelSegments();
    void lianxinApi.saveSettingsPanels({ global: { silentMode: next }, sound: { silentMode: next } }).catch(() => setSilentMode(!next));
  };
  const openPanel = (next: PanelId) => {
    if (!isSettingsWindowId(next)) { setPanel(next); return; }
    void openSettingsWindow(next).then((opened) => setPanel(opened ? "none" : next));
  };
  const openLegacy = (feature: LegacyFeature) => {
    if (feature === "alarm" || feature === "qq" || feature === "wechat") {
      openPanel(feature);
      return;
    }
    if (feature === "voice-stt" || feature === "sound" || feature === "network") {
      openPanel(feature);
      return;
    }
    if (feature === "capability") {
      openPanel("capability");
      return;
    }
    if (feature === "proactive") {
      openPanel("proactive");
      return;
    }
    if (feature === "api") {
      openPanel("api");
      return;
    }
    if (feature === "settings") {
      openPanel("settings");
      return;
    }
    if (feature === "vision" || feature === "camera") {
      setPanel("none");
      void openVisionWindow().then((opened) => {
        if (!opened) setPanel("vision");
      });
      return;
    }
    if (feature === "video-call") {
      setPanel("video-call");
      return;
    }
    if (feature === "memory-constellation") {
      openPanel("constellation");
      return;
    }
    if (feature === "ripple") {
      openPanel("ripple");
      return;
    }
    if (feature === "persona") {
      openPanel("persona");
      return;
    }
    if (feature === "data-tide") {
      openPanel("data-tide");
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
        window.sessionStorage.getItem("lianxin-tool-selection") || "{}",
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
            onPanel={openPanel}
            onLegacy={openLegacy}
            onMinimize={handleMinimize}
            onMaximize={handleMaximize}
            onClose={handleClose}
             searchQuery={searchQuery}
             searchMatchCount={searchMatches.length}
             searchActiveIndex={searchActiveIndex}
             onSearchQueryChange={changeSearchQuery}
             onSearchPrevious={() => moveSearch(-1)}
             onSearchNext={() => moveSearch(1)}
             onSearchClear={clearSearch}
          />
          <div className="app-body">
            <SideNavigation
              activeWorkspace={activeWorkspace}
              onWorkspaceChange={handleWorkspaceChange}
              managementOpen={managementOpen}
              onManagementToggle={() => setManagementOpen((open) => !open)}
              onPanel={openPanel}
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
          window.sessionStorage.setItem(
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
          onPanel={openPanel}
          onLegacy={openLegacy}
          onMinimize={handleMinimize}
          onMaximize={handleMaximize}
          onClose={handleClose}
             searchQuery={searchQuery}
             searchMatchCount={searchMatches.length}
             searchActiveIndex={searchActiveIndex}
             onSearchQueryChange={changeSearchQuery}
             onSearchPrevious={() => moveSearch(-1)}
             onSearchNext={() => moveSearch(1)}
             onSearchClear={clearSearch}
        />
        <div className="app-body">
          <SideNavigation
            activeWorkspace={activeWorkspace}
            onWorkspaceChange={handleWorkspaceChange}
            managementOpen={managementOpen}
            onManagementToggle={() => setManagementOpen((open) => !open)}
            onPanel={openPanel}
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
                backendOnline={backendOnline}
                toolRounds={toolBlocks}
                avatar={avatar}
                sessionId={sessionIdRef.current ?? undefined}
                composerResetKey={composerResetKey}
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
                runtimeTask={runtimeTask?.task?.sessionId === sessionIdRef.current ? runtimeTask : null}
                onRuntimeApprove={(requestId, decision) =>
                  void approveRuntime(requestId, decision)
                }
                onRuntimeStop={() => void stopRuntime()}
                onRuntimeSteer={steerRuntime}
                runtimeDeciding={runtimeDeciding}
                 searchTargetId={searchMatches[searchActiveIndex]}
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
        <HistoryPanel onClose={() => setPanel("none")} onOpen={openSession} onChanged={refreshHistoryConversation} chatBusy={busy} />
      )}
      {panel === "settings" && <GlobalSettingsPanel onClose={() => setPanel("none")} />}
      {panel === "api" && <ApiConfigPanel onClose={() => setPanel("none")} />}
      {panel === "voice-stt" && <SttPanel onClose={() => setPanel("none")} />}
      {panel === "sound" && <SoundPanel onClose={() => setPanel("none")} />}
      {panel === "network" && <NetworkPanel onClose={() => setPanel("none")} />}
      <AlarmNotices online={backendOnline} />
      {panel === "alarm" && <AlarmPanel onClose={() => setPanel("none")} />}
      {panel === "qq" && <SocialChatPanel channel="qq" onClose={() => setPanel("none")} />}
      {panel === "wechat" && <SocialChatPanel channel="wechat" onClose={() => setPanel("none")} />}
      {panel === "capability" && <CapabilityPanel onClose={() => setPanel("none")} onUse={name => { applyPreferredTool(name); setActiveWorkspace("chat"); setPanel("none"); }} />}
      {panel === "note" && <NotePanel onClose={() => setPanel("none")} />}
      {panel === "decision" && <DecisionPanel onClose={() => setPanel("none")} />}
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
      {panel === "persona" && <PersonaPanelView onClose={() => setPanel("none")} />}
      {panel === "data-tide" && (
        <DataTidePanel onClose={() => setPanel("none")} />
      )}
      {panel === "study-room" && (
        <StudyRoomPanel onClose={() => setPanel("none")} />
      )}
      {panel === "time-capsule" && (
        <TimeCapsulePanel onClose={() => setPanel("none")} />
      )}
      {panel === "vision" && (
        <Overlay onClose={() => setPanel("none")}>
          <VisionPanel />
        </Overlay>
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
