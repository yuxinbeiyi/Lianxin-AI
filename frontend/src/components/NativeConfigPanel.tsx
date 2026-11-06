import { useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronRight, X } from "lucide-react";
import { open } from "@tauri-apps/plugin-dialog";
import { lianxinApi, type ConfigPanelDomain, type ConfigPanelTask, type ConfigPanelValue, type NativeConfigState } from "../services/lianxinApi";
import { confirmConfigAction } from "../utils/nativeConfig";
import "../styles/global-settings.css";
import "../styles/native-config.css";

function message(error: unknown) {
  if (!(error instanceof Error)) return "设置操作未完成";
  try { return JSON.parse(error.message).error || error.message; } catch { return error.message; }
}
const draftKey = (data: NativeConfigState | null) => JSON.stringify([data?.sections, data?.output?.silent_mode, data?.catalog && Object.values(data.catalog).map(items => items.map(item => [item.id, item.checked]))]);

export function useNativeConfig(domain: ConfigPanelDomain, onClose: () => void) {
  const [draft, setDraft] = useState<NativeConfigState | null>(null);
  const [saved, setSaved] = useState<NativeConfigState | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [clears, setClears] = useState<string[]>([]);
  const [task, setTask] = useState<ConfigPanelTask | undefined>();
  const busyRef = useRef(false);
  const dirty = draftKey(draft) !== draftKey(saved) || clears.length > 0;
  const locked = busy || task?.status === "running";
  const accept = (remote: NativeConfigState, keep = false, rebase = false) => {
    setSaved(structuredClone(remote));
    if (keep && !rebase && draft && draft.revision !== remote.revision) setError("配置已被其它操作修改；当前草稿保留，请重新加载核对后再保存");
    setDraft(current => keep && current ? { ...current, revision: rebase ? remote.revision : current.revision, migration: remote.migration } : structuredClone(remote));
    setTask(remote.task);
    if (!keep) setClears([]);
  };
  useEffect(() => {
    let live = true;
    void lianxinApi.nativeConfig(domain).then(remote => { if (live) accept(remote); }).catch(cause => { if (live) setError(message(cause)); });
    return () => { live = false; };
  }, [domain]);
  useEffect(() => {
    if (task?.status !== "running") return;
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await lianxinApi.nativeConfigRead<{ task: ConfigPanelTask }>(domain, "job");
        if (!live) return;
        if (result.task.status !== "running") {
          const remote = await lianxinApi.nativeConfig(domain);
          if (live) { accept(remote, true, result.task.committedRevision === remote.revision); setNotice(result.task.message); }
          return;
        }
        setTask(result.task); setError("");
      } catch (cause) { if (live) setError(`任务状态暂时不可读：${message(cause)}；不会假定后台任务已取消`); }
      if (live) timer = setTimeout(poll, 1000);
    };
    timer = setTimeout(poll, 300);
    return () => { live = false; clearTimeout(timer); };
  }, [domain, task?.id, task?.status]);
  useEffect(() => {
    const prevent = (event: BeforeUnloadEvent) => { if (dirty || locked) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [dirty, locked]);
  const run = async (operation: () => Promise<void>, allowTask = false) => {
    if (busyRef.current || !allowTask && task?.status === "running") return;
    busyRef.current = true; setBusy(true); setError("");
    try { await operation(); } catch (cause) { setError(message(cause)); }
    finally { busyRef.current = false; setBusy(false); }
  };
  const payload = () => ({ revision: draft!.revision, sections: Object.fromEntries(Object.entries(draft!.sections).map(([section, fields]) => [section, Object.fromEntries(Object.entries(fields).filter(([field]) => !field.endsWith("_set")))])), clearSecrets: clears });
  const patch = (section: string, field: string, value: ConfigPanelValue) => setDraft(current => current ? { ...current, sections: { ...current.sections, [section]: { ...current.sections[section], [field]: value } } } : current);
  const submit = (action: string, extra: Record<string, unknown> = {}, confirmation = "", keep = false) => run(async () => {
    if (confirmation && !await confirmConfigAction(confirmation)) return;
    const remote = await lianxinApi.nativeConfigAction(domain, action, { ...payload(), ...extra, confirmed: Boolean(confirmation) });
    accept(remote, keep); setNotice(action === "save" ? "配置已保存并应用" : "操作已提交；请核对实际结果");
  });
  const close = (): void => void run(async () => {
    if (!dirty || await confirmConfigAction("有未保存的设置，确定放弃并关闭？音量、已试听配置及已执行动作不会撤销。")) onClose();
  });
  const reload = () => void run(async () => {
    if (dirty && !await confirmConfigAction("重新加载会放弃未保存的草稿，是否继续？")) return;
    accept(await lianxinApi.nativeConfig(domain)); setNotice("已读取实际配置");
  });
  const help = (name: string) => void run(async () => { await lianxinApi.nativeConfigAction(domain, "help", { name }); setNotice("已请求在系统浏览器打开帮助入口"); });
  const choose = async (directory = false, extensions = ["wav"]) => {
    if (!("__TAURI_INTERNALS__" in window)) throw new Error("目录/文件选择需要桌面版；浏览器预览可手动填写路径");
    const result = await open({ directory, multiple: false, ...(directory ? {} : { filters: [{ name: extensions.join(" / "), extensions }] }) });
    return typeof result === "string" ? result : null;
  };
  return { domain, draft, saved, setDraft, setSaved, accept, error, setError, notice, setNotice, busy, locked, clears, setClears, dirty, task, run, payload, patch, submit, close, reload, help, choose };
}
export type ConfigController = ReturnType<typeof useNativeConfig>;

export function ConfigCard({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return <section className="global-settings-card"><header><h3>{title}</h3>{hint && <p>{hint}</p>}</header>{children}</section>;
}

export function ConfigField({ controller, section, field, label, options, min, max, step, secret = false, hint }: { controller: ConfigController; section: string; field: string; label: string; options?: Array<[ConfigPanelValue, string]>; min?: number; max?: number; step?: number; secret?: boolean; hint?: string }) {
  const [visible, setVisible] = useState(false);
  const [revealed, setRevealed] = useState("");
  const values = controller.draft!.sections[section];
  const value = values[field];
  const identifier = `${section}.${field}`;
  const clearing = controller.clears.includes(identifier);
  useEffect(() => { setVisible(false); setRevealed(""); }, [controller.draft?.revision]);
  const show = () => void controller.run(async () => {
    if (visible) { setVisible(false); setRevealed(""); return; }
    if (!value && values[`${field}_set`] && !clearing) setRevealed((await lianxinApi.revealNativeConfig(controller.domain, identifier, controller.draft!.revision)).value);
    setVisible(true);
  });
  return <div className="native-config-field"><label className="global-settings-field"><span>{label}</span>{options ? <select aria-label={label} value={String(value)} onChange={event => controller.patch(section, field, options.find(([key]) => String(key) === event.target.value)![0])}>{options.map(([key, text]) => <option key={String(key)} value={String(key)}>{text}</option>)}</select> : typeof value === "boolean" ? <input aria-label={label} type="checkbox" checked={value} onChange={event => controller.patch(section, field, event.target.checked)} /> : <input aria-label={label} type={secret && !visible ? "password" : typeof value === "number" ? section === "tts" && min !== undefined ? "range" : "number" : "text"} min={min} max={max} step={step} value={secret && visible && !value && !clearing ? revealed : String(value ?? "")} autoComplete="off" placeholder={secret && values[`${field}_set`] ? "已配置；留空保留" : ""} disabled={clearing} onChange={event => { setRevealed(""); controller.patch(section, field, typeof value === "number" ? Number(event.target.value) : event.target.value); }} />}{typeof value === "number" && section === "tts" && min !== undefined && <output className="native-config-range-value">{value}（{min}–{max}）</output>}</label>{hint && <small>{hint}</small>}{secret && <div className="native-config-secret"><button type="button" disabled={clearing} onClick={show}>{visible ? "隐藏" : "显示"}</button><span>{values[`${field}_set`] ? "已保存凭据，空白不会覆盖" : "尚未保存凭据"}</span><label><input type="checkbox" checked={clearing} onChange={event => { setRevealed(""); setVisible(false); controller.patch(section, field, ""); controller.setClears(current => event.target.checked ? [...current, identifier] : current.filter(item => item !== identifier)); }} />保存时清除</label></div>}</div>;
}

export function NativeConfigFrame({ controller, title, eyebrow, hint, groups, active, setActive, children, save, footerActions }: { controller: ConfigController; title: string; eyebrow: string; hint: string; groups: Array<[string, string]>; active: string; setActive: (value: string) => void; children: ReactNode; save: () => void; footerActions?: ReactNode }) {
  const content = useRef<HTMLElement>(null);
  return <div className="global-settings-overlay"><div className="global-settings-shell native-config-shell"><header className="global-settings-header"><div><div className="eyebrow">{eyebrow}</div><h2>{title}</h2><p>{hint}</p></div><button data-settings-close className="global-settings-close" aria-label={`关闭${title}`} onClick={controller.close}><X size={18} /></button></header><div className="global-settings-body"><nav className="global-settings-nav" aria-label={`${title}分组`}>{groups.map(([id, label]) => <button key={id} className={id === active ? "is-active" : ""} aria-current={id === active ? "page" : undefined} onClick={() => { setActive(id); if (content.current) content.current.scrollTop = 0; }}><strong>{label}</strong><ChevronRight size={14} /></button>)}</nav><main className="global-settings-content" ref={content}>{controller.error && <p role="alert" className="global-settings-error">{controller.error}</p>}{controller.draft ? <fieldset disabled={controller.locked} className="native-config-fieldset">{children}</fieldset> : <p role="status">正在读取配置；旧后端需重启以加载新接口。</p>}</main></div><footer className="global-settings-footer"><div className="native-config-feedback" role="status"><p>{controller.busy ? "正在处理，请稍候…" : controller.notice || (controller.dirty ? "有未保存的修改" : "配置与当前后端一致")}</p>{controller.task && controller.task.status !== "idle" && <p data-task-status={controller.task.status}>{controller.task.message}{controller.task.status === "running" && controller.task.progress !== null && ` · ${controller.task.progress}%`}</p>}{controller.task?.status === "running" && <small>后台任务未结束，暂不能关闭；关窗不会取消模型下载或供应商请求。</small>}</div>{footerActions}<button className="secondary" disabled={controller.locked} onClick={controller.reload}>重新加载</button><button className="secondary" disabled={controller.locked} onClick={controller.close}>取消</button><button className="primary" disabled={controller.locked || !controller.draft} onClick={save}>保存并应用</button></footer></div></div>;
}
