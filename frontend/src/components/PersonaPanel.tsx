import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Copy,
  Maximize2,
  Minimize2,
  PanelRightClose,
  PanelRightOpen,
  Loader2,
  Plus,
  Save,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import {
  lianxinApi,
  type PersonaPreview,
  type PersonaState,
} from "../services/lianxinApi";
import { parsePersonaPreview } from "../utils/personaPreview";
import { GrowthPanel } from "./GrowthPanel";

const PERSONA_FIELDS = [
  "summary", "identity", "appearance", "personality", "speaking_style",
  "habits", "relationship", "user_address", "boundaries", "custom_instructions",
] as const;
type PersonaField = (typeof PERSONA_FIELDS)[number];
const DRAFT_KEYS = ["profile_name", "assistant_name", ...PERSONA_FIELDS] as const;
type DraftKey = (typeof DRAFT_KEYS)[number];
type PersonaDraft = Record<DraftKey, string>;
type PersonaProfile = Record<string, any>;
type ToastKind = "success" | "error" | "info";
type ToastState = { id: number; kind: ToastKind; text: string };
type PendingAction = "save" | "activate" | "toggle" | "create" | "delete" | null;
type PaneId = "list" | "editor" | "preview";
type NavigationIntent = { kind: "select"; id: string } | { kind: "close" };
const FIELD_LABELS: Record<PersonaField, string> = {
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
const FIELD_SIZE: Record<PersonaField, "compact" | "medium" | "tall"> = {
  summary: "compact", identity: "tall", appearance: "medium",
  personality: "tall", speaking_style: "tall", habits: "medium",
  relationship: "medium", user_address: "compact", boundaries: "tall",
  custom_instructions: "medium",
};
const EMPTY_DRAFT = Object.fromEntries(
  DRAFT_KEYS.map((key) => [key, ""]),
) as PersonaDraft;

function profileToDraft(profile: PersonaProfile): PersonaDraft {
  return Object.fromEntries(
    DRAFT_KEYS.map((key) => [key, String(profile[key] ?? "")]),
  ) as PersonaDraft;
}

function findProfile(source: PersonaState | null, id: string) {
  return source?.profiles.find((item) => String(item.id) === id);
}

function formatError(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error);
  const start = raw.indexOf("{");
  const end = raw.lastIndexOf("}");
  if (start >= 0 && end > start) {
    try {
      const payload = JSON.parse(raw.slice(start, end + 1)) as {
        error?: unknown;
        message?: unknown;
      };
      const message = payload.error ?? payload.message;
      if (message) return String(message);
    } catch {
      return raw;
    }
  }
  return raw.replace(/^ApiRequestError:\s*\d+\s*/, "") || "操作失败";
}

function PersonaTextArea({ value, disabled, expanded, onChange }: {
  value: string;
  disabled: boolean;
  expanded: boolean;
  onChange: (value: string) => void;
}) {
  const input = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const element = input.current;
    if (!element || expanded) return;
    const resize = () => {
      element.style.height = "auto";
      element.style.height = `${element.scrollHeight + 2}px`;
    };
    resize();
    let width = element.getBoundingClientRect().width;
    const observer = new ResizeObserver(() => {
      const nextWidth = element.getBoundingClientRect().width;
      if (nextWidth !== width) {
        width = nextWidth;
        resize();
      }
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [value, expanded]);
  return <textarea ref={input} value={value} maxLength={20000} spellCheck={false}
    disabled={disabled} onChange={(event) => onChange(event.target.value)} />;
}
export function PersonaPanel({ onClose }: { onClose: () => void }) {
  const [state, setState] = useState<PersonaState | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [draft, setDraft] = useState<PersonaDraft>(EMPTY_DRAFT);
  const [savedDraft, setSavedDraft] = useState<PersonaDraft>(EMPTY_DRAFT);
  const [preview, setPreview] = useState<PersonaPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [loading, setLoading] = useState(true);
  const [pendingAction, setPendingAction] = useState<PendingAction>(null);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<PersonaProfile | null>(null);
  const [navigationIntent, setNavigationIntent] =
    useState<NavigationIntent | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createTemplate, setCreateTemplate] = useState<"" | "naga_style">("naga_style");
  const [pane, setPane] = useState<PaneId>("editor");
  const [previewVisible, setPreviewVisible] = useState(true);
  const [expandedField, setExpandedField] = useState<PersonaField | null>(null);
  const [growthOpen, setGrowthOpen] = useState(false);
  const [growthRevision, setGrowthRevision] = useState(0);
  const previewSequence = useRef(0);
  const toastSequence = useRef(0);

  const notify = useCallback((text: string, kind: ToastKind = "info") => {
    toastSequence.current += 1;
    setToast({ id: toastSequence.current, text, kind });
  }, []);

  const hydrateFromProfile = useCallback((profile: PersonaProfile) => {
    const next = profileToDraft(profile);
    setDraft(next);
    setSavedDraft(next);
  }, []);

  const loadState = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const next = await lianxinApi.personaState();
      setState(next);
      const activeId = String(next.snapshot.profile.id || "");
      const target = findProfile(next, activeId) ?? next.profiles[0];
      if (target) {
        setSelectedId(String(target.id));
        hydrateFromProfile(target);
      }
      setPreview(next.preview);
      setPreviewError(next.preview.error || "");
    } catch (error) {
      const message = formatError(error);
      setLoadError(message);
      notify(message, "error");
    } finally {
      setLoading(false);
    }
  }, [hydrateFromProfile, notify]);

  useEffect(() => { void loadState(); }, [loadState]);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 4500);
    return () => window.clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    if (!selectedId) return;
    const sequence = ++previewSequence.current;
    setPreviewLoading(true);
    setPreviewError("");
    const timer = window.setTimeout(() => {
      void lianxinApi
        .personaPreview({ id: selectedId, ...draft })
        .then((result) => {
          if (sequence !== previewSequence.current) return;
          setPreview(result.preview);
          setPreviewError(result.preview.error || "");
        })
        .catch((error) => {
          if (sequence !== previewSequence.current) return;
          setPreviewError(formatError(error));
        })
        .finally(() => {
          if (sequence === previewSequence.current) setPreviewLoading(false);
        });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [selectedId, draft, growthRevision]);

  const profile = findProfile(state, selectedId) ?? null;
  const activeId = String(state?.snapshot.profile.id || "");
  const activeProfile = findProfile(state, activeId);
  const isActive = Boolean(profile && String(profile.id) === activeId);
  const systemEnabled = Boolean(state?.snapshot.enabled);
  const busy = pendingAction !== null;
  const draftSignature = useMemo(
    () => JSON.stringify(DRAFT_KEYS.map((key) => draft[key] ?? "")),
    [draft],
  );
  const savedSignature = useMemo(
    () => JSON.stringify(DRAFT_KEYS.map((key) => savedDraft[key] ?? "")),
    [savedDraft],
  );
  const dirty = Boolean(profile) && draftSignature !== savedSignature;
  const canDelete = Boolean(
    profile && !isActive && String(profile.id) !== "default-lianxin" && !profile.is_builtin,
  );
  const deleteBlockReason = !profile
    ? ""
    : isActive
      ? "当前激活的人格不能删除，请先切换到其他人格。"
      : String(profile.id) === "default-lianxin" || profile.is_builtin
        ? "内置默认人格不能删除。"
        : "";
  const previewSections = useMemo(
    () => parsePersonaPreview(preview?.text || "", preview?.layers || []),
    [preview?.text, preview?.layers],
  );
  const activeSavedNotApplied = useMemo(() => {
    if (!activeProfile || !state?.snapshot.profile) return false;
    return JSON.stringify(profileToDraft(activeProfile))
      !== JSON.stringify(profileToDraft(state.snapshot.profile));
  }, [activeProfile, state?.snapshot.profile]);
  const selectedName = String(profile?.profile_name || "选择一个人格档案");
  const activeName = String(
    state?.snapshot.profile.profile_name || "未知",
  );

  const adoptProfile = useCallback((source: PersonaState, id: string) => {
    const target = findProfile(source, id);
    if (!target) return false;
    setSelectedId(String(target.id));
    hydrateFromProfile(target);
    setPreview(null);
    setPreviewError("");
    setPane("editor");
    setExpandedField(null);
    return true;
  }, [hydrateFromProfile]);

  const updateDraft = (key: DraftKey, value: string) => {
    setDraft((current) => ({ ...current, [key]: value }));
  };
  const requestClose = useCallback(() => {
    if (busy) {
      notify("操作正在进行，请稍候。", "info");
      return;
    }
    if (dirty) {
      setNavigationIntent({ kind: "close" });
      return;
    }
    onClose();
  }, [busy, dirty, notify, onClose]);

  const requestSelect = (id: string) => {
    if (busy) return;
    if (id === selectedId) {
      setPane("editor");
      return;
    }
    if (dirty) {
      setNavigationIntent({ kind: "select", id });
      return;
    }
    if (state) adoptProfile(state, id);
  };

  const persistDraft = async (
    mode: "save" | "activate",
  ): Promise<PersonaState | null> => {
    if (!profile || busy) return null;
    setPendingAction(mode);
    try {
      const payload = { id: selectedId, ...draft };
      const next = mode === "activate"
        ? await lianxinApi.personaSaveAndActivate(payload)
        : await lianxinApi.personaSave(payload);
      setState(next);
      const updated = findProfile(next, selectedId);
      if (updated) hydrateFromProfile(updated);
      if (mode === "activate") {
        setPreview(next.preview);
        setPreviewError(next.preview.error || "");
        notify(`已激活「${String(updated?.profile_name || selectedName)}」，新请求将使用该人格。`, "success");
      } else {
        notify("草稿已保存；当前运行人格未改变。", "success");
      }
      return next;
    } catch (error) {
      notify(formatError(error), "error");
      return null;
    } finally {
      setPendingAction(null);
    }
  };

  const continueNavigation = (intent: NavigationIntent, source: PersonaState) => {
    if (intent.kind === "close") {
      onClose();
      return;
    }
    adoptProfile(source, intent.id);
  };

  const saveAndContinue = async () => {
    if (!navigationIntent) return;
    const intent = navigationIntent;
    const next = await persistDraft("save");
    if (!next) return;
    setNavigationIntent(null);
    continueNavigation(intent, next);
  };

  const discardAndContinue = () => {
    if (!navigationIntent) return;
    const intent = navigationIntent;
    setNavigationIntent(null);
    if (intent.kind === "close") {
      onClose();
      return;
    }
    if (state) adoptProfile(state, intent.id);
  };
  const toggleSystem = async () => {
    if (!state || busy) return;
    setPendingAction("toggle");
    try {
      const next = await lianxinApi.personaToggle(!systemEnabled);
      setState(next);
      const nextName = String(next.snapshot.profile.profile_name || "当前人格");
      notify(
        next.snapshot.enabled
          ? `人格系统已启用，当前激活「${nextName}」。`
          : "人格系统已停用，对话将回退到旧版提示词。",
        "success",
      );
    } catch (error) {
      notify(formatError(error), "error");
    } finally {
      setPendingAction(null);
    }
  };

  const openCreateDialog = () => {
    if (busy) return;
    setCreateName(`新的人格 ${(state?.profiles.length || 0) + 1}`);
    setCreateTemplate("naga_style");
    setCreateOpen(true);
  };

  const createProfile = async () => {
    const name = createName.trim();
    if (!name || busy) {
      if (!name) notify("请填写人格档案名称。", "error");
      return;
    }
    const hadUnsavedChanges = dirty;
    setPendingAction("create");
    try {
      const next = await lianxinApi.personaCreate(name, createTemplate);
      setState(next);
      const createdId = String(next.created_id || "");
      const target = findProfile(next, createdId) ?? next.profiles.at(-1);
      setCreateOpen(false);
      notify(
        createTemplate === "naga_style"
          ? `已创建「${name}」，已套用 Naga 语言风格模板。`
          : `已创建「${name}」。`,
        "success",
      );
      if (!target) return;
      if (hadUnsavedChanges) {
        setNavigationIntent({ kind: "select", id: String(target.id) });
        return;
      }
      adoptProfile(next, String(target.id));
    } catch (error) {
      notify(formatError(error), "error");
    } finally {
      setPendingAction(null);
    }
  };
  const confirmDelete = async () => {
    if (!deleteTarget || busy) return;
    const target = deleteTarget;
    setPendingAction("delete");
    try {
      const next = await lianxinApi.personaDelete(String(target.id));
      setState(next);
      const fallback =
        findProfile(next, String(next.snapshot.profile.id)) ?? next.profiles[0];
      if (fallback) adoptProfile(next, String(fallback.id));
      setPreview(next.preview);
      setPreviewError(next.preview.error || "");
      setDeleteTarget(null);
      notify(`已删除「${String(target.profile_name || "未命名人格")}」。`, "success");
    } catch (error) {
      notify(formatError(error), "error");
    } finally {
      setPendingAction(null);
    }
  };

  const copyPrompt = async () => {
    const text = preview?.text || "";
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      notify("最终 Prompt 已复制。", "success");
    } catch (error) {
      notify(`复制失败：${formatError(error)}`, "error");
    }
  };

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (growthOpen) return;
      if (deleteTarget) {
        if (!busy) setDeleteTarget(null);
        return;
      }
      if (createOpen) {
        if (!busy) setCreateOpen(false);
        return;
      }
      if (navigationIntent) {
        if (!busy) setNavigationIntent(null);
        return;
      }
      requestClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [busy, createOpen, deleteTarget, navigationIntent, requestClose, growthOpen]);

  const activateLabel = pendingAction === "activate"
    ? "正在激活…"
    : !systemEnabled
      ? "启用并激活"
      : isActive && dirty
        ? "保存修改并重新激活"
        : isActive && activeSavedNotApplied
          ? "应用已保存的修改"
        : "保存并激活";
  const activateDisabled =
    !profile
    || busy
    || (isActive && !dirty && systemEnabled && !activeSavedNotApplied);
  if (growthOpen) return <GrowthPanel personaId={selectedId || activeId} profiles={state?.profiles || []}
    onClose={() => setGrowthOpen(false)} onChange={(growth) => {
      if (growth.persona_id === selectedId) {
        setState((previous) => previous ? { ...previous, growth } : previous);
      } else {
        void lianxinApi.personaGrowth(selectedId).then((current) => {
          setState((previous) => previous ? { ...previous, growth: current } : previous);
        }).catch((error) => notify(formatError(error), "error"));
      }
      setGrowthRevision((revision) => revision + 1);
    }} />;
  return (
    <div className="constellation-overlay persona-overlay">
      <div className="persona-frame">
        <header className="persona-header">
          <div className="persona-title-block">
            <span className="eyebrow">莲心系统 · 人格枢控</span>
            <h2>人格枢控</h2>
            <p>选择档案进行编辑，确认后再激活；当前运行版本不会被草稿悄悄覆盖。</p>
          </div>
          <div className="persona-header-actions">
            <div className="persona-header-badges">
              <span className={`persona-badge ${systemEnabled ? "is-success" : "is-off"}`}>
                {systemEnabled ? "人格系统已启用" : "人格系统已停用"}
              </span>
              <span className="persona-badge is-active" title={`当前激活：${activeName}`}>
                {systemEnabled ? "当前运行" : "上次激活"} · {activeName}
              </span>
              {dirty && <span className="persona-badge is-warning">有未保存修改</span>}
              {!dirty && activeSavedNotApplied && (
                <span className="persona-badge is-warning">已保存草稿尚未应用</span>
              )}
            </div>
            <button className="secondary-button" disabled={!state || busy} onClick={() => setGrowthOpen(true)}>成长轨迹</button>
            <button
              className="secondary-button persona-toggle"
              onClick={() => void toggleSystem()}
              disabled={!state || busy}
            >
              {pendingAction === "toggle" ? "处理中…" : systemEnabled ? "停用系统" : "启用系统"}
            </button>
            <button className="overlay-close" onClick={requestClose} aria-label="关闭人格枢控">
              <X size={18} />
            </button>
          </div>
        </header>

        <nav className="persona-mobile-tabs" aria-label="人格枢控视图">
          {([
            ["list", `档案 ${state?.profiles.length || 0}`],
            ["editor", "编辑"],
            ["preview", "预览"],
          ] as const).map(([id, label]) => (
            <button
              key={id}
              className={pane === id ? "is-active" : ""}
              onClick={() => setPane(id)}
              aria-current={pane === id ? "page" : undefined}
            >
              {label}
              {id === "editor" && dirty && <span className="persona-tab-dot" />}
            </button>
          ))}
        </nav>

        <div className={`persona-body persona-pane-${pane}${previewVisible ? "" : " persona-preview-hidden"}`}>
          <aside className="persona-sidebar" aria-label="人格档案列表">
            <div className="persona-section-title">
              <div>
                <strong>人格档案</strong>
                <span>{state?.profiles.length || 0} 份</span>
              </div>
              <button className="secondary-button" onClick={openCreateDialog} disabled={busy}>
                <Plus size={14} />
                新建
              </button>
            </div>
            <div className="persona-list">
              {(state?.profiles || []).map((item) => {
                const id = String(item.id);
                const itemActive = id === activeId;
                const itemSelected = id === selectedId;
                return (
                  <button
                    className={`persona-list-item ${itemSelected ? "is-selected" : ""} ${itemActive ? "is-active-profile" : ""}`}
                    key={id}
                    onClick={() => requestSelect(id)}
                    disabled={busy}
                    aria-pressed={itemSelected}
                    title={itemActive ? "当前激活的人格" : "选中后编辑，不会立即切换"}
                  >
                    <span className="persona-list-name">
                      {String(item.profile_name || "未命名人格")}
                    </span>
                    <span className="persona-list-meta">
                      <small>{String(item.assistant_name || "莲心")}</small>
                      {itemActive && <em>当前激活</em>}
                      {itemSelected && dirty && <em className="is-dirty">未保存</em>}
                    </span>
                  </button>
                );
              })}
            </div>
            <div className="persona-sidebar-footer">
              <p>点击档案只会进入编辑，激活需要明确确认。</p>
              <button
                className="persona-danger"
                onClick={() => profile && setDeleteTarget(profile)}
                disabled={!canDelete || busy}
              >
                <Trash2 size={14} />
                删除当前档案
              </button>
              {deleteBlockReason && <small>{deleteBlockReason}</small>}
            </div>
          </aside>
          <section className="persona-editor" aria-label="人格档案编辑">
            <div className="persona-editor-head">
              <div>
                <span className="eyebrow">档案编辑</span>
                <h3>{selectedName}</h3>
                <p>这里修改的是草稿；右侧预览会随输入实时编译。</p>
              </div>
              <div className="persona-editor-badges">
                {isActive && systemEnabled && <span className="persona-badge is-active">正在运行</span>}
                {dirty && <span className="persona-badge is-warning">草稿未保存</span>}
                <button className="persona-preview-toggle secondary-button"
                  onClick={() => setPreviewVisible((visible) => !visible)}
                  aria-expanded={previewVisible} aria-controls="persona-preview-panel">
                  {previewVisible ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}
                  {previewVisible ? "收起预览" : "显示预览"}
                </button>
              </div>
            </div>

            {loading ? (
              <div className="persona-empty">
                <Loader2 className="persona-spin" size={18} />
                正在读取人格档案…
              </div>
            ) : loadError && !state ? (
              <div className="persona-empty is-error">
                <AlertTriangle size={18} />
                <span>{loadError}</span>
                <button className="secondary-button" onClick={() => void loadState()}>
                  重试
                </button>
              </div>
            ) : profile ? (
              <div className={`persona-form${expandedField ? " is-focused" : ""}`}>
                <label className="persona-field persona-field-name">
                  <span>档案名称</span>
                  <input
                    value={draft.profile_name}
                    maxLength={120}
                    autoComplete="off"
                    disabled={busy}
                    onChange={(event) => updateDraft("profile_name", event.target.value)}
                  />
                </label>
                <label className="persona-field persona-field-name">
                  <span>助手名称</span>
                  <input
                    value={draft.assistant_name}
                    maxLength={120}
                    autoComplete="off"
                    disabled={busy}
                    onChange={(event) => updateDraft("assistant_name", event.target.value)}
                  />
                </label>
                {PERSONA_FIELDS.map((field) => (
                  <div
                    className={`persona-field persona-field-${FIELD_SIZE[field]}${expandedField === field ? " is-expanded" : ""}`}
                    key={field}
                  >
                    <div className="persona-field-label">
                      <span id={`persona-label-${field}`}>{FIELD_LABELS[field]}</span>
                      <div className="persona-field-tools">
                        <small>{draft[field].length.toLocaleString()} 字符</small>
                        <button type="button" className="persona-field-expand" disabled={busy}
                          aria-expanded={expandedField === field}
                          aria-label={`${expandedField === field ? "退出" : "专注编辑"}${FIELD_LABELS[field]}`}
                          onClick={() => setExpandedField(expandedField === field ? null : field)}>
                          {expandedField === field ? <Minimize2 size={12} /> : <Maximize2 size={12} />}
                          {expandedField === field ? "返回全部字段" : "专注编辑"}
                        </button>
                      </div>
                    </div>
                    <label className="persona-textarea-wrap" aria-labelledby={`persona-label-${field}`}>
                      <span className="persona-sr-only">{FIELD_LABELS[field]}</span>
                      <PersonaTextArea value={draft[field]} disabled={busy} expanded={expandedField === field}
                        onChange={(value) => updateDraft(field, value)} />
                    </label>
                  </div>
                ))}
              </div>
            ) : (
              <div className="persona-empty">选择左侧的人格档案开始编辑。</div>
            )}
            <footer className="persona-editor-footer">
              <div className={`persona-save-hint ${dirty ? "is-dirty" : ""}`}>
                {dirty
                  ? "有未保存修改：保存草稿不会影响当前对话，激活后才会用于新请求。"
                  : isActive && !systemEnabled
                    ? "人格系统已停用；启用并激活后，新请求才会使用此档案。"
                  : isActive && activeSavedNotApplied
                    ? "档案已保存，但当前运行版本仍是旧快照；点击“应用已保存的修改”后才会生效。"
                  : isActive
                    ? "当前档案与运行版本一致。"
                    : "当前档案尚未激活。"}
              </div>
              <div className="persona-editor-actions">
                <button
                  className="secondary-button"
                  onClick={() => void persistDraft("save")}
                  disabled={!profile || busy || !dirty}
                >
                  <Save size={14} />
                  {pendingAction === "save" ? "正在保存…" : "保存草稿"}
                </button>
                <button
                  className="persona-primary"
                  onClick={() => void persistDraft("activate")}
                  disabled={activateDisabled}
                  title={
                    isActive && !dirty && systemEnabled && !activeSavedNotApplied
                      ? "当前人格已经处于激活状态"
                      : isActive && activeSavedNotApplied
                        ? "将磁盘中已保存的档案重新应用到当前运行快照"
                        : "保存当前草稿并立即切换到此人格"
                  }
                >
                  <Sparkles size={14} />
                  {activateLabel}
                </button>
              </div>
            </footer>
          </section>

          <aside id="persona-preview-panel" className="persona-preview" aria-label="人格 Prompt 实时预览">
            <div className="persona-preview-head">
              <div>
                <span className="eyebrow">主聊天场景 · 完整档案</span>
                <h3>草稿实时预览</h3>
              </div>
              <div className="persona-preview-tools">
                <span>约 {preview?.estimated_tokens || 0} tokens</span>
                <button
                  className="persona-icon-button"
                  onClick={() => void copyPrompt()}
                  disabled={!preview?.text}
                  title="复制草稿预览"
                  aria-label="复制草稿预览"
                >
                  <Copy size={14} />
                </button>
              </div>
            </div>
            <p className="persona-preview-note">预览不代表已激活；实际请求还会按场景、精简模式和动态上下文调整。</p>
            {previewLoading && (
              <div className="persona-preview-loading" aria-live="polite">
                <Loader2 className="persona-spin" size={13} />
                正在根据草稿更新预览…
              </div>
            )}
            <div className="persona-layers">
              {(preview?.layers || []).map((layer, index) => (
                <span key={`${layer}-${index}`}>{layer}</span>
              ))}
            </div>
            {previewError && (
              <div className="persona-inline-error" role="alert">
                <AlertTriangle size={14} />
                {previewError}
              </div>
            )}
            {previewSections.length ? (
              <div className="persona-prompt-layers">
                {previewSections.map((section, index) => (
                  <details
                    className="persona-prompt-layer"
                    key={section.key}
                    open={index === 0}
                  >
                    <summary>
                      <span>{section.name}</span>
                      <small>{section.body.length.toLocaleString()} 字符</small>
                    </summary>
                    <pre>{section.body}</pre>
                  </details>
                ))}
              </div>
            ) : (
              <div className="persona-empty">
                {previewError ? "预览编译失败" : "正在等待 Prompt 预览…"}
              </div>
            )}
            <button className="persona-growth" onClick={() => setGrowthOpen(true)} disabled={!state || busy}>
              <strong>成长轨迹</strong>
              <span>
                {state?.growth.summary
                  ? `当前生效 ${state.growth.summary.counts?.applied || 0} 项 · 本周变化 ${state.growth.summary.weekly_changes || 0} 次`
                  : "暂无成长数据"} · 打开共同成长中心
              </span>
            </button>
          </aside>
        </div>

        {toast && (
          <div
            className={`persona-toast is-${toast.kind}`}
            role={toast.kind === "error" ? "alert" : "status"}
            aria-live={toast.kind === "error" ? "assertive" : "polite"}
          >
            {toast.kind === "error" ? <AlertTriangle size={15} /> : <CheckCircle2 size={15} />}
            <span>{toast.text}</span>
            <button onClick={() => setToast(null)} aria-label="关闭提示">
              <X size={13} />
            </button>
          </div>
        )}
        {createOpen && (
          <div className="persona-modal-backdrop">
            <div className="persona-modal" role="dialog" aria-modal="true" aria-labelledby="persona-create-title">
              <div className="persona-modal-head">
                <div>
                  <span className="eyebrow">新建人格</span>
                  <h3 id="persona-create-title">选择起始模板</h3>
                </div>
                <button className="persona-icon-button" onClick={() => !busy && setCreateOpen(false)} aria-label="关闭新建人格">
                  <X size={16} />
                </button>
              </div>
              <label className="persona-modal-field">
                <span>档案名称</span>
                <input
                  autoFocus
                  value={createName}
                  maxLength={120}
                  onChange={(event) => setCreateName(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void createProfile();
                  }}
                />
              </label>
              <div className="persona-template-list" role="radiogroup" aria-label="人格模板">
                <label className={`persona-template-option ${createTemplate === "naga_style" ? "is-selected" : ""}`}>
                  <input
                    type="radio"
                    name="persona-template"
                    checked={createTemplate === "naga_style"}
                    onChange={() => setCreateTemplate("naga_style")}
                  />
                  <span>
                    <strong>Naga 语言风格 <em>推荐</em></strong>
                    <small>保留莲心身份，加入 Naga 的松弛、画面感与理性任务切换。</small>
                  </span>
                </label>
                <label className={`persona-template-option ${createTemplate === "" ? "is-selected" : ""}`}>
                  <input
                    type="radio"
                    name="persona-template"
                    checked={createTemplate === ""}
                    onChange={() => setCreateTemplate("")}
                  />
                  <span>
                    <strong>默认莲心</strong>
                    <small>复制当前默认莲心，从官方人格骨架开始修改。</small>
                  </span>
                </label>
              </div>
              <div className="persona-modal-actions">
                <button className="secondary-button" onClick={() => !busy && setCreateOpen(false)} disabled={busy}>
                  取消
                </button>
                <button className="persona-primary" onClick={() => void createProfile()} disabled={busy}>
                  {pendingAction === "create" ? "正在创建…" : "创建档案"}
                </button>
              </div>
            </div>
          </div>
        )}
        {deleteTarget && (
          <div className="persona-modal-backdrop">
            <div className="persona-modal is-danger" role="alertdialog" aria-modal="true" aria-labelledby="persona-delete-title">
              <div className="persona-modal-head">
                <div>
                  <span className="eyebrow">危险操作</span>
                  <h3 id="persona-delete-title">删除「{String(deleteTarget.profile_name || "未命名人格")}」？</h3>
                </div>
                <button className="persona-icon-button" onClick={() => !busy && setDeleteTarget(null)} aria-label="取消删除">
                  <X size={16} />
                </button>
              </div>
              <p className="persona-modal-copy">
                这个操作会删除人格档案文件及其备份，无法撤销。当前正在运行的人格不会受影响。
              </p>
              <div className="persona-modal-actions">
                <button className="secondary-button" onClick={() => !busy && setDeleteTarget(null)} disabled={busy}>
                  取消
                </button>
                <button className="persona-danger-button" onClick={() => void confirmDelete()} disabled={busy}>
                  <Trash2 size={14} />
                  {pendingAction === "delete" ? "正在删除…" : "确认删除"}
                </button>
              </div>
            </div>
          </div>
        )}
        {navigationIntent && (
          <div className="persona-modal-backdrop">
            <div className="persona-modal" role="alertdialog" aria-modal="true" aria-labelledby="persona-discard-title">
              <div className="persona-modal-head">
                <div>
                  <span className="eyebrow">未保存修改</span>
                  <h3 id="persona-discard-title">要保存当前草稿吗？</h3>
                </div>
                <button className="persona-icon-button" onClick={() => !busy && setNavigationIntent(null)} aria-label="取消离开">
                  <X size={16} />
                </button>
              </div>
              <p className="persona-modal-copy">
                {navigationIntent.kind === "close"
                  ? "关闭人格枢控后，当前未保存的修改会丢失。"
                  : "切换到另一个档案后，当前未保存的修改会丢失。"}
              </p>
              <div className="persona-modal-actions is-three">
                <button className="secondary-button" onClick={() => !busy && setNavigationIntent(null)} disabled={busy}>
                  取消
                </button>
                <button className="persona-discard-button" onClick={discardAndContinue} disabled={busy}>
                  放弃修改
                </button>
                <button className="persona-primary" onClick={() => void saveAndContinue()} disabled={busy}>
                  {pendingAction === "save" ? "正在保存…" : "保存后继续"}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
