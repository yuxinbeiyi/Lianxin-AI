# 莲心主界面 UI 规格说明

> 版本：v1.0
>
> 状态：主界面重构设计基线
>
> 目标：以 `莲心预览图2.png` 为视觉参考，建立可落地的 React + Tauri 2 主界面规格，并为后续 Python 能力接入保留清晰边界。

## 1. 文档目标

本文件不是单纯的视觉描述，也不是对现有 PyQt 界面的逐字翻译。它定义：

- 莲心主界面的信息架构
- 一级空间、管理中心和快捷操作的层级
- 主界面各区域的尺寸、职责和状态
- React 前端组件树
- Tauri 桌面壳与 Python 服务的边界
- 聊天、音乐、任务、人格和系统状态的数据模型
- 从当前 PyQt5 界面迁移到新界面的实施顺序
- 视觉稿转为实际产品时必须满足的验收标准

设计目标是让最新版莲心在使用体验上接近预览图，但所有功能名称和交互都必须对应莲心真实能力，不能把示意图中的虚构业务内容直接实现。

## 2. 设计结论

莲心主界面采用“人格化桌面工作台”结构：

```text
┌──────────────────────────────────────────────────────────────┐
│ 品牌 / 在线状态 │ 搜索 │ 历史记录 │ 新建对话 │ 备忘本 │ 模式 │ 窗口 │
├───────────────┬──────────────────────────────────┬───────────┤
│               │                                  │           │
│ 一级空间导航   │        中央对话主工作区            │ 伴随信息栏 │
│               │                                  │           │
│ 对话          │ 当前会话标题                      │ 莲心状态   │
│ 音乐          │ 消息流                            │ 当前播放   │
│ 时间胶囊      │ 工具调用 / 任务进度                │ 当前任务   │
│ 棱镜记忆系统  │ 结果卡片                           │ 今日概览   │
│ 莲心自习室    │                                  │ 人格状态   │
│ 莲心世界      │ 底部统一输入                       │           │
│               │                                  │           │
│ 管理中心      │                                  │ 可折叠     │
└───────────────┴──────────────────────────────────┴───────────┘
```

核心原则：

1. 对话是默认主场，任何视觉装饰都不能降低消息可读性。
2. 人格化角色是陪伴层，不是导航栏，也不是主要内容遮罩。
3. 高频空间进入一级导航，低频配置进入管理中心。
4. 右侧栏展示“莲心正在做什么”，不展示未经真实数据支持的评分。
5. 所有长耗时操作必须以任务状态呈现，不能只显示“正在思考”。
6. 设计先统一信息架构，再扩展页面和动效。

## 3. 真实功能边界

### 3.1 一级空间

主界面左侧一级导航只放以下空间：

| 空间 | 真实职责 | 默认页面形态 |
|---|---|---|
| 对话 | 普通聊天、流式回复、工具调用、图片、语音和主动消息 | 中央聊天工作区 |
| 音乐 | 网易云音乐播放、歌词、歌单、当前播放和听歌反馈 | 音乐空间 |
| 时间胶囊 | 日记、附件、时间线、回忆和回溯 | 时间胶囊工作区 |
| 棱镜记忆系统 | 长期记忆、记忆检索、关系、来源和质量管理 | 记忆工作区 |
| 莲心自习室 | 专注计时、学习空间和相关记录 | 自习室工作区 |
| 莲心世界 | 具身智能、虚拟世界、视觉和设备交互 | 沉浸式工作区 |

一级导航不应出现“工具箱”“知识库”“健康中心”“探索中心”等未定义功能名称。

### 3.2 管理中心

管理中心是一个分组入口，不把所有功能平铺在主界面。其内部菜单使用以下真实名称：

#### 运行与任务

- 闹钟与提醒
- 任务运行中心
- 主动聊天

#### 人格与关系

- 涟漪情感系统
- 人格枢控
- 星图系统

#### 能力与感知

- 能力中枢
- 视觉理解
- 语音转录

#### 系统与连接

- 声音设置
- 全局设置
- API Key
- 网络设置
- QQ 聊天
- 微信聊天

#### 数据与记录

- 数据潮汐

时间胶囊和棱镜记忆系统虽然也属于数据功能，但它们是高频空间，因此仍放在一级导航；管理中心只提供配置或管理入口时，应跳转到对应空间的管理页，而不是再创建一个重复功能。

### 3.3 顶部真实快捷操作

顶部栏可以直接显示以下快捷操作：

- 历史记录
- 新建对话
- 备忘本
- Galgame 模式
- 语音聊天
- 全局设置
- 最小化、最大化、关闭

“搜索”可以作为界面能力，但第一版必须明确范围。建议先支持：

```text
搜索对话、记忆和功能
```

如果未来要支持本地文件全文检索，再增加明确的文件检索状态，不要在视觉稿里暗示一个尚未定义的“全能搜索”。

## 4. 页面总体布局

### 4.1 桌面窗口

目标平台为 Windows 桌面端。推荐设计基准：

| 项目 | 基准 |
|---|---:|
| 设计画布 | 1440 × 900 |
| 支持比例 | 16:9、16:10 |
| 最小可用尺寸 | 1100 × 700 |
| 最小允许尺寸 | 900 × 600 |
| 顶部栏高度 | 70 px |
| 左侧栏宽度 | 240 px，紧凑态 72 px |
| 右侧栏宽度 | 300 px，紧凑态 64 px，隐藏态 0 px |
| 中央内容最小宽度 | 560 px |
| 底部输入区高度 | 112 - 160 px，按内容自适应 |

在最小可用尺寸下，右侧栏默认进入紧凑态；当中央聊天区低于 560 px 时，右侧栏自动隐藏并显示恢复按钮。

### 4.2 层级关系

```text
AppShell
├── TopBar
├── AppBody
│   ├── SideNavigation
│   ├── WorkspaceHost
│   │   ├── ChatWorkspace
│   │   ├── MusicWorkspace
│   │   ├── TimeCapsuleWorkspace
│   │   ├── PrismMemoryWorkspace
│   │   ├── StudyRoomWorkspace
│   │   └── LianxinWorldWorkspace
│   └── CompanionRail
└── GlobalComposer
```

工作区切换不应销毁全局输入栏、连接状态和后台任务状态。对话和音乐等页面可以保持各自滚动位置及局部状态。

## 5. 顶部栏规格

### 5.1 左侧品牌区

内容：

- 莲心图标
- “莲心 AI”品牌名
- 在线 / 离线状态
- 当前空间名称可在中间区域重复显示，但不必在品牌区重复

状态颜色：

- 在线：青绿色
- 连接中：琥珀色
- 部分能力不可用：橙色
- 后端离线：红色

不能仅用颜色表达状态，必须同时有文字或可访问名称。

### 5.2 全局搜索

默认占用顶部中间偏左区域，建议宽度 280 - 360 px。

占位文本：

```text
搜索对话、记忆和功能
```

搜索结果分组：

- 对话
- 记忆
- 功能入口

搜索框不应默认承诺文件检索、实时网络搜索或所有工具搜索。

### 5.3 快捷按钮

推荐顺序：

```text
历史记录 → 新建对话 → 备忘本 → Galgame 模式 → 语音聊天 → 设置
```

设计规则：

- 熟悉的操作使用图标 + 短文字
- 仅图标按钮必须提供 Tooltip
- Galgame 模式使用明显的模式按钮，但不能使用过大的彩色胶囊
- 语音聊天显示当前模式：关闭、待机、监听中、通话中
- 设置不与 API Key、网络设置等高级入口并列显示

## 6. 左侧导航规格

### 6.1 导航区结构

```text
品牌区
一级空间
  对话
  音乐
  时间胶囊
  棱镜记忆系统
  莲心自习室
  莲心世界
分隔线
管理中心
底部用户 / 莲心状态
```

一级空间每项包含：

- 线性图标
- 中文名称
- 当前选中指示条
- 可选的状态徽标

不建议在一级导航展示长句说明。名称必须保持简短、稳定和真实。

### 6.2 导航状态

每个导航项需要支持：

- 默认
- 悬停
- 选中
- 键盘聚焦
- 禁用
- 有后台活动
- 有未读或待处理事项

活动徽标示例：

- 音乐：播放中
- 任务运行中心：有运行中任务
- 主动聊天：已开启
- 闹钟与提醒：有即将触发提醒

这些徽标必须来自真实状态，不得使用装饰性数字。

### 6.3 紧凑导航

紧凑态只显示图标，鼠标悬停或键盘聚焦时显示 Tooltip。切换按钮放在导航顶部或顶部栏，不隐藏当前选中状态。

## 7. 中央对话工作区

### 7.1 对话页结构

```text
ChatWorkspace
├── ConversationHeader
├── MessageViewport
│   ├── TimestampDivider
│   ├── UserMessage
│   ├── AssistantMessage
│   ├── ToolCallGroup
│   ├── TaskProgressCard
│   ├── ImageMessage
│   ├── Audio / VoiceMessage
│   └── ProactiveMessage
└── GlobalComposer
```

### 7.2 会话头部

内容：

- 当前会话标题，默认“默认对话”
- 编辑标题入口
- 当前会话状态
- 可选的任务或工具状态

会话头部不应堆放历史记录、新建对话等全局动作，因为这些已经在顶部栏中。

### 7.3 消息流

消息流必须优先保证阅读体验：

- 内容列最大宽度 720 px
- 消息气泡不覆盖核心文字区域
- 背景角色位于内容层之后
- 长消息支持折叠与展开
- 工具调用默认折叠细节，显示当前阶段和结果
- 代码、表格、图片和链接使用专门渲染器
- 流式输出时保持滚动位置稳定
- 用户主动向上滚动时，不强制拉回底部

### 7.4 真实任务卡片

任务卡片应描述莲心真实执行过程，例如：

```text
正在处理请求
✓ 分析用户问题
✓ 查询记忆 / 网络 / 音乐状态
◌ 等待工具返回
○ 生成最终回复
```

禁止在没有对应业务能力时展示虚构的实验、概率、效率或评估结果。

任务卡片状态：

- queued：排队中
- running：运行中
- waiting：等待外部服务
- success：已完成
- partial：部分完成
- failed：失败
- cancelled：已取消

### 7.5 角色背景层

预览图中的人物是视觉参考，不应作为固定的不可读背景。实现时需要分层：

```text
背景壁纸层
环境氛围层
角色层
内容遮罩层
消息与交互层
```

建议：

- 普通聊天时降低角色不透明度和对比度
- 聊天内容区域使用局部暗化或半透明遮罩
- 角色不能覆盖消息气泡和输入框
- 语音聊天 / Galgame 模式可提升角色权重
- 用户进入专注阅读状态时允许隐藏角色
- 背景图必须支持无图、低性能和高对比度模式

## 8. 全局输入区规格

### 8.1 输入区内容

底部输入区是固定的全局组件，不能随工作区切换而消失。

```text
GlobalComposer
├── QuotePreview
├── ImagePreviewStrip
├── SelectedToolChip
├── TextEditor
└── ComposerToolbar
    ├── 图片
    ├── 文件
    ├── 引用
    ├── 备忘本
    ├── 语音聊天 / 语音输入
    ├── 停止 / 重新发送
    └── 发送
```

当前 PyQt `InputPanel` 已有图片、文件、引用、工具选择、语音输入、清空、重新发送、停止朗读和发送等能力，新前端应保持这些能力，不应只复刻视觉稿中四个装饰按钮。

### 8.2 输入状态

- empty：空输入
- composing：正在输入
- has_attachments：有附件
- quoted：引用消息
- submitting：提交中
- streaming：莲心回复中
- voice_recording：录音中
- disabled：待机或后端不可用
- error：提交失败，可重试

发送按钮必须根据状态变化：

- 空输入：禁用
- 有文本或附件：可发送
- 流式回复：显示停止
- 录音中：显示停止录音

## 9. 右侧伴随信息栏

右侧栏是预览图中最有产品差异化的部分。它不是设置栏，而是“莲心当前状态的可视化”。

### 9.1 卡片顺序

默认顺序：

1. 莲心状态
2. 当前播放
3. 当前任务
4. 今日概览
5. 莲心人格状态

卡片可以折叠，但折叠状态必须记忆。

### 9.2 莲心状态卡

真实内容：

- 在线状态
- 待机中 / 思考中 / 说话中
- 当前语音或后台活动状态
- 头像 / 当前动画状态

不添加“智能指数”“人格分数”等未定义指标。

### 9.3 当前播放卡

真实内容：

- 歌曲封面
- 歌曲名称
- 歌手
- 网易云音乐标识
- 当前进度和总时长
- 上一首、暂停 / 播放、下一首

如果没有播放：

```text
当前没有播放歌曲
打开音乐空间
```

### 9.4 当前任务卡

真实内容：

- 当前任务名称
- 任务阶段
- 进度（仅在真实任务提供进度时显示）
- 任务运行中心入口
- 取消或查看详情

不能默认显示虚构的“Jev 实验测试”。设计稿中的示例应改成“正在处理当前请求”或“当前任务”。

### 9.5 今日概览卡

可显示已存在或可稳定统计的数据：

- 对话次数
- 陪伴时长
- 当前任务
- 主动聊天状态
- 系统状态

每项必须有数据来源。没有数据时显示“暂无记录”，不显示伪造的数值。

### 9.6 人格状态卡

入口：

- 涟漪情感系统
- 人格枢控
- 星图系统

这些入口可以放在卡片中，但卡片标题应明确是“莲心人格状态”，不能误导为用户情绪评估。

### 9.7 右侧栏响应式行为

```text
宽度 >= 1280：完整右侧栏
1100 <= 宽度 < 1280：右侧栏紧凑卡片
900 <= 宽度 < 1100：右侧栏隐藏，保留展开按钮
高度不足：卡片内部滚动，输入区不被挤出视口
```

## 10. 视觉系统

### 10.1 色彩角色

建议建立语义色，而不是在每个组件里写独立颜色：

```css
--lx-bg-0: #081116;
--lx-bg-1: #0e1b22;
--lx-surface: rgba(16, 31, 39, 0.86);
--lx-surface-soft: rgba(26, 48, 58, 0.64);
--lx-border: rgba(157, 214, 205, 0.18);
--lx-text: #edf7f5;
--lx-text-muted: #9eb1b5;
--lx-accent: #69d7c3;
--lx-accent-strong: #3dbda9;
--lx-info: #83baff;
--lx-warning: #eac477;
--lx-danger: #ef8585;
--lx-persona: #b9a3ed;
```

青绿色用于主要交互和在线状态；蓝色用于信息；琥珀色用于提醒；紫色只用于人格和情绪相关功能；红色只用于错误和危险操作。

### 10.2 背景和透明度

背景壁纸是可选视觉层，不是组件容器的唯一背景。必须提供：

- 默认背景
- 用户自定义背景
- 无背景 / 纯色背景
- 低对比度背景
- 高对比度模式

所有正文区域需要保证文字和控件对比度，不允许用强背景图直接承载长段文字。

### 10.3 圆角和阴影

- 普通控件圆角：8 px
- 输入舱圆角：16 - 20 px
- 主要卡片圆角：12 px
- 不使用多层嵌套卡片
- 阴影只用于浮层、输入舱和选中状态
- 不让所有元素都发光

### 10.4 图标

React 版本优先使用 Lucide Icons 或统一图标库。图标必须表达功能，不使用难以识别的装饰符号替代按钮。

## 11. React 组件树

建议前端目录：

```text
src/
├── app/
│   ├── App.tsx
│   ├── routes.tsx
│   ├── providers/
│   │   ├── AppStateProvider.tsx
│   │   ├── ThemeProvider.tsx
│   │   └── TauriEventProvider.tsx
│   └── app-shell/
│       ├── AppShell.tsx
│       ├── TopBar.tsx
│       ├── SideNavigation.tsx
│       ├── WorkspaceHost.tsx
│       ├── CompanionRail.tsx
│       └── GlobalComposer.tsx
├── components/
│   ├── avatar/
│   │   ├── LianxinAvatar.tsx
│   │   ├── AvatarState.ts
│   │   └── AvatarLayer.tsx
│   ├── chat/
│   │   ├── ConversationHeader.tsx
│   │   ├── MessageViewport.tsx
│   │   ├── MessageBubble.tsx
│   │   ├── ToolCallGroup.tsx
│   │   ├── TaskProgressCard.tsx
│   │   ├── AttachmentMessage.tsx
│   │   └── ProactiveMessage.tsx
│   ├── music/
│   │   ├── NowPlayingCard.tsx
│   │   ├── MusicControls.tsx
│   │   └── MusicState.ts
│   ├── companion/
│   │   ├── LianxinStatusCard.tsx
│   │   ├── CurrentTaskCard.tsx
│   │   ├── TodayOverviewCard.tsx
│   │   └── PersonaStateCard.tsx
│   ├── composer/
│   │   ├── TextComposer.tsx
│   │   ├── ComposerToolbar.tsx
│   │   ├── QuotePreview.tsx
│   │   ├── AttachmentPreview.tsx
│   │   └── VoiceButton.tsx
│   ├── management/
│   │   ├── ManagementMenu.tsx
│   │   ├── ManagementGroup.tsx
│   │   └── ManagementEntry.tsx
│   └── primitives/
│       ├── Panel.tsx
│       ├── IconButton.tsx
│       ├── StatusDot.tsx
│       ├── ProgressBar.tsx
│       ├── CollapseButton.tsx
│       └── EmptyState.tsx
├── workspaces/
│   ├── chat/ChatWorkspace.tsx
│   ├── music/MusicWorkspace.tsx
│   ├── time-capsule/TimeCapsuleWorkspace.tsx
│   ├── prism-memory/PrismMemoryWorkspace.tsx
│   ├── study-room/StudyRoomWorkspace.tsx
│   └── lianxin-world/LianxinWorldWorkspace.tsx
├── features/
│   ├── history/
│   ├── notes/
│   ├── galgame/
│   ├── voice-chat/
│   ├── management/
│   └── search/
├── stores/
│   ├── appStore.ts
│   ├── chatStore.ts
│   ├── musicStore.ts
│   ├── taskStore.ts
│   ├── companionStore.ts
│   └── settingsStore.ts
├── services/
│   ├── apiClient.ts
│   ├── chatService.ts
│   ├── musicService.ts
│   ├── memoryService.ts
│   ├── taskService.ts
│   └── eventService.ts
├── types/
│   ├── chat.ts
│   ├── music.ts
│   ├── task.ts
│   ├── companion.ts
│   └── settings.ts
└── styles/
    ├── tokens.css
    ├── globals.css
    └── themes.css
```

### 11.1 组件职责边界

`AppShell` 只负责布局和全局区域，不直接调用 AgentCore。

`WorkspaceHost` 只负责路由和工作区生命周期，不负责具体业务。

`ChatWorkspace` 负责聊天呈现和用户输入，不负责直接操作 SQLite、MCP 或模型配置。

`CompanionRail` 只消费状态，不自行推测情绪或生成统计。

`GlobalComposer` 负责输入编排、附件和语音入口，不负责执行模型请求。

`services/` 负责调用 Python 应用 API；`stores/` 负责前端状态；`components/` 负责展示和交互。

## 12. 状态模型

### 12.1 应用状态

```ts
type AppState = {
  activeWorkspace: WorkspaceId;
  sideNavCollapsed: boolean;
  companionRailMode: "full" | "compact" | "hidden";
  connection: ConnectionState;
  theme: ThemeMode;
  reducedMotion: boolean;
};
```

### 12.2 莲心状态

```ts
type LianxinPresence = {
  availability: "online" | "connecting" | "offline" | "degraded";
  activity: "idle" | "thinking" | "speaking" | "listening" | "working";
  voiceChat: "off" | "standby" | "listening" | "speaking";
  avatarState: string;
  updatedAt: string;
};
```

### 12.3 音乐状态

```ts
type MusicState = {
  service: "netease" | "none";
  active: boolean;
  title: string;
  artist: string;
  coverUrl?: string;
  positionSeconds: number;
  durationSeconds: number;
  paused: boolean;
  canControl: boolean;
};
```

### 12.4 任务状态

```ts
type TaskState = {
  id: string;
  title: string;
  phase: "queued" | "running" | "waiting" | "success" | "partial" | "failed" | "cancelled";
  progress?: number;
  detail?: string;
  cancellable: boolean;
  startedAt?: string;
  finishedAt?: string;
};
```

任务进度为可选字段。没有真实进度时使用阶段状态，不要伪造百分比。

## 13. React、Tauri 与 Python 边界

### 13.1 总体链路

```text
React UI
  ↓ HTTP JSON / WebSocket / SSE
Python Application API
  ↓
AgentCore / MCP / Memory / DutyScheduler
  ↓
LLM / SQLite / Voice / Vision / Music / QQ / WeChat / Hardware

React UI
  ↔ Tauri Commands / Events
Tauri 2
  ├── 窗口管理
  ├── 托盘
  ├── 通知
  ├── 全局快捷键
  ├── 文件选择
  ├── 桌宠窗口
  └── Python 进程启动与回收
```

### 13.2 推荐通信方式

| 场景 | 推荐通道 |
|---|---|
| 查询历史、设置、歌单 | HTTP JSON |
| 流式聊天回复 | WebSocket 或 SSE |
| 工具开始、工具结束、主动消息 | WebSocket 事件 |
| 音乐状态推送 | WebSocket 事件 |
| 任务进度推送 | WebSocket 事件 |
| 窗口、托盘、通知、热键 | Tauri Command / Event |
| 文件选择 | Tauri 文件对话框 |
| Python 进程生命周期 | Tauri Rust 层 |

### 13.3 事件信封

所有后台事件使用统一格式：

```json
{
  "event": "task.progress",
  "event_id": "evt_01",
  "timestamp": "2026-09-25T12:00:00+08:00",
  "session_id": "session_01",
  "payload": {}
}
```

建议事件名称：

- `chat.delta`
- `chat.completed`
- `chat.failed`
- `tool.started`
- `tool.completed`
- `task.created`
- `task.progress`
- `task.completed`
- `music.state_changed`
- `presence.changed`
- `proactive.message`
- `notification.created`
- `connection.changed`

前端不能依赖 Python 内部类名或 Qt 信号名作为长期协议。

## 14. 从现有 PyQt 界面的映射

| 当前模块 | 新界面目标 |
|---|---|
| `MainWindow` | `AppShell` 和应用生命周期协调层 |
| `CharacterWidget` | `LianxinAvatar`、`SideNavigation`、`ManagementMenu` 的拆分来源 |
| `ChatWidget` | `ChatWorkspace`、`MessageViewport`、消息组件 |
| `InputPanel` | `GlobalComposer` |
| `MusicBoxWidget` | `MusicWorkspace`、`NowPlayingCard` |
| `HistoryDialog` | `HistoryWorkspace` 或历史抽屉 |
| `NoteDialog` | `NotesPanel` 或备忘本抽屉 |
| `SettingsDialog` | 管理中心下的全局设置工作区 |
| `CapabilityCenter` | 管理中心下的能力中枢工作区 |
| `SoundSettingsDialog` | 管理中心下的声音设置工作区 |
| `VoiceSTTDialog` | 管理中心下的语音转录工作区 |
| `ProactiveDialog` | 管理中心下的主动聊天工作区 |
| `AlarmDialog` | 管理中心下的闹钟与提醒工作区 |
| `QqSettingsDialog` | 管理中心下的 QQ 聊天工作区 |
| `WeChatSettingsDialog` | 管理中心下的微信聊天工作区 |
| `TimeCapsuleWindow` | 时间胶囊工作区 |
| `StudyRoomWindow` | 莲心自习室工作区 |
| `MusicSpaceWindow` | 音乐工作区或沉浸式音乐模式 |

现有 HTML/CSS/JS 页面中的视觉资源可以参考或迁移，但依赖 `QWebChannel` 的桥接代码不能原样作为 Tauri 协议使用，需要重新接入应用 API 和事件系统。

## 15. 迁移顺序

### 阶段 0：视觉壳和假数据

目标：先实现接近预览图的界面，不碰现有 Python 能力。

- 搭建 Tauri 2 + React + TypeScript + Vite
- 完成窗口、主题、导航、中央工作区、右侧栏、输入区
- 使用固定 fixture 展示消息、任务、音乐和状态
- 验证不同窗口尺寸和背景图片下的可读性

验收：用户能看到一张稳定、完整、可交互的莲心主界面。

### 阶段 1：接入聊天

- Python 增加聊天 API
- 接入流式回复
- 接入图片附件
- 接入工具开始 / 结束事件
- 接入停止、重试和错误状态
- 保留现有 PyQt 界面作为回退入口

### 阶段 2：接入全局状态

- 莲心在线状态
- 当前活动状态
- 当前任务
- 主动聊天事件
- 今日概览
- 统一通知

### 阶段 3：接入音乐和快捷功能

- 网易云音乐状态
- 播放控制
- 历史记录
- 新建对话
- 备忘本
- 语音聊天
- Galgame 模式

### 阶段 4：迁移一级工作区

顺序建议：

1. 音乐
2. 时间胶囊
3. 棱镜记忆系统
4. 莲心自习室
5. 莲心世界

### 阶段 5：迁移管理中心

按以下顺序迁移：

1. 任务运行中心
2. 主动聊天
3. 闹钟与提醒
4. 能力中枢
5. 涟漪情感系统
6. 人格枢控
7. 星图系统
8. 视觉理解
9. 语音转录
10. 声音设置
11. 全局设置
12. API Key
13. 网络设置
14. QQ 聊天
15. 微信聊天
16. 数据潮汐

### 阶段 6：桌宠和沉浸模式

- Tauri 多窗口
- 透明窗口
- 始终置顶
- Galgame / 桌宠模式
- 角色动画
- 窗口跟随和鼠标交互

只有主界面、核心工作区和关键管理功能稳定后，才建议关闭旧 PyQt 主窗口。

## 16. 性能和可靠性要求

- 主界面首次可交互时间目标小于 3 秒，不等待所有模型或 MCP 服务完成初始化。
- Python 后端不可用时，界面仍能打开并显示明确的离线状态。
- 音乐、语音、视觉等重能力按需加载。
- 长列表使用虚拟化或分页，聊天历史不一次性渲染全部消息。
- QWebEngine 旧页面迁移期间不能与新前端重复初始化同一重资源。
- 所有后台事件必须可丢弃、可重连，不能因为单个事件丢失导致界面永久卡在运行中。
- WebSocket 重连后必须重新拉取当前状态，而不是只等待下一条增量事件。
- 发送请求需要幂等标识，避免网络重试造成重复消息或重复工具调用。
- 视觉背景和角色动画必须支持降低动效和关闭背景图。

## 17. 可访问性和交互规范

- 所有图标按钮有 Tooltip 和无障碍名称。
- 颜色不是唯一状态表达方式。
- 键盘可以在导航、消息、输入框和管理中心之间移动。
- `Enter` 发送，`Shift + Enter` 换行；快捷键需要显示在 Tooltip 或设置中。
- 语音录制、任务运行和后台连接状态必须有文字状态。
- 文字不因窗口缩放而溢出或覆盖其他内容。
- 低对比度背景下仍要保证消息、按钮和状态可识别。

## 18. 预览图到成品的修改要求

以 `莲心预览图2.png` 为基准时，必须保留：

- 顶部快捷操作区
- 左侧一级空间导航
- 中央对话主场
- 底部统一输入舱
- 右侧伴随信息栏
- 管理中心的层级收纳
- 深色、半透明、青绿色主视觉
- 人格化角色作为陪伴层

必须调整：

- 背景角色不能覆盖聊天文字
- 中央聊天内容必须有稳定阅读列
- “Jev 实验测试”等虚构示例替换为真实莲心任务
- 右侧数值必须来自真实数据
- 右侧信息栏支持完整、紧凑、隐藏三种状态
- 搜索范围使用准确文案
- 管理中心不能膨胀成主界面第二个导航栏
- 不增加未定义的健康、效率、关系或智能评分

## 19. 验收清单

### 信息架构

- [ ] 左侧一级导航只有真实一级空间
- [ ] 管理功能全部有明确分组
- [ ] 历史记录、新建对话、备忘本位于顶部快捷区
- [ ] Galgame 模式和语音聊天不被误归类为普通工作区
- [ ] 没有出现虚构功能名称

### 视觉和布局

- [ ] 中央聊天区是视觉主层级
- [ ] 角色不会遮挡消息、输入框和任务卡片
- [ ] 右侧栏可以折叠
- [ ] 低分辨率窗口下中央聊天仍可用
- [ ] 背景图关闭后布局不塌陷
- [ ] 卡片层级清楚，没有多层卡片嵌套

### 交互

- [ ] 新建对话不会丢失未发送内容，或有明确确认
- [ ] 历史记录可以打开、搜索和返回当前对话
- [ ] 输入区支持图片、文件、引用、备忘本和语音入口
- [ ] 流式回复可以停止、失败后重试
- [ ] 工具调用可以查看状态和结果
- [ ] 任务运行中心能与右侧当前任务状态联动
- [ ] 音乐播放卡片与音乐空间状态一致
- [ ] 后端断开时界面不会假装在线

### 技术

- [ ] React 组件不直接访问 Python 内部模块
- [ ] Python 能力通过稳定应用 API 暴露
- [ ] 流式数据和后台主动事件使用统一事件信封
- [ ] Tauri 负责窗口、通知、热键和文件选择等桌面能力
- [ ] WebSocket 重连后可以重新同步完整状态
- [ ] 重能力按需加载
- [ ] 旧 PyQt 界面仍可作为迁移期间回退路径

## 20. 第一版实现范围

为了尽快得到接近预览图的真实版本，第一版不应同时实现所有功能。建议范围：

```text
必须实现：
- AppShell
- 顶部栏
- 左侧一级导航
- 对话工作区
- 消息气泡
- 工具 / 任务卡片
- GlobalComposer
- 右侧莲心状态
- 当前播放卡片
- 当前任务卡片
- 今日概览
- 人格状态入口
- 管理中心抽屉

可以先使用 fixture：
- 音乐状态
- 今日统计
- 当前任务进度
- 人格状态

暂缓真实接入：
- 所有复杂设置页
- 记忆图谱完整交互
- 桌宠多窗口
- 复杂 WebGL 场景
- 视觉和语音模型的完整控制面板
```

第一版的成功标准是“主界面结构、交互层级和视觉气质正确”，而不是一次性迁移全部莲心能力。

## 21. 给后续开发模型的执行指令

实现 React + Tauri 版本时，必须遵循：

```text
1. 先实现 AppShell 和 fixture，不要先接复杂后端。
2. 严格使用本文件中的真实功能名称。
3. 不要创造新的一级导航。
4. 不要把管理中心功能全部放进主导航。
5. 不要把角色背景当成内容层。
6. 不要用虚构的业务场景填充任务卡片。
7. 所有状态卡片必须标明数据来源或处于空状态。
8. UI 组件通过 services/stores 获取数据，不直接调用 Python 内部代码。
9. 先完成桌面端 1440×900，再验证 1280×800 和 1100×700。
10. 每完成一个工作区，都用真实功能名称和真实空状态替换 fixture。
```

## 22. 参考基线

视觉参考：`莲心预览图2.png`

当前主界面实现：`gui/main_window.py`、`gui/character_widget.py`、`gui/chat_widget.py`、`gui/input_panel.py`

当前嵌入式 Web 功能：`gui/music_box/`、`gui/time_capsule/`、`gui/study_room/`

当前核心能力：`brain/agent.py`、`brain/mcp/`、`brain/graph_memory.py`、`utils/duty_scheduler.py`

本规格的设计原则是：保留莲心真实能力，重新组织视觉层和交互层；不以重写 AI 核心为前提，不以虚构功能填充界面。
