import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Activity, Check, Database, History, Link2, List, Microscope, Pencil,
  Plus, RefreshCw, Save, Scissors, Trash2, X,
} from "lucide-react";
import {
  lianxinApi,
  type MemoryCurrentState, type MemoryDiagnostics, type MemoryFact,
  type MemoryFacts, type MemoryPanelState, type MemoryStateEvent, type MemoryTrace,
} from "../services/lianxinApi";
import "../styles/memory-panel.css";

type Props = { onClose: () => void };
type Tab = "extract" | "graph" | "context" | "states" | "diagnostics" | "facts";

const CATEGORY_LABELS: Record<string, string> = {
  profile: "个人档案", preferences: "偏好", events: "事件",
  knowledge: "知识", behaviors: "行为模式", skills: "技能",
};
const STATE_TYPES = ["health", "emotion", "location", "project", "relationship", "plan", "other"] as const;
const STATE_LABELS: Record<string, string> = {
  health: "健康", emotion: "情绪", location: "位置", project: "项目",
  relationship: "关系", plan: "计划", other: "其他",
};
const STATUS_LABELS: Record<string, string> = { active: "有效", resolved: "已结束", expired: "已过期" };
const ACTION_LABELS: Record<string, string> = {
  set: "创建", update: "更新", confirm: "再次确认", resolve: "结束", expire: "自动过期",
};
const TABS: Array<{ id: Tab; label: string; icon: typeof Database }> = [
  { id: "extract", label: "记忆提取", icon: Database },
  { id: "graph", label: "知识图谱", icon: Link2 },
  { id: "context", label: "上下文压缩", icon: Scissors },
  { id: "states", label: "当前状态", icon: Activity },
  { id: "diagnostics", label: "记忆诊断", icon: Microscope },
  { id: "facts", label: "记忆浏览", icon: List },
];

type Draft = {
  autoExtract: boolean; conversationAutoSave: boolean; extractionIdleSeconds: number;
  extractionBacklogMessages: number; extractionRetryBaseMinutes: number;
  extractionFailurePauseThreshold: number; extractMessageCount: number;
  maxItemsPerCategory: number; defaultSaveCategory: string; maintenanceEnabled: boolean;
  maintenanceIntervalHours: number; maintenanceConflictScanBatch: number;
  narrativeEnabled: boolean; narrativeIntervalHours: number; narrativeCandidateBatch: number;
  graphEnabled: boolean; autoExtractQuintuples: boolean; semanticRetrievalMode: string;
  contextWindowSize: number; enableConversationSummary: boolean; summaryTriggerThreshold: number;
};

const numberOr = (value: unknown, fallback: number) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const boolOr = (value: unknown, fallback: boolean) => typeof value === "boolean" ? value : fallback;

function draftOf(state: MemoryPanelState): Draft {
  const m = state.memory;
  const g = state.graph;
  return {
    autoExtract: boolOr(m.auto_extract, true),
    conversationAutoSave: boolOr(m.conversation_auto_save, false),
    extractionIdleSeconds: numberOr(m.extraction_idle_seconds, 120),
    extractionBacklogMessages: numberOr(m.extraction_backlog_messages, 20),
    extractionRetryBaseMinutes: numberOr(m.extraction_retry_base_minutes, 5),
    extractionFailurePauseThreshold: numberOr(m.extraction_failure_pause_threshold, 5),
    extractMessageCount: numberOr(m.extract_message_count, 20),
    maxItemsPerCategory: numberOr(m.max_items_per_category, 200),
    defaultSaveCategory: String(m.default_save_category || "knowledge"),
    maintenanceEnabled: boolOr(m.maintenance_enabled, true),
    maintenanceIntervalHours: numberOr(m.maintenance_interval_hours, 6),
    maintenanceConflictScanBatch: numberOr(m.maintenance_conflict_scan_batch, 10),
    narrativeEnabled: boolOr(m.narrative_enabled, true),
    narrativeIntervalHours: numberOr(m.narrative_interval_hours, 12),
    narrativeCandidateBatch: numberOr(m.narrative_candidate_batch, 36),
    graphEnabled: boolOr(g.graph_enabled, true),
    autoExtractQuintuples: boolOr(g.auto_extract_quintuples, true),
    semanticRetrievalMode: String(m.semantic_retrieval_mode || "on_demand"),
    contextWindowSize: numberOr(m.context_window_size, 20),
    enableConversationSummary: boolOr(m.enable_conversation_summary, true),
    summaryTriggerThreshold: numberOr(m.summary_trigger_threshold, 30),
  };
}

function patchOf(draft: Draft) {
  return {
    memory: {
      auto_extract: draft.autoExtract,
      conversation_auto_save: draft.conversationAutoSave,
      extraction_idle_seconds: draft.extractionIdleSeconds,
      extraction_backlog_messages: draft.extractionBacklogMessages,
      extraction_retry_base_minutes: draft.extractionRetryBaseMinutes,
      extraction_failure_pause_threshold: draft.extractionFailurePauseThreshold,
      extract_message_count: draft.extractMessageCount,
      max_items_per_category: draft.maxItemsPerCategory,
      default_save_category: draft.defaultSaveCategory,
      maintenance_enabled: draft.maintenanceEnabled,
      maintenance_interval_hours: draft.maintenanceIntervalHours,
      maintenance_conflict_scan_batch: draft.maintenanceConflictScanBatch,
      narrative_enabled: draft.narrativeEnabled,
      narrative_interval_hours: draft.narrativeIntervalHours,
      narrative_candidate_batch: draft.narrativeCandidateBatch,
      semantic_retrieval_mode: draft.semanticRetrievalMode,
      context_window_size: draft.contextWindowSize,
      enable_conversation_summary: draft.enableConversationSummary,
      summary_trigger_threshold: draft.summaryTriggerThreshold,
    },
    graph: {
      graph_enabled: draft.graphEnabled,
      auto_extract_quintuples: draft.autoExtractQuintuples,
    },
  };
}

function toLocalInput(date: Date) {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
function defaultExpiry() {
  const date = new Date();
  date.setDate(date.getDate() + 7);
  return toLocalInput(date);
}
function formatTime(value?: string) {
  return String(value || "").replace("T", " ").slice(0, 16);
}

function Toggle({ label, hint, checked, onChange }: { label: string; hint?: string; checked: boolean; onChange: (value: boolean) => void }) {
  return <label className="global-settings-toggle"><span><strong>{label}</strong>{hint && <small>{hint}</small>}</span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><i aria-hidden="true" /></label>;
}
function NumberField({ label, value, min, max, unit, onChange }: { label: string; value: number; min: number; max: number; unit: string; onChange: (value: number) => void }) {
  return <label className="global-settings-field"><span>{label}</span><input type="number" min={min} max={max} value={value} aria-label={label} onChange={(event) => onChange(Math.max(min, Math.min(max, Number(event.target.value) || min)))} /><b>{unit}</b></label>;
}
function Card({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return <section className="global-settings-card"><header><h3>{title}</h3>{hint && <p>{hint}</p>}</header>{children}</section>;
}
function Empty({ children }: { children: ReactNode }) {
  return <p className="memory-empty">{children}</p>;
}

export function MemoryPanel({ onClose }: Props) {
  const [tab, setTab] = useState<Tab>("extract");
  const [panel, setPanel] = useState<MemoryPanelState | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [facts, setFacts] = useState<MemoryFacts | null>(null);
  const [states, setStates] = useState<MemoryCurrentState[]>([]);
  const [diagnostics, setDiagnostics] = useState<MemoryDiagnostics | null>(null);
  const [notice, setNotice] = useState("正在读取棱镜记忆…");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState("");

  const [factQuery, setFactQuery] = useState("");
  const [factCategory, setFactCategory] = useState("");
  const [factDraft, setFactDraft] = useState<{ mode: "add" | "edit"; category: string; content: string; oldContent?: string } | null>(null);
  const [stateStatus, setStateStatus] = useState("active");
  const [stateTypeFilter, setStateTypeFilter] = useState("");
  const [stateDraft, setStateDraft] = useState<{ mode: "add" | "edit"; id?: number; content: string; stateType: string; expiresAt: string; confidence: number } | null>(null);
  const [historyView, setHistoryView] = useState<{ state: MemoryCurrentState; events: MemoryStateEvent[]; loading: boolean } | null>(null);
  const [diagPersona, setDiagPersona] = useState("");
  const [diagChannel, setDiagChannel] = useState("");
  const [traceView, setTraceView] = useState<{ trace: MemoryTrace; events: Array<Record<string, unknown>>; loading: boolean } | null>(null);

  useEffect(() => {
    let live = true;
    Promise.all([
      lianxinApi.memoryPanelState(),
      lianxinApi.memoryPanelFacts(),
      lianxinApi.memoryPanelStates(),
      lianxinApi.memoryPanelDiagnostics(),
    ]).then(([config, factData, stateData, diag]) => {
      if (!live) return;
      setPanel(config);
      setDraft(draftOf(config));
      setFacts(factData);
      setStates(stateData.items);
      setDiagnostics(diag);
      setNotice("棱镜记忆已载入");
    }).catch((cause) => {
      if (!live) return;
      setError(String(cause));
      setNotice("棱镜记忆读取失败");
    });
    return () => { live = false; };
  }, []);

  // 与旧版 showEvent 对齐：窗口重新获得焦点时刷新数据列表与维护状态，但不覆盖未保存的配置草稿。
  useEffect(() => {
    const refreshOnFocus = () => {
      void lianxinApi.memoryPanelFacts().then(setFacts).catch(() => undefined);
      void lianxinApi.memoryPanelStates().then((result) => setStates(result.items)).catch(() => undefined);
      void lianxinApi.memoryPanelState().then(setPanel).catch(() => undefined);
    };
    window.addEventListener("focus", refreshOnFocus);
    return () => window.removeEventListener("focus", refreshOnFocus);
  }, []);

  const updateDraft = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft((current) => current ? { ...current, [key]: value } : current);

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    setError("");
    try {
      const saved = await lianxinApi.memoryPanelSave(patchOf(draft));
      setPanel(saved);
      setDraft(draftOf(saved));
      setNotice("记忆配置已保存");
      onClose();
    } catch (cause) {
      setError(String(cause));
      setNotice("保存失败，请检查错误后重试");
    } finally {
      setSaving(false);
    }
  };

  const refreshFacts = async () => {
    setBusy("facts");
    try {
      setFacts(await lianxinApi.memoryPanelFacts());
      setError("");
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(""); }
  };

  const submitFact = async () => {
    if (!factDraft) return;
    const content = factDraft.content.trim();
    if (!content) { setError("记忆内容不能为空"); return; }
    setBusy("fact");
    try {
      const action = factDraft.mode === "add" ? "add" : "update";
      setFacts(await lianxinApi.memoryPanelFact(action, {
        content, category: factDraft.category, ...(factDraft.mode === "edit" ? { oldContent: factDraft.oldContent } : {}),
      }));
      setNotice(factDraft.mode === "add" ? "记忆已新增" : "记忆已更新");
      setError("");
      setFactDraft(null);
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(""); }
  };

  const deleteFact = async (row: MemoryFact, category: string) => {
    if (!window.confirm(`确定删除这条记忆吗？\n\n[${CATEGORY_LABELS[category] || category}] ${row.content.slice(0, 60)}${row.content.length > 60 ? "…" : ""}`)) return;
    setBusy(`delete:${row.id}`);
    try {
      setFacts(await lianxinApi.memoryPanelFact("delete", { content: row.content, category }));
      setNotice("记忆已删除");
      setError("");
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(""); }
  };

  const refreshStates = async () => {
    setBusy("states");
    try {
      setStates((await lianxinApi.memoryPanelStates()).items);
      setError("");
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(""); }
  };

  const submitState = async () => {
    if (!stateDraft) return;
    const content = stateDraft.content.trim();
    if (!content) { setError("状态内容不能为空"); return; }
    const expiresAt = stateDraft.expiresAt ? new Date(stateDraft.expiresAt).toISOString() : "";
    setBusy("state");
    try {
      const action = stateDraft.mode === "add" ? "add" : "update";
      setStates((await lianxinApi.memoryPanelStateAction(action, {
        ...(stateDraft.id ? { id: stateDraft.id } : {}),
        content, stateType: stateDraft.stateType, expiresAt, confidence: stateDraft.confidence,
      })).items);
      setNotice(stateDraft.mode === "add" ? "当前状态已创建" : "当前状态已更新");
      setError("");
      setStateDraft(null);
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(""); }
  };

  const resolveState = async (row: MemoryCurrentState) => {
    if (!window.confirm(`确定结束这条状态吗？\n\n${row.content}`)) return;
    const reason = window.prompt("请输入结束原因：", "状态已不再适用");
    if (reason === null) return;
    setBusy(`resolve:${row.id}`);
    try {
      setStates((await lianxinApi.memoryPanelStateAction("resolve", { id: row.id, reason: reason || "状态已不再适用" })).items);
      setNotice("当前状态已结束");
      setError("");
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(""); }
  };

  const openStateHistory = async (row: MemoryCurrentState) => {
    setHistoryView({ state: row, events: [], loading: true });
    try {
      setHistoryView({ state: row, events: (await lianxinApi.memoryPanelStateEvents(row.id)).events, loading: false });
    } catch (cause) {
      setError(String(cause));
      setHistoryView({ state: row, events: [], loading: false });
    }
  };

  const refreshDiagnostics = async (persona = diagPersona, channel = diagChannel) => {
    setBusy("diagnostics");
    setDiagPersona(persona);
    setDiagChannel(channel);
    setTraceView(null);
    try {
      setDiagnostics(await lianxinApi.memoryPanelDiagnostics({ persona, channel }));
      setError("");
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(""); }
  };

  const openTrace = async (trace: MemoryTrace) => {
    setTraceView({ trace, events: [], loading: true });
    try {
      setTraceView({ trace, events: (await lianxinApi.memoryPanelTrace(trace.trace_id)).events, loading: false });
    } catch (cause) {
      setError(String(cause));
      setTraceView({ trace, events: [], loading: false });
    }
  };

  const visibleFacts = useMemo(() => {
    const keyword = factQuery.trim().toLowerCase();
    return (facts?.categories ?? []).filter((category) => !factCategory || category === factCategory).map((category) => ({
      category,
      rows: (facts?.items[category] ?? []).filter((row) => !keyword || row.content.toLowerCase().includes(keyword)),
    })).filter((group) => group.rows.length);
  }, [facts, factCategory, factQuery]);
  const visibleFactCount = visibleFacts.reduce((sum, group) => sum + group.rows.length, 0);
  const filteredStates = states.filter((row) =>
    (stateStatus === "all" || row.status === stateStatus) && (!stateTypeFilter || row.state_type === stateTypeFilter));
  const activeStateCount = states.filter((row) => row.status === "active").length;
  const maintenance = panel?.maintenance || {};
  const health = diagnostics?.health || panel?.health || {};
  const diagnosticStats = diagnostics?.stats?.requests || panel?.diagnostics?.requests || {};
  const narrative = diagnostics?.narrative || panel?.narrative || { entities: 0, episodes: 0, sagas: 0 };

  return (
    <div className="global-settings-overlay">
      <section className="global-settings-shell" role="dialog" aria-modal="true" aria-labelledby="memory-panel-title">
        <header className="global-settings-header">
          <div>
            <span className="eyebrow">长期记忆</span>
            <h2 id="memory-panel-title">棱镜记忆系统</h2>
            <p>配置保存后即时生效；记忆浏览与当前状态的增删改会立即写入原存储。</p>
          </div>
          <button data-settings-close className="global-settings-close" onClick={onClose} aria-label="关闭棱镜记忆系统"><X /></button>
        </header>
        <div className="global-settings-body">
          <nav className="global-settings-nav" aria-label="棱镜记忆页签">
            {TABS.map(({ id, label, icon: Icon }) => (
              <button key={id} className={tab === id ? "is-active" : ""} onClick={() => setTab(id)}>
                <span className="memory-tab-label"><Icon size={16} />{label}</span>
              </button>
            ))}
          </nav>
          <main className="global-settings-content">
            {!draft ? <p role="status">{notice}</p> : tab === "extract" ? (
              <>
                <Card title="自动提取" hint="记忆系统始终可用；关闭自动提取后不会再自动新增记忆。">
                  <Toggle label="自动提取对话记忆" checked={draft.autoExtract} onChange={(value) => updateDraft("autoExtract", value)} />
                  <Toggle label="对话过程中自动保存记忆" hint="关闭后仅在用户明确说“记住”时保存。" checked={draft.conversationAutoSave} onChange={(value) => updateDraft("conversationAutoSave", value)} />
                </Card>
                <Card title="后台提取触发与失败保护" hint="对话安静后自动处理；积压较多时优先处理。失败采用指数退避，达到阈值后暂停。">
                  <NumberField label="空闲触发" value={draft.extractionIdleSeconds} min={30} max={1800} unit="秒" onChange={(value) => updateDraft("extractionIdleSeconds", value)} />
                  <NumberField label="积压优先" value={draft.extractionBacklogMessages} min={3} max={100} unit="条" onChange={(value) => updateDraft("extractionBacklogMessages", value)} />
                  <NumberField label="首次重试" value={draft.extractionRetryBaseMinutes} min={1} max={60} unit="分钟" onChange={(value) => updateDraft("extractionRetryBaseMinutes", value)} />
                  <NumberField label="连续失败暂停" value={draft.extractionFailurePauseThreshold} min={2} max={20} unit="次" onChange={(value) => updateDraft("extractionFailurePauseThreshold", value)} />
                </Card>
                <Card title="单次提取与容量">
                  <NumberField label="单次提取消息数" value={draft.extractMessageCount} min={5} max={100} unit="条" onChange={(value) => updateDraft("extractMessageCount", value)} />
                  <NumberField label="每类记忆上限" value={draft.maxItemsPerCategory} min={50} max={500} unit="条" onChange={(value) => updateDraft("maxItemsPerCategory", value)} />
                </Card>
                <Card title="后台维护" hint="低频整理记忆结构；最近一次执行状态来自原维护记录。">
                  <Toggle label="启用后台记忆维护" checked={draft.maintenanceEnabled} onChange={(value) => updateDraft("maintenanceEnabled", value)} />
                  <NumberField label="维护间隔" value={draft.maintenanceIntervalHours} min={1} max={168} unit="小时" onChange={(value) => updateDraft("maintenanceIntervalHours", value)} />
                  <NumberField label="每轮冲突扫描" value={draft.maintenanceConflictScanBatch} min={1} max={50} unit="条" onChange={(value) => updateDraft("maintenanceConflictScanBatch", value)} />
                  <p className="memory-note">
                    {maintenance.status
                      ? `最近维护：${formatTime(maintenance.finished_at || maintenance.started_at)} · ${({ success: "成功", failed: "失败", running: "运行中" } as Record<string, string>)[maintenance.status] || maintenance.status} · ${Math.round(Number(maintenance.duration_ms) || 0)} ms`
                      : "尚未执行后台维护"}
                  </p>
                </Card>
                <Card title="叙事记忆整合" hint="后台模型把有来源的碎片整理成实体档案、Episode 和 Saga；原始碎片不会删除。">
                  <Toggle label="启用叙事记忆整合" checked={draft.narrativeEnabled} onChange={(value) => updateDraft("narrativeEnabled", value)} />
                  <NumberField label="整合间隔" value={draft.narrativeIntervalHours} min={1} max={168} unit="小时" onChange={(value) => updateDraft("narrativeIntervalHours", value)} />
                  <NumberField label="每轮碎片" value={draft.narrativeCandidateBatch} min={4} max={100} unit="条" onChange={(value) => updateDraft("narrativeCandidateBatch", value)} />
                </Card>
                <Card title="默认保存分类" hint="用户要求记住某件事但未指定分类时使用。">
                  <label className="global-settings-field"><span>默认分类</span><select value={draft.defaultSaveCategory} onChange={(event) => updateDraft("defaultSaveCategory", event.target.value)}>{Object.keys(CATEGORY_LABELS).map((key) => <option key={key} value={key}>{CATEGORY_LABELS[key]}</option>)}</select></label>
                </Card>
              </>
            ) : tab === "graph" ? (
              <>
                <Card title="知识图谱" hint="图谱存储实体之间的关联关系；关闭后只使用分类事实记忆。">
                  <Toggle label="启用知识图谱记忆" checked={draft.graphEnabled} onChange={(value) => updateDraft("graphEnabled", value)} />
                  <Toggle label="自动提取实体关系" hint="自动识别人物、地点、物品等实体及其关联，无需手动添加。" checked={draft.autoExtractQuintuples} onChange={(value) => updateDraft("autoExtractQuintuples", value)} />
                </Card>
                <Card title="语义记忆检索" hint="按需加载让普通聊天保持轻量，仅在明确询问回忆、时间线、实体或事实时加载本地语义模型。">
                  <label className="global-settings-field"><span>检索模式</span><select value={draft.semanticRetrievalMode} onChange={(event) => updateDraft("semanticRetrievalMode", event.target.value)}><option value="on_demand">按需加载（推荐）</option><option value="always">始终使用语义检索</option><option value="off">仅使用关键词检索</option></select></label>
                </Card>
              </>
            ) : tab === "context" ? (
              <>
                <Card title="上下文窗口" hint="窗口内保留最近 N 条完整消息；数值越大记忆越完整，Token 消耗越高。推荐 15-25。">
                  <NumberField label="窗口大小" value={draft.contextWindowSize} min={4} max={40} unit="条" onChange={(value) => updateDraft("contextWindowSize", value)} />
                </Card>
                <Card title="摘要压缩" hint="窗口外的早期对话压缩成摘要注入上下文；关闭后早期内容直接截断。">
                  <Toggle label="启用早期对话摘要压缩" checked={draft.enableConversationSummary} onChange={(value) => updateDraft("enableConversationSummary", value)} />
                  <NumberField label="摘要触发阈值" value={draft.summaryTriggerThreshold} min={0} max={100} unit="条" onChange={(value) => updateDraft("summaryTriggerThreshold", value)} />
                  <p className="memory-note">0 = 无论多少条都压缩；推荐 20-40。关闭摘要 + 窗口 10 条约 1200 Token/轮；开启摘要 + 窗口 20 条约 2500 Token/轮且长对话不增长。</p>
                </Card>
              </>
            ) : tab === "states" ? (
              <>
                <div className="memory-toolbar">
                  <span className="memory-summary">有效 {activeStateCount} 条 · 共记录 {states.length} 条</span>
                  <select aria-label="状态筛选" value={stateStatus} onChange={(event) => setStateStatus(event.target.value)}><option value="active">有效</option><option value="all">全部</option><option value="resolved">已结束</option><option value="expired">已过期</option></select>
                  <select aria-label="类型筛选" value={stateTypeFilter} onChange={(event) => setStateTypeFilter(event.target.value)}><option value="">全部类型</option>{STATE_TYPES.map((key) => <option key={key} value={key}>{STATE_LABELS[key]}</option>)}</select>
                  <button className="memory-icon-button" onClick={() => void refreshStates()} disabled={busy === "states"} title="刷新当前状态"><RefreshCw size={15} /></button>
                  <button className="memory-action-button" onClick={() => setStateDraft({ mode: "add", content: "", stateType: "other", expiresAt: defaultExpiry(), confidence: 0.95 })}><Plus size={15} />新增</button>
                </div>
                <div className="memory-state-list">
                  {!filteredStates.length && <Empty>当前筛选下没有状态</Empty>}
                  {filteredStates.map((row) => (
                    <article className="memory-state-card" key={row.id}>
                      <div className="memory-state-top">
                        <span className="memory-badge">{STATE_LABELS[row.state_type] || row.state_type}</span>
                        <span className={`memory-status is-${row.status}`}>{STATUS_LABELS[row.status] || row.status}</span>
                        <span className="memory-meta">有效至 {formatTime(row.expires_at)}</span>
                      </div>
                      <p className="memory-state-content">{row.content}</p>
                      <p className="memory-meta">置信度 {Math.round(Number(row.confidence || 0) * 100)}% · 来源：{row.source_channel || "桌面端手动编辑"}{row.source_message_ids?.length ? ` · 消息 ${row.source_message_ids.map((id) => `#${id}`).join(", ")}` : ""}</p>
                      <div className="memory-state-actions">
                        <button className="memory-action-button" onClick={() => void openStateHistory(row)}><History size={14} />历史</button>
                        {row.status === "active" && <>
                          <button className="memory-action-button" onClick={() => setStateDraft({ mode: "edit", id: row.id, content: row.content, stateType: row.state_type, expiresAt: toLocalInput(new Date(row.expires_at)), confidence: Number(row.confidence || 0.8) })}><Pencil size={14} />编辑</button>
                          <button className="memory-action-button" onClick={() => void resolveState(row)} disabled={busy === `resolve:${row.id}`}><Check size={14} />结束</button>
                        </>}
                      </div>
                    </article>
                  ))}
                </div>
              </>
            ) : tab === "diagnostics" ? (
              <>
                <div className="memory-toolbar">
                  <span className="memory-summary">请求 {diagnosticStats.total || 0} · 成功 {diagnosticStats.success || 0} · 失败 {diagnosticStats.failed || 0} · 跨人格召回差异 {diagnostics?.stats?.persona_recall_mismatches || 0} · 实体 {narrative.entities} · Episode {narrative.episodes} · Saga {narrative.sagas}</span>
                </div>
                <div className="memory-toolbar">
                  <select aria-label="人格筛选" value={diagPersona} onChange={(event) => void refreshDiagnostics(event.target.value, diagChannel)}><option value="">全部人格</option>{diagnostics?.personas.map((persona) => <option key={persona} value={persona}>{persona}</option>)}</select>
                  <select aria-label="渠道筛选" value={diagChannel} onChange={(event) => void refreshDiagnostics("", event.target.value)}><option value="">全部渠道</option><option value="desktop">desktop</option><option value="qq">qq</option><option value="wechat">wechat</option></select>
                  <button className="memory-action-button" onClick={() => void refreshDiagnostics()} disabled={busy === "diagnostics"}><RefreshCw size={15} />刷新</button>
                  <span className="memory-meta">活跃事实 {health.active_facts ?? 0} / {health.total_facts ?? 0} · 平均质量 {Math.round(Number(health.average_quality || 0) * 100)}% · 待裁决冲突 {health.pending_conflicts ?? 0}（仅展示）</span>
                </div>
                <div className="memory-trace-list">
                  {!(diagnostics?.traces.length) && <Empty>暂无记忆请求记录</Empty>}
                  {diagnostics?.traces.map((trace) => (
                    <button className={`memory-trace-row${traceView?.trace.trace_id === trace.trace_id ? " is-active" : ""}`} key={trace.trace_id} onClick={() => void openTrace(trace)}>
                      <span>{formatTime(trace.started_at)}</span>
                      <span>{trace.channel || "-"}</span>
                      <span>{trace.persona_id || "默认"}</span>
                      <span className="memory-trace-message">{trace.user_message || "（无用户消息）"}</span>
                      <span>{trace.status || "-"}</span>
                      <span>{Math.round(Number(trace.duration_ms) || 0)} ms</span>
                    </button>
                  ))}
                </div>
                {traceView && (
                  <div className="memory-trace-detail" role="region" aria-label="记忆 Trace 详情">
                    <p className="memory-trace-title">Trace: {traceView.trace.trace_id} · 人格：{traceView.trace.persona_id || "默认"}</p>
                    {traceView.loading ? <p className="memory-note">正在读取事件明细…</p> : traceView.events.length ? traceView.events.map((event, index) => (
                      <article className="memory-trace-event" key={index}>
                        <strong>[{formatTime(String(event.created_at || ""))}] {String(event.event_type || "")}</strong>
                        <span>{String(event.reason || "")}{event.score !== undefined && ` · score=${String(event.score)}`}</span>
                        {event.payload ? <small>{String(JSON.stringify(event.payload)).slice(0, 1600)}</small> : null}
                      </article>
                    )) : <p className="memory-note">暂无事件明细</p>}
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="memory-toolbar">
                  <input className="memory-search" aria-label="搜索记忆" placeholder="搜索记忆关键词…" value={factQuery} onChange={(event) => setFactQuery(event.target.value)} />
                  <select aria-label="记忆分类筛选" value={factCategory} onChange={(event) => setFactCategory(event.target.value)}><option value="">全部分类</option>{Object.keys(CATEGORY_LABELS).map((key) => <option key={key} value={key}>{CATEGORY_LABELS[key]}</option>)}</select>
                  <button className="memory-action-button" onClick={() => setFactDraft({ mode: "add", category: "profile", content: "" })}><Plus size={15} />新增记忆</button>
                  <button className="memory-icon-button" onClick={() => void refreshFacts()} disabled={busy === "facts"} title="刷新记忆列表"><RefreshCw size={15} /></button>
                  <span className="memory-summary">共 {visibleFactCount} 条记忆{factQuery.trim() || factCategory ? "（已过滤）" : ""}</span>
                </div>
                <div className="memory-fact-list">
                  {!visibleFacts.length && <Empty>{(facts?.total ?? 0) ? "没有匹配的记忆" : "还没有任何记忆"}</Empty>}
                  {visibleFacts.map(({ category, rows }) => (
                    <section key={category}>
                      <h3 className="memory-group-title">{CATEGORY_LABELS[category] || category}<small>{rows.length}</small></h3>
                      {rows.map((row) => (
                        <article className="memory-fact" key={row.id}>
                          <div className="memory-fact-main">
                            <p>{row.content}</p>
                            <span className="memory-meta">
                              强度 {row.strength ?? 1} · 质量 {Math.round(Number(row.quality_score ?? 0.5) * 100)}% · {row.source === "auto_extracted" ? "自动" : "手动"}
                              {row.review_status && row.review_status !== "normal" ? ` · ${row.review_status}` : ""}{row.created_at ? ` · ${String(row.created_at).slice(0, 10)}` : ""}
                            </span>
                          </div>
                          <div className="memory-fact-actions">
                            <button title="编辑这条记忆" onClick={() => setFactDraft({ mode: "edit", category, content: row.content, oldContent: row.content })}><Pencil size={14} /></button>
                            <button title="删除这条记忆" className="is-danger" onClick={() => void deleteFact(row, category)} disabled={busy === `delete:${row.id}`}><Trash2 size={14} /></button>
                          </div>
                        </article>
                      ))}
                    </section>
                  ))}
                </div>
              </>
            )}
          </main>
        </div>
        <footer className="global-settings-footer">
          <p role="status" aria-live="polite" className={error ? "global-settings-error" : ""}>{error || notice}</p>
          <button className="secondary" onClick={onClose}>取消</button>
          <button className="primary" disabled={!draft || saving} onClick={() => void save()}>{saving ? "保存中…" : <><Save size={15} />保存配置</>}</button>
        </footer>

        {factDraft && (
          <div className="global-settings-modal-backdrop">
            <div className="global-settings-modal" role="dialog" aria-modal="true" aria-labelledby="memory-fact-title">
              <button className="global-settings-close" onClick={() => setFactDraft(null)} aria-label="关闭"><X /></button>
              <h3 id="memory-fact-title">{factDraft.mode === "add" ? "新增记忆" : "修改记忆"}</h3>
              <label className="global-settings-field"><span>分类</span><select value={factDraft.category} disabled={factDraft.mode === "edit"} onChange={(event) => setFactDraft({ ...factDraft, category: event.target.value })}>{Object.keys(CATEGORY_LABELS).map((key) => <option key={key} value={key}>{CATEGORY_LABELS[key]}</option>)}</select></label>
              <label className="memory-field-block"><span>内容</span><textarea value={factDraft.content} onChange={(event) => setFactDraft({ ...factDraft, content: event.target.value })} placeholder="输入记忆内容…" /></label>
              <div className="global-settings-modal-actions">
                <button className="secondary" onClick={() => setFactDraft(null)}>取消</button>
                <button className="primary" disabled={busy === "fact"} onClick={() => void submitFact()}>{busy === "fact" ? "保存中…" : "保存记忆"}</button>
              </div>
            </div>
          </div>
        )}

        {stateDraft && (
          <div className="global-settings-modal-backdrop">
            <div className="global-settings-modal" role="dialog" aria-modal="true" aria-labelledby="memory-state-title">
              <button className="global-settings-close" onClick={() => setStateDraft(null)} aria-label="关闭"><X /></button>
              <h3 id="memory-state-title">{stateDraft.mode === "add" ? "新增当前状态" : "编辑当前状态"}</h3>
              <label className="memory-field-block"><span>内容</span><textarea value={stateDraft.content} onChange={(event) => setStateDraft({ ...stateDraft, content: event.target.value })} placeholder="例如：用户这周正在准备产品发布" /></label>
              <label className="global-settings-field"><span>类型</span><select value={stateDraft.stateType} onChange={(event) => setStateDraft({ ...stateDraft, stateType: event.target.value })}>{STATE_TYPES.map((key) => <option key={key} value={key}>{STATE_LABELS[key]}</option>)}</select></label>
              <label className="global-settings-field"><span>有效至</span><input type="datetime-local" min={toLocalInput(new Date())} max={toLocalInput(new Date(Date.now() + 90 * 86_400_000))} value={stateDraft.expiresAt} onChange={(event) => setStateDraft({ ...stateDraft, expiresAt: event.target.value })} /></label>
              <label className="global-settings-field"><span>置信度</span><input type="number" min={0} max={1} step={0.05} value={stateDraft.confidence} onChange={(event) => setStateDraft({ ...stateDraft, confidence: Math.max(0, Math.min(1, Number(event.target.value) || 0)) })} /></label>
              <p className="memory-note">手动编辑会立即应用；结束状态后仍可在历史记录中查看。</p>
              <div className="global-settings-modal-actions">
                <button className="secondary" onClick={() => setStateDraft(null)}>取消</button>
                <button className="primary" disabled={busy === "state"} onClick={() => void submitState()}>{busy === "state" ? "保存中…" : "保存状态"}</button>
              </div>
            </div>
          </div>
        )}

        {historyView && (
          <div className="global-settings-modal-backdrop">
            <div className="global-settings-modal memory-history-modal" role="dialog" aria-modal="true" aria-labelledby="memory-history-title">
              <button className="global-settings-close" onClick={() => setHistoryView(null)} aria-label="关闭"><X /></button>
              <h3 id="memory-history-title">状态 #{historyView.state.id} 的变更历史</h3>
              <p className="memory-note">{STATE_LABELS[historyView.state.state_type] || historyView.state.state_type} · {historyView.state.content}</p>
              {historyView.loading ? <p className="memory-note">正在读取变更记录…</p> : historyView.events.length ? historyView.events.map((event) => (
                <article className="memory-history-event" key={event.id}>
                  <strong>{formatTime(event.created_at)} · {ACTION_LABELS[event.action] || event.action}</strong>
                  <p>{event.content}</p>
                  <small>来源：{event.source_channel || "桌面端手动操作"}{event.source_message_ids?.length ? ` · 消息 ${event.source_message_ids.map((id) => `#${id}`).join(", ")}` : ""}{event.reason ? ` · 原因：${event.reason}` : ""}</small>
                </article>
              )) : <p className="memory-note">暂无变更记录</p>}
              <div className="global-settings-modal-actions"><button className="secondary" onClick={() => setHistoryView(null)}>关闭</button></div>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
