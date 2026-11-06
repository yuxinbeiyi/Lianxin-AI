import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronRight, RefreshCw, Save, X } from "lucide-react";
import { lianxinApi, type ProactiveBehavior, type ProactivePanelState, type ProactiveStatus } from "../services/lianxinApi";
import { ProactiveBilibiliPanel, type BilibiliPanelHandle } from "./ProactiveBilibiliPanel";
import { confirmProactiveAction } from "../utils/proactiveConfirmation";
import "../styles/global-settings.css";
import "../styles/proactive.css";

const groups = [
  { id: "general", label: "总体调度", hint: "通道、节奏与时间权重" },
  { id: "normal", label: "普通聊天", hint: "轻量问候与记忆联动" },
  { id: "observe", label: "调皮观察", hint: "屏幕、摄像头与观察预告" },
  { id: "bilibili", label: "B站冲浪", hint: "兴趣、授权、互动与历史" },
  { id: "slack", label: "莲心摸鱼", hint: "闲时探索与陪伴动作" },
] as const;
const behaviors: Array<[ProactiveBehavior, string]> = [["normal", "普通聊天"], ["observe", "调皮观察"], ["bilibili", "B站冲浪"], ["slack", "莲心摸鱼"]];
export const slackActions = [
  ["supplement_diary", "后续笔迹"], ["review_old_diary", "重温书页"], ["search_old_topic", "搜索旧话题"], ["remind_todo", "提醒 Todo"], ["random_question", "随机提问"], ["weather_chitchat", "天气碎碎念"],
  ["read_local_files", "读本地文件"], ["browser_history", "浏览器历史"], ["check_cpu_disk", "CPU / 磁盘"], ["check_recycle_bin", "回收站"],
  ["remind_rest", "提醒休息"], ["remind_water", "提醒喝水"], ["anniversary_remind", "纪念日提醒"], ["next_song", "切歌放音乐"],
] as const;

function Card({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return <section className="global-settings-card"><header><h3>{title}</h3>{hint && <p>{hint}</p>}</header>{children}</section>;
}
function message(error: unknown) {
  if (!(error instanceof Error)) return "操作失败，请稍后重试";
  try { return JSON.parse(error.message).error || error.message; } catch { return error.message; }
}

export function ProactivePanel({ onClose }: { onClose: () => void }) {
  const [draft, setDraft] = useState<ProactivePanelState | null>(null);
  const [saved, setSaved] = useState<ProactivePanelState | null>(null);
  const [active, setActive] = useState<typeof groups[number]["id"]>("general");
  const [runtime, setRuntime] = useState<ProactiveStatus>({ ready: false, running: false });
  const [statusError, setStatusError] = useState("");
  const [notice, setNotice] = useState("正在读取主动聊天设置…");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [biliDirty, setBiliDirty] = useState(false);
  const [biliBusy, setBiliBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [cameraItems, setCameraItems] = useState<Array<{ index: number; label: string }>>([]);
  const [countdown, setCountdown] = useState(0);
  const [debugAction, setDebugAction] = useState<string>(slackActions[0][0]);
  const [biliVersion, setBiliVersion] = useState(0);
  const content = useRef<HTMLElement>(null);
  const bili = useRef<BilibiliPanelHandle>(null);
  const busyRef = useRef(false);
  const confirmingRef = useRef(false);
  const delay = useRef<ReturnType<typeof setInterval> | null>(null);
  const schedulerDirty = !!draft && !!saved && JSON.stringify(draft.settings) !== JSON.stringify(saved.settings);
  const dirty = schedulerDirty || biliDirty;
  const blocked = busy || biliBusy || confirming || countdown > 0;
  const updateBiliDirty = useCallback((value: boolean) => setBiliDirty(value), []);
  const updateBiliBusy = useCallback((value: boolean) => setBiliBusy(value), []);
  const accept = (state: ProactivePanelState) => { setDraft(structuredClone(state)); setSaved(structuredClone(state)); };
  useEffect(() => {
    let live = true;
    lianxinApi.proactivePanel().then(state => { if (live) { accept(state); setNotice("修改不会自动应用；调试使用已保存配置"); } }).catch(cause => { if (live) setError(message(cause)); });
    const sync = async () => {
      try { const state = await lianxinApi.proactiveStatus(); if (live) { setRuntime(state); setStatusError(""); } }
      catch { if (live) setStatusError("运行状态暂不可读取"); }
    };
    void sync();
    const timer = window.setInterval(() => void sync(), 3000);
    return () => { live = false; window.clearInterval(timer); if (delay.current) clearInterval(delay.current); };
  }, []);
  useEffect(() => {
    const prevent = (event: BeforeUnloadEvent) => { if (dirty || busyRef.current || confirmingRef.current || biliBusy) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [dirty, biliBusy]);
  const cancelCountdown = () => { if (delay.current) clearInterval(delay.current); delay.current = null; setCountdown(0); };
  const confirmAction = async (text: string) => {
    if (confirmingRef.current) return false;
    confirmingRef.current = true; setConfirming(true);
    try { return await confirmProactiveAction(text); }
    catch (cause) { setError(message(cause)); return false; }
    finally { confirmingRef.current = false; setConfirming(false); }
  };
  const close = async () => {
    if (busyRef.current || confirmingRef.current || biliBusy) { setNotice("操作或确认尚未结束，请稍候；关闭窗口不会取消后台动作"); return; }
    if (dirty && !await confirmAction("还有未保存的设置、标签或评论正文，确定放弃并关闭？已明确应用的修改不会撤销。")) return;
    cancelCountdown(); onClose();
  };
  const run = async (operation: () => Promise<void>) => {
    if (busyRef.current || confirmingRef.current || biliBusy) return;
    busyRef.current = true; setBusy(true); setError("");
    try { await operation(); } catch (cause) { setError(message(cause)); setNotice("操作未完成，请查看错误提示"); }
    finally { busyRef.current = false; setBusy(false); }
  };
  const patch = (field: string, value: unknown) => setDraft(current => current ? { ...current, settings: { ...current.settings, [field]: value } } : current);
  const toggle = (field: string, label: string, hint?: string) => <label className="global-settings-toggle"><span><strong>{label}</strong>{hint && <small>{hint}</small>}</span><input type="checkbox" checked={Boolean(draft?.settings[field])} onChange={event => patch(field, event.target.checked)} /><i aria-hidden="true" /></label>;
  const number = (field: string, label: string, minimum: number, maximum: number, unit = "分钟") => <label className="global-settings-field"><span>{label}</span><input type="number" aria-label={label} min={minimum} max={maximum} step={1} value={Number(draft?.settings[field])} onChange={event => patch(field, event.target.value === "" ? "" : Number(event.target.value))} /><small>{unit}</small></label>;
  const cooldown = (behavior: ProactiveBehavior) => <label className="global-settings-field"><span>行为冷却</span><input aria-label={`${behavior}冷却`} type="number" min={0} max={1440} value={draft!.settings.behavior_cooldowns[behavior]} onChange={event => patch("behavior_cooldowns", { ...draft!.settings.behavior_cooldowns, [behavior]: event.target.value === "" ? "" : Number(event.target.value) })} /><small>分钟 · 0 表示无单项冷却</small></label>;
  const save = () => void run(async () => {
    if (!draft) return;
    await lianxinApi.validateProactivePanel(draft.revision, draft.settings);
    const hadBiliChanges = bili.current?.settingsDirty;
    if (hadBiliChanges && !await bili.current?.applySettings()) throw new Error("B站互动设置未保存，请查看 B站页提示；总体调度尚未修改");
    try { accept(await lianxinApi.saveProactivePanel(draft.revision, draft.settings)); }
    catch (cause) { throw new Error(`${hadBiliChanges ? "B站互动设置已应用，但总体调度未保存。" : ""}${message(cause)}`); }
    setNotice("调度与互动设置已应用；兴趣标签和评论正文仍需各自明确保存，不会自动登录、生成或发送评论");
  });
  const reload = async () => {
    if (dirty && !await confirmAction("重新加载会放弃所有未保存修改，继续？")) return;
    void run(async () => { accept(await lianxinApi.proactivePanel()); setBiliVersion(current => current + 1); setBiliDirty(false); setNotice("已重新加载保存值"); });
  };
  const trigger = async (mode: string, action = "") => {
    if (delay.current || busyRef.current || confirmingRef.current || biliBusy) return;
    if (schedulerDirty || bili.current?.settingsDirty) { setError("请先保存设置，再调试，避免草稿与实际执行配置不一致"); return; }
    if (!await confirmAction(mode === "bilibili" ? "即将实际执行兴趣冲浪；如已开启自动评论，可能公开发布评论。继续？" : "即将实际触发一次主动行为，可能调用模型、工具或读取屏幕 / 摄像头。继续？")) return;
    const execute = () => void run(async () => { setNotice("正在等待后台启动调试…"); const result = await lianxinApi.proactiveTrigger(mode, action); if (!result.ok) throw new Error(result.error || "调试未受理"); setNotice(result.started ? "后台已启动本次调试，不代表生成或发送成功；请查看运行状态和主聊天区实际结果" : "请求已入队，不代表执行成功；请查看运行状态和主聊天区实际结果"); setRuntime(await lianxinApi.proactiveStatus()); });
    if (mode === "screenshot" || mode === "camera") {
      let remaining = 3; setCountdown(remaining);
      delay.current = setInterval(() => { remaining -= 1; setCountdown(remaining); if (remaining <= 0) { cancelCountdown(); execute(); } }, 1000);
    } else execute();
  };
  const debugReason = blocked ? "正在处理操作或确认，请稍候" : runtime.running ? "主动行为正在执行，请等待结束" : statusError || (!runtime.ready ? "主动聊天运行时尚未就绪，请等待或刷新状态" : "");
  const debug = (mode: string, label: string, action = "") => <div className="proactive-debug-action"><button type="button" title={debugReason || "使用已保存配置调试"} disabled={!!debugReason} onClick={() => void trigger(mode, action)}>{label}</button>{debugReason && <small role="status">{debugReason}</small>}</div>;
  const weightsTotal = draft ? Object.values(draft.settings.behavior_weights).reduce((total, value) => total + Number(value), 0) : 0;
  const probability = draft ? Math.min(.3, (draft.settings.weights[new Date().getHours()] / 10) * (Number(draft.settings.frequency) / 30) * draft.baseRate * 1.5) * 100 : 0;
  return <div className="global-settings-overlay proactive-overlay"><div className="global-settings-shell">
    <header className="global-settings-header"><div><div className="eyebrow">PRESENCE / PROACTIVE CHAT</div><h2>主动聊天</h2><p>沿用原有统一调度 · 每轮只选一种主动行为</p></div><button className="global-settings-close" data-settings-close aria-label="关闭主动聊天" onClick={close}><X size={18} /></button></header>
    <div className="global-settings-body"><nav className="global-settings-nav" aria-label="主动聊天分组">{groups.map(group => <button key={group.id} className={active === group.id ? "is-active" : ""} aria-current={active === group.id ? "page" : undefined} onClick={() => { setActive(group.id); if (content.current) content.current.scrollTop = 0; }}><span><strong>{group.label}</strong><small>{group.hint}</small></span><ChevronRight size={14} /></button>)}</nav>
      <main className="global-settings-content" ref={content}>
        {error && <p className="global-settings-error" role="alert">{error}</p>}
        <div className="proactive-runtime" role="status"><strong>{statusError || (runtime.running ? "主动行为执行中" : runtime.ready ? "运行时就绪" : "运行时尚未就绪")}</strong><span>最近结果：{runtime.lastResult || "暂无"}{runtime.lastResultText ? ` · ${runtime.lastResultText}` : ""}</span><small>最后运行：{runtime.lastFireTime ? new Date(runtime.lastFireTime * 1000).toLocaleString() : "暂无"} · 执行 {runtime.runCount ?? 0} · 成功 {runtime.successCount ?? 0} · 失败 {runtime.failCount ?? 0}</small><div className="proactive-actions"><button disabled={blocked} onClick={() => void run(async () => { setRuntime(await lianxinApi.proactiveStatus()); setStatusError(""); setNotice("运行状态已刷新；没有重新载入或覆盖草稿"); })}>刷新运行状态（只读）</button>{dirty && <small>有未保存的修改</small>}</div></div>
        {draft && <fieldset className="proactive-fieldset" disabled={blocked}>
          {active === "general" && <>
            <Card title="主动聊天通道" hint="音乐感想独立于桌面主动聊天开关。QQ 送达与切歌仍取决于现有运行时桥接。">{toggle("desktop_enabled", "桌面主动聊天")}{toggle("qq_enabled", "QQ 主动聊天")}{toggle("music_feedback_enabled", "音乐感想", "保留现有音乐监听参数，本面板不覆盖高级配置")}</Card>
            <Card title="聊天节奏" hint="频率是概率强度，不是每日硬预算。全局冷却、用户活动和空会话保护仍生效。">{number("frequency", "频率", 1, 50, "约条 / 天")}{number("min_interval_minutes", "最短全局间隔", 10, 120)}{number("user_defer_minutes", "用户活跃后延迟", 5, 60)}{toggle("memory_link_enabled", "记忆联动")}{number("memory_evaluation_interval_minutes", "记忆评估间隔", 10, 1440)}</Card>
            <Card title="统一行为抽签" hint="只在已启用且满足条件的行为中选择；以下占比是全部配置权重的归一化展示，不代表实际触发保证。">{behaviors.map(([key, label]) => <label className="global-settings-field" key={key}><span>{label}</span><input type="number" aria-label={`${label}权重`} min={0} max={100} value={draft.settings.behavior_weights[key]} onChange={event => patch("behavior_weights", { ...draft.settings.behavior_weights, [key]: event.target.value === "" ? "" : Number(event.target.value) })} /><small>{weightsTotal > 0 ? (Number(draft.settings.behavior_weights[key]) / weightsTotal * 100).toFixed(1) : "0"}%</small></label>)}{weightsTotal === 0 && <p className="global-settings-error">四类权重不能同时为 0</p>}{toggle("avoid_behavior_repeat", "避免连续重复同一行为")}{toggle("fallback_on_failure", "失败时允许候补行为")}<p>最近成功行为：{saved?.recentBehaviors.map(item => behaviors.find(([key]) => key === item)?.[1] || item).join(" → ") || "暂无"}</p><p>最近成功时间：{saved?.lastSuccess || "暂无"}</p></Card>
            <Card title="24 小时时段权重" hint={`当前小时的草稿概率估算 ${probability.toFixed(2)}% / 每次调度检查；实际仍受冷却和行为可用性限制。0 为不主动，10 为最活跃。`}><div className="proactive-hours">{draft.settings.weights.map((weight, hour) => <label key={hour}><span>{String(hour).padStart(2, "0")}:00 <strong>{weight}</strong></span><input type="range" aria-label={`${hour}时权重`} min={0} max={10} step={1} value={weight} onChange={event => patch("weights", draft.settings.weights.map((value, index) => index === hour ? Number(event.target.value) : value))} /></label>)}</div><div className="proactive-actions"><button onClick={() => patch("weights", [...draft.defaultWeights])}>恢复默认时段（保存后生效）</button></div></Card>
            <Card title="整体调试" hint="立即触发沿用统一加权抽签，不保证选择普通聊天；请求入队不等于成功。"><div className="proactive-actions">{debug("normal", "立即触发（统一抽签）")}</div></Card>
          </>}
          {active === "normal" && <Card title="普通聊天" hint="轻量问候，不强行调用观察、B站和摸鱼工具。记忆联动沿用总体调度配置。">{toggle("normal_enabled", "启用普通聊天")}{cooldown("normal")}</Card>}
          {active === "observe" && <><Card title="调皮观察" hint="屏幕与摄像头的数值是来源相对权重，不会因同时有权重而一轮发送两次。">{toggle("observe_enabled", "启用调皮观察")}{cooldown("observe")}{number("screenshot_prob", "截图来源权重", 0, 100, "相对权重")}{number("camera_prob", "摄像头来源权重", 0, 100, "相对权重")}{number("camera_wait", "摄像头预告等待", 3, 30, "秒")}{toggle("observe_send_to_qq", "观察结果同时发送 QQ")}</Card><Card title="摄像头与调试" hint="打开面板不会扫描摄像头。扫描仅检查索引 0–3，会短暂点亮摄像头。"><label className="global-settings-field"><span>摄像头</span><select aria-label="观察摄像头" value={Number(draft.settings.camera_index)} onChange={event => patch("camera_index", Number(event.target.value))}>{[...new Set([Number(draft.settings.camera_index), ...cameraItems.map(item => item.index)])].map(index => <option key={index} value={index}>{cameraItems.find(item => item.index === index)?.label || `摄像头 ${index}（已保存 / 未扫描）`}</option>)}</select></label><div className="proactive-actions"><button onClick={async () => { if (await confirmAction("扫描会依次短暂启动摄像头 0–3，继续？")) void run(async () => { const result = await lianxinApi.proactiveCameras(); setCameraItems(result.items); setNotice(`扫描完成：找到 ${result.items.length} 个摄像头`); }); }}>扫描摄像头</button>{debug("screenshot", "截图观察（3 秒后）")}{debug("camera", "摄像头观察（3 秒后）")}</div></Card></>}
          {active === "bilibili" && <Card title="B站冲浪调度" hint="只管理冲浪开关与冷却；互动发言开关独立，保存不触发冲浪或公开评论。">{toggle("bilibili_enabled", "启用 B站冲浪")}{cooldown("bilibili")}<div className="proactive-actions">{debug("bilibili", "测试兴趣标签冲浪（实际执行）")}</div></Card>}
          {active === "slack" && <><Card title="闲时摸鱼">{toggle("slack_enabled", "启用莲心摸鱼")}{cooldown("slack")}{number("slack_idle_minutes", "空闲多久后开始摸鱼", 0, 240)}</Card>{[["内容与灵感", 0, 6], ["本机探索", 6, 10], ["关心与陪伴", 10, 13], ["音乐", 13, 14]].map(([label, start, end]) => <Card key={label} title={String(label)}>{slackActions.slice(Number(start), Number(end)).map(([key, name]) => <div key={key}>{toggle(`slack_${key}`, name)}</div>)}</Card>)}<Card title="摸鱼调试" hint="实际执行所选动作；依赖工具权限及当前运行时能力。"><label className="global-settings-field"><span>调试动作</span><select aria-label="摸鱼调试动作" value={debugAction} onChange={event => setDebugAction(event.target.value)}>{slackActions.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><div className="proactive-actions">{debug("slack", "执行所选摸鱼动作", debugAction)}</div></Card></>}
        </fieldset>}
        {draft && <div hidden={active !== "bilibili"}><ProactiveBilibiliPanel key={biliVersion} ref={bili} disabled={busy || confirming || countdown > 0} onDirtyChange={updateBiliDirty} onBusyChange={updateBiliBusy} /></div>}
      </main>
    </div>
    {countdown > 0 && <div className="proactive-countdown" role="alert">{countdown} 秒后开始观察 · <button onClick={cancelCountdown}>取消本次观察</button></div>}
    <footer className="global-settings-footer"><div className="proactive-feedback" role={error ? "alert" : "status"}><p>{busy ? "正在处理…" : notice}</p>{error && <p className="global-settings-error">{error}</p>}</div><button className="secondary" disabled={blocked} onClick={reload}><RefreshCw size={14} />重新加载</button><button className="secondary" onClick={close}>取消</button><button className="primary" disabled={!draft || blocked} onClick={save}><Save size={14} />保存并应用</button></footer>
  </div></div>;
}
