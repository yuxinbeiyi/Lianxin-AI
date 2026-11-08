import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, RefreshCw, X } from "lucide-react";
import { lianxinApi, type SocialChatChannel, type SocialChatSettings, type SocialChatState, type SocialChatStatus } from "../services/lianxinApi";
import { confirmConfigAction } from "../utils/nativeConfig";
import "../styles/global-settings.css";
import '../styles/social-chat.css';

const labels: Record<string, string> = {
  auto_start: "莲心启动时自动连接", fast_reply: "极速回复（主人私聊）", listen_port: "Webhook 监听端口", owner_id: "主人微信 ID",
  think_delay_min: "思考延迟 · 最短", think_delay_max: "思考延迟 · 最长", type_speed_min: "模拟打字速度 · 最慢", type_speed_max: "模拟打字速度 · 最快",
  min_reply_interval: "最短回复间隔", segment_threshold_min: "分段字数阈值 · 最少", segment_threshold_max: "分段字数阈值 · 最多",
  segment_interval_min: "段间间隔 · 最短", segment_interval_max: "段间间隔 · 最长", global_send_interval_min: "全局发送间隔 · 最短", global_send_interval_max: "全局发送间隔 · 最长",
  limit_enabled: "启用聊天次数限制", daily_limit_owner: "主人每日上限（当前机制不限制主人）", daily_limit_other: "其它用户每日上限", per_group_daily_limit: "单群每日上限",
  block_links: "过滤所有链接", cross_session_context_limit: "跨端参考聊天条数", voice_reply_enabled: "语音回复", segmented_reply_enabled: "启用分段发送",
  poke_enabled: "开启拍一拍回应", poke_llm: "使用 LLM 生成回应（失败时用萌语兜底）", poke_cooldown_seconds: "拍一拍冷却时间",
};
const units: Record<string, string> = {
  think_delay_min: "秒", think_delay_max: "秒", type_speed_min: "字/分钟", type_speed_max: "字/分钟", min_reply_interval: "秒",
  segment_threshold_min: "字", segment_threshold_max: "字", segment_interval_min: "秒", segment_interval_max: "秒",
  global_send_interval_min: "秒", global_send_interval_max: "秒", daily_limit_owner: "条/日", daily_limit_other: "条/日", per_group_daily_limit: "条/日",
  cross_session_context_limit: "条（0=关闭）", poke_cooldown_seconds: "秒",
};
const ranges: Record<string, [number, number, number]> = {
  think_delay_min: [.5, 30, .5], think_delay_max: [.5, 30, .5], type_speed_min: [10, 2000, 10], type_speed_max: [10, 2000, 10],
  min_reply_interval: [0, 60, .5], segment_threshold_min: [20, 500, 10], segment_threshold_max: [20, 500, 10],
  segment_interval_min: [1, 60, .5], segment_interval_max: [1, 60, .5], global_send_interval_min: [0, 120, .5], global_send_interval_max: [0, 120, .5],
  daily_limit_owner: [5, 500, 1], daily_limit_other: [1, 999, 1], per_group_daily_limit: [5, 200, 1], cross_session_context_limit: [0, 50, 1],
  poke_cooldown_seconds: [0, 600, 1], listen_port: [1024, 65535, 1],
};
const groups: Record<SocialChatChannel, Array<[string, string]>> = {
  qq: [["bridge", "桥接与即时控制"], ["timing", "回复节奏"], ["limits", "发送限制"], ["interaction", "互动与语音"]],
  wechat: [["bridge", "桥接设置"], ["timing", "回复节奏"], ["segments", "分段发送"], ["limits", "风控与跨端记忆"], ["voice", "语音回复"]],
};
const fields: Record<SocialChatChannel, Record<string, Array<["timing" | "bridge", string]>>> = {
  qq: {
    bridge: [["bridge", "auto_start"]],
    timing: ["think_delay_min", "think_delay_max", "type_speed_min", "type_speed_max", "min_reply_interval"].map(key => ["timing", key]),
    limits: ["global_send_interval_min", "global_send_interval_max", "limit_enabled", "daily_limit_other", "cross_session_context_limit"].map(key => ["timing", key]),
    interaction: ["voice_reply_enabled", "segmented_reply_enabled", "poke_enabled", "poke_llm", "poke_cooldown_seconds"].map(key => ["bridge", key]),
  },
  wechat: {
    bridge: [["bridge", "auto_start"], ["bridge", "listen_port"], ["bridge", "owner_id"]],
    timing: ["think_delay_min", "think_delay_max", "type_speed_min", "type_speed_max", "min_reply_interval"].map(key => ["timing", key]),
    segments: ["segment_threshold_min", "segment_threshold_max", "segment_interval_min", "segment_interval_max"].map(key => ["timing", key]),
    limits: ["global_send_interval_min", "global_send_interval_max", "daily_limit_owner", "limit_enabled", "daily_limit_other", "per_group_daily_limit", "block_links", "cross_session_context_limit"].map(key => ["timing", key]),
    voice: [["bridge", "voice_reply_enabled"]],
  },
};

function message(error: unknown) {
  if (!(error instanceof Error)) return "设置操作未完成";
  try { return JSON.parse(error.message).error || error.message; } catch { return error.message; }
}
function clone(settings: SocialChatSettings): SocialChatSettings {
  return { timing: { ...settings.timing }, bridge: { ...settings.bridge } };
}
function Toggle({ label, checked, disabled, onChange }: { label: string; checked: boolean; disabled: boolean; onChange: (value: boolean) => void }) {
  return <label className="global-settings-toggle"><span><strong>{label}</strong></span><input type="checkbox" checked={checked} disabled={disabled} onChange={event => onChange(event.target.checked)} /><i /></label>;
}

export function SocialChatPanel({ channel, onClose }: { channel: SocialChatChannel; onClose: () => void }) {
  const title = channel === "qq" ? "QQ 聊天" : "微信聊天";
  const [remote, setRemote] = useState<SocialChatState | null>(null);
  const [draft, setDraft] = useState<SocialChatSettings | null>(null);
  const [saved, setSaved] = useState<SocialChatSettings | null>(null);
  const [active, setActive] = useState("bridge");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("正在读取桥接与聊天设置…");
  const busyRef = useRef(false);
  const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(saved), [draft, saved]);
  const accept = useCallback((state: SocialChatState) => {
    setRemote(state);
    setDraft(clone(state.settings));
    setSaved(clone(state.settings));
  }, []);
  const reload = useCallback(async () => {
    if (dirty && !await confirmConfigAction("重新加载会放弃未保存的草稿，是否继续？")) return;
    accept(await lianxinApi.socialChatState(channel));
    setError("");
    setNotice("已重新读取当前配置与运行状态");
  }, [accept, channel, dirty]);
  const refreshStatus = async () => {
    const state = await lianxinApi.socialChatState(channel);
    setRemote(current => current ? { ...current, status: state.status } : state);
    setError("");
    setNotice("运行状态已刷新，未保存草稿保持不变");
  };
  useEffect(() => {
    let live = true;
    void lianxinApi.socialChatState(channel).then(state => {
      if (live) { accept(state); setNotice("自动连接与 QQ 极速回复即时生效；其它设置点击立即生效后保存"); }
    }).catch(cause => { if (live) { setError(message(cause)); setNotice("无法读取配置，请确认 Python 核心已启动"); } });
    const timer = window.setInterval(() => {
      if (busyRef.current) return;
      void lianxinApi.socialChatState(channel).then(state => { if (live) setRemote(current => current ? { ...current, status: state.status } : state); }).catch(() => undefined);
    }, 3000);
    return () => { live = false; window.clearInterval(timer); };
  }, [accept, channel]);

  const run = async (action: () => Promise<void>) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    try { await action(); }
    catch (cause) { setError(message(cause)); setNotice("操作未完成；请核对状态后重试"); }
    finally { busyRef.current = false; setBusy(false); }
  };
  const patch = (section: "timing" | "bridge", key: string, value: string | number | boolean) => {
    setDraft(current => current ? { ...current, [section]: { ...current[section], [key]: value } } : current);
  };
  const immediate = (key: string, value: boolean) => void run(async () => {
    if (channel === "qq" && key === "auto_start") {
      const result = await lianxinApi.socialChatAction(channel, "auto-start", { enabled: value }) as { enabled: boolean };
      setDraft(current => current ? { ...current, bridge: { ...current.bridge, auto_start: result.enabled } } : current);
      setSaved(current => current ? { ...current, bridge: { ...current.bridge, auto_start: result.enabled } } : current);
    } else {
      const status = await lianxinApi.socialChatAction(channel, "fast-reply", { enabled: value }) as unknown as SocialChatStatus;
      setRemote(current => current ? { ...current, status } : current);
    }
    setNotice(`${labels[key]}已即时${value ? "开启" : "关闭"}`);
  });
  const bridgeAction = (action: "connect" | "disconnect" | "retry") => void run(async () => {
    const status = await lianxinApi.socialChatAction(channel, action) as unknown as SocialChatStatus;
    setRemote(current => current ? { ...current, status } : current);
    setNotice(action === "disconnect" ? "已提交断开请求" : action === "retry" ? "已提交重连请求" : "已提交连接请求");
  });
  const restoreDefaults = () => void run(async () => {
    if (!remote || !draft) return;
    const next = clone(remote.defaults);
    if (channel === "qq") next.bridge.auto_start = draft.bridge.auto_start;
    setDraft(next);
    setNotice("已恢复默认草稿；点击立即生效后保存");
  });
  const save = () => void run(async () => {
    if (!remote || !draft) return;
    const settings = clone(draft);
    if (channel === "qq") {
      delete settings.timing.profile_version;
      delete settings.timing.segment_interval_min;
      delete settings.timing.segment_interval_max;
      delete settings.bridge.auto_start;
      delete settings.bridge.fast_reply;
    }
    const state = await lianxinApi.saveSocialChat(channel, { revision: remote.revision, settings });
    accept(state);
    setNotice(channel === "wechat" && state.status.running ? "配置已保存；桥接已按新端口及参数重载" : "配置已立即生效");
  });
  const close = () => void run(async () => {
    if (dirty && !await confirmConfigAction("有未保存的设置，确定放弃并关闭？自动连接和 QQ 极速回复等即时动作不会撤销。")) return;
    onClose();
  });

  const status = remote?.status;
  const statusLabel = channel === "qq"
    ? status?.connected ? "QQ 已连接" : status?.state === "connecting" ? "正在连接 QQ" : status?.state === "error" ? "连接失败" : status?.running ? "桥接运行中，等待连接" : "未连接"
    : status?.state === "listening" ? `Webhook 监听中 · ${status.listenPort ?? draft?.bridge.listen_port ?? "—"}` : status?.state === "starting" ? "Webhook 正在启动" : status?.state === "error" ? "监听启动失败" : "未连接";
  const statusTone = status?.connected ? "is-online" : status?.state === "error" ? "is-error" : status?.running ? "is-pending" : "is-offline";
  const sectionList = groups[channel];
  const activeLabel = sectionList.find(([id]) => id === active)?.[1] ?? sectionList[0][1];

  const renderField = (section: "timing" | "bridge", key: string) => {
    const value = draft?.[section][key];
    if (typeof value === "boolean") {
      const onChange = channel === "qq" && (key === "auto_start" || key === "fast_reply")
        ? (next: boolean) => immediate(key, next)
        : (next: boolean) => patch(section, key, next);
      return <Toggle key={key} label={labels[key] || key} checked={key === "fast_reply" ? Boolean(status?.fastReplyEnabled) : value} disabled={busy} onChange={onChange} />;
    }
    const range = key === "min_reply_interval"
      ? channel === "qq" ? [0, 30, .5] : [.5, 60, .5]
      : key.startsWith("global_send_interval_") && channel === "wechat"
        ? [1, 120, .5]
        : key.startsWith("type_speed_") && channel === "wechat"
          ? [10, 999, 10]
          : ranges[key];
    return <label key={key} className="global-settings-field"><span>{labels[key] || key}</span><span className="social-chat-input-wrap"><input aria-label={labels[key] || key} type={typeof value === "number" ? "number" : "text"} min={range?.[0]} max={range?.[1]} step={range?.[2]} maxLength={256} value={String(value ?? "")} disabled={busy || key === "daily_limit_owner"} onChange={event => patch(section, key, typeof value === "number" ? Number(event.target.value) : event.target.value)} />{units[key] && <small>{units[key]}</small>}</span></label>;
  };

  return <div className="global-settings-overlay social-chat-overlay"><div className="global-settings-shell social-chat-shell">
    <header className="global-settings-header"><div><div className="eyebrow">SOCIAL CHAT / BRIDGE</div><h2>{title}</h2><p>保留原有桥接控制、回复节奏和风控配置；即时动作与草稿保存分开处理。</p></div><button data-settings-close className="global-settings-close" aria-label={`关闭${title}`} onClick={close}><X size={18} /></button></header>
    <div className="global-settings-body"><nav className="global-settings-nav" aria-label={`${title}分组`}>{sectionList.map(([id, label]) => <button key={id} className={active === id ? "is-active" : ""} aria-current={active === id ? "page" : undefined} onClick={() => { setActive(id); const content = document.querySelector<HTMLElement>(".social-chat-content"); if (content) content.scrollTop = 0; }}><strong>{label}</strong><ChevronRight size={14} /></button>)}</nav>
      <main className="global-settings-content social-chat-content">
        {error && <p role="alert" className="global-settings-error">{error}</p>}
        {!draft || !remote ? <p role="status">{notice}</p> : active === "bridge" ? <section className="global-settings-card social-chat-status-card"><header><h3>桥接运行状态</h3><p>{channel === "wechat" ? "本地 Webhook 监听不代表微信客户端或上游机器人已完成登录。" : "连接状态来自现有 QQ 桥接运行时。"}</p></header><div className={`social-chat-status ${statusTone}`} role="status"><span />{statusLabel}</div>{status?.error && <p role="alert" className="global-settings-error">{status.error}</p>}{status?.disconnectReason && <small className="social-chat-muted">断开原因：{status.disconnectReason}</small>}<div className="social-chat-actions"><button type="button" disabled={busy || Boolean(status?.running && status?.state !== "error")} onClick={() => bridgeAction(channel === "qq" && status?.state === "error" ? "retry" : "connect")}>{status?.state === "error" ? <><RefreshCw size={14} />重试</> : "连接"}</button><button type="button" className="secondary" disabled={busy || !status?.running} onClick={() => bridgeAction("disconnect")}>断开</button><button type="button" className="secondary" disabled={busy} onClick={() => void run(refreshStatus)}>刷新状态</button></div>{fields[channel].bridge.map(([section, key]) => renderField(section, key))}{channel === "qq" && <Toggle label={labels.fast_reply} checked={Boolean(status?.fastReplyEnabled)} disabled={busy} onChange={value => immediate("fast_reply", value)} />}</section> : <section className="global-settings-card"><header><h3>{activeLabel}</h3><p>{channel === "qq" && active === "interaction" ? "QQ 分段间隔沿用旧版固定的 0.1–0.4 秒；其余选项保留草稿至保存。" : "数值边界与原版控件一致，范围上下限在保存时自动排序。"}</p></header>{fields[channel][active].map(([section, key]) => renderField(section, key))}</section>}
      </main>
    </div>
    <footer className="global-settings-footer"><div className="social-chat-feedback" role="status"><p>{busy ? "正在处理，请稍候…" : notice || (dirty ? "有未保存的修改" : "配置与当前后端一致")}</p><small>{dirty ? "仍有未保存草稿" : "当前草稿已与保存配置同步"}</small></div><button type="button" className="secondary" disabled={busy || !remote} onClick={restoreDefaults}>恢复默认</button><button type="button" className="secondary" disabled={busy || !remote} onClick={() => void run(reload)}>重新加载</button><button type="button" className="secondary" disabled={busy} onClick={close}>取消</button><button type="button" className="primary" disabled={busy || !draft || !remote || !dirty} onClick={save}>立即生效</button></footer>
  </div></div>;
}
