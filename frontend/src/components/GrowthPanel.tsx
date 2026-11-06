import { useCallback, useEffect, useRef, useState } from "react";
import { Download, RefreshCw, X } from "lucide-react";
import { ApiRequestError, lianxinApi, type GrowthEvent, type GrowthState } from "../services/lianxinApi";
import "./growthPanel.css";

const STATUS: Record<string, string> = {
  pending: "待确认", applied: "已采纳", reverted: "已撤销", dismissed: "已忽略",
  expired: "已到期", superseded: "已被替代",
};
const SCENE: Record<string, string> = {
  all: "所有场景", main_chat: "主聊天", qq_chat: "QQ 聊天", proactive: "主动陪伴",
  tree_hole: "树洞", study_room: "自习室", time_capsule: "时间胶囊", bilibili_comment: "B站公开评论",
};

type Props = {
  personaId: string;
  profiles: Array<Record<string, any>>;
  onClose: () => void;
  onChange: (state: GrowthState) => void;
};

function errorMessage(error: unknown): string {
  if (error instanceof ApiRequestError && error.status === 404) return "成长接口尚未加载，请重启莲心后端并刷新页面；不会改动原有记录。";
  const message = error instanceof Error ? error.message : String(error);
  try {
    const payload = JSON.parse(message);
    return typeof payload.error === "string" ? payload.error : message;
  } catch {
    return message;
  }
}

export function GrowthPanel({ personaId, profiles, onClose, onChange }: Props) {
  const [selectedId, setSelectedId] = useState(personaId);
  const [state, setState] = useState<GrowthState | null>(null);
  const [filter, setFilter] = useState("all");
  const [range, setRange] = useState("all");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState("");
  const [confirmation, setConfirmation] = useState<{ event: GrowthEvent; action: "apply" | "revert" | "dismiss" } | null>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLElement>(null);
  const confirmationDialog = useRef<HTMLElement>(null);
  const sequence = useRef(0);

  const refresh = useCallback(async () => {
    const request = ++sequence.current;
    setLoading(true);
    setNotice("");
    try {
      const next = await lianxinApi.personaGrowth(selectedId);
      if (request === sequence.current) setState(next);
    } catch (error) {
      if (request === sequence.current) { setState(null); setNotice(errorMessage(error)); }
    } finally {
      if (request === sequence.current) setLoading(false);
    }
  }, [selectedId]);

  useEffect(() => { void refresh(); return () => { sequence.current += 1; }; }, [refresh]);
  useEffect(() => { closeButton.current?.focus(); }, []);
  useEffect(() => {
    if (!confirmation) return;
    const previous = document.activeElement as HTMLElement | null;
    confirmationDialog.current?.querySelector<HTMLButtonElement>("button")?.focus();
    return () => { previous?.focus(); };
  }, [confirmation]);
  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      if (event.key === "Tab") {
        const scope = confirmation ? confirmationDialog.current : dialog.current;
        const controls = Array.from(scope?.querySelectorAll<HTMLElement>('button:not(:disabled), select:not(:disabled), input:not(:disabled), summary, [tabindex="0"]') || []);
        const first = controls[0], last = controls[controls.length - 1];
        if (!first) { event.preventDefault(); return; }
        if (!scope?.contains(document.activeElement) || (event.shiftKey && document.activeElement === first)) {
          event.preventDefault(); (event.shiftKey ? last : first).focus();
        } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        return;
      }
      if (event.key !== "Escape" || busy) return;
      if (confirmation) setConfirmation(null); else onClose();
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [busy, confirmation, onClose]);

  const mutate = async (operation: () => Promise<GrowthState>, message: string) => {
    if (busy || loading) return;
    setBusy(true);
    setNotice("");
    try {
      const next = await operation();
      setState(next);
      onChange(next);
      setConfirmation(null);
      setNotice(message);
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const paused = Boolean(state?.settings.growth_paused_until && Date.parse(state.settings.growth_paused_until) > Date.now());
  const events = (state?.events || []).filter((event) =>
    (filter === "all" || event.status === filter)
    && (range === "all" || (range === "shared" ? event.scope === "shared" : event.scope === "persona" && event.persona_id === selectedId)),
  );
  const name = (id: string) => String(profiles.find((profile) => profile.id === id)?.profile_name || "已删除的表达模式");
  const exportEvents = () => {
    if (!state) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify({ exported_at: new Date().toISOString(), ...state }, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "莲心成长轨迹.json";
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return <div className="constellation-overlay persona-overlay growth-overlay">
    <section ref={dialog} className="persona-frame growth-frame" role="dialog" aria-modal="true" aria-labelledby="growth-title">
      <header className="growth-header">
        <div><span className="eyebrow">同一个莲心 · 经历与改变</span><h2 id="growth-title">成长轨迹</h2>
          <p>共同经历属于记忆；这里保存有证据、可撤销的相处习惯。切换表达模式不会重建一个莲心。</p></div>
        <button data-settings-close ref={closeButton} className="secondary-button" disabled={busy} onClick={onClose} aria-label="返回人格枢控"><X size={18} />返回</button>
      </header>
      <div className="growth-scroll">
        <section className="growth-settings" aria-label="成长学习设置">
          <label>学习方式<select aria-label="成长学习方式" value={state?.settings.mode || "confirm"} disabled={!state || busy || loading}
            onChange={(event) => void mutate(() => lianxinApi.growthSettings(selectedId, { mode: event.target.value }), "学习方式已保存。已有生效偏好不受此开关影响。") }>
            <option value="off">停止学习新偏好</option><option value="confirm">所有候选先确认</option><option value="low_risk_auto">明确低风险反馈可自动采纳</option>
          </select></label>
          <button className="secondary-button" disabled={!state || busy || loading}
            onClick={() => void mutate(() => lianxinApi.growthSettings(selectedId, { paused: !paused }), paused ? "已恢复学习。" : "已暂停学习新偏好 7 天；已有偏好继续生效。")}>{paused ? "恢复学习" : "暂停学习 7 天"}</button>
          <button className="secondary-button" disabled={busy || loading} onClick={() => void refresh()}><RefreshCw size={14} />刷新</button>
          <button className="secondary-button" disabled={!state || busy || loading} onClick={exportEvents}><Download size={14} />导出记录</button>
          <small>模型提取的候选始终等待确认，不因自动模式直接生效。不新增 d3 调用。导出文件包含原话证据，请妥善保管。</small>
          <label><input type="checkbox" checked={state?.settings.allow_proactive_requests || false} disabled={!state || busy || loading}
            onChange={(event) => void mutate(() => lianxinApi.growthSettings(selectedId, { allow_proactive_requests: event.target.checked }), "主动诉求设置已保存。") } />允许偶尔提出兴趣分享邀请</label>
          <label><input type="checkbox" checked={state?.settings.allow_photo_invites || false} disabled={!state || busy || loading}
            onChange={(event) => void mutate(() => lianxinApi.growthSettings(selectedId, { allow_photo_invites: event.target.checked }), "照片邀请设置已保存。") } />允许自愿照片邀请（独立授权）</label>
        </section>
        <section className="growth-effective" aria-label="实际生效偏好">
          <label>查看此表达模式的实际生效结果<select aria-label="生效表达模式" value={selectedId} disabled={busy || Boolean(confirmation)} onChange={(event) => setSelectedId(event.target.value)}>
            {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.profile_name}</option>)}
          </select></label>
          <p>主聊天场景：场景专属优先于通用，同场景下模式专属优先于共享。旧记录保留原模式范围，不自动合并。</p>
          <pre>{loading ? "正在读取…" : state?.context || "暂无已确认的生效偏好。"}</pre>
        </section>
        <div className="growth-filters">
          <label>状态<select aria-label="成长记录状态" value={filter} onChange={(event) => setFilter(event.target.value)}><option value="all">全部状态</option>{Object.entries(STATUS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
          <label>范围<select aria-label="成长记录范围" value={range} onChange={(event) => setRange(event.target.value)}><option value="all">全部记录</option><option value="shared">共同成长</option><option value="persona">所选模式专属</option></select></label>
          <span>展示最近 500 条，当前筛选 {events.length} 条</span>
        </div>
        {notice && <p className="growth-notice" role="status">{notice}</p>}
        {!loading && events.length === 0 && <p className="growth-empty">暂无符合条件的成长记录。没有候选并不表示没有保存共同记忆。</p>}
        <div className="growth-events">{!loading && events.map((event) => <article key={event.id} className="growth-event">
          <header><h3>{event.title}</h3><span>{STATUS[event.status] || event.status}{state?.effective_ids.includes(event.id) ? " · 主聊天实际生效" : ""}</span></header>
          <p>{event.detail}</p>
          <dl><dt>范围</dt><dd>{event.scope === "shared" ? "共同成长 · 所有表达模式" : `模式专属 · ${name(event.persona_id)}`} / {SCENE[event.scene] || event.scene}</dd>
            <dt>变化</dt><dd>{event.field || "旧版偏好"}：{event.old_value || "未指定"} → {event.proposed_value || event.detail}</dd>
            <dt>来源</dt><dd>{event.source_kind === "extracted_candidate" ? "模型提取建议（不是已确认事实）" : event.source_kind === "explicit_feedback" ? "用户明确反馈" : "旧版记录"} · {event.created_at}
              {event.source_message_ids.length > 0 && ` · 会话 ${event.source_session_id} / 消息 ${event.source_message_ids.join("、")}`}</dd>
            <dt>有效期</dt><dd>{event.expires_at || (event.source_kind === "legacy" ? "沿用旧版到期规则" : "长期，直到撤销或被替代")}</dd></dl>
          <details><summary>查看原话证据与冲突</summary><blockquote>{event.evidence || "旧记录未保留原话证据。"}</blockquote>
            {(event.evidence_sources || []).filter((source) => source.evidence_text !== event.evidence).map((source) => <blockquote key={source.source_ref || source.evidence_text}>{source.evidence_text}<br /><small>{source.source_ref}</small></blockquote>)}
            {event.risk === "confirmation_required" && <p>需要手动确认；采纳会替代相同范围、场景、字段的旧偏好。</p>}
            {event.supersedes_id > 0 && <p>关联旧记录 #{event.supersedes_id}；撤销本次采纳可恢复被它替代的偏好。</p>}</details>
          <div className="growth-actions">
            {(event.status === "pending" || event.status === "reverted") && <button className="persona-primary" disabled={busy} onClick={() => setConfirmation({ event, action: "apply" })}>{event.status === "reverted" ? "重新采纳" : "采纳"}</button>}
            {event.status === "pending" && <button className="secondary-button" disabled={busy} onClick={() => setConfirmation({ event, action: "dismiss" })}>忽略</button>}
            {event.status === "applied" && <button className="secondary-button" disabled={busy} onClick={() => setConfirmation({ event, action: "revert" })}>撤销生效</button>}
          </div>
        </article>)}</div>
      </div>
      {confirmation && <div className="persona-modal-backdrop"><section ref={confirmationDialog} className="persona-modal" role="alertdialog" aria-modal="true" aria-labelledby="growth-confirm-title">
        <h3 id="growth-confirm-title">{confirmation.action === "apply" ? "确认采纳这项改变？" : confirmation.action === "revert" ? "撤销这项偏好？" : "忽略这个候选？"}</h3>
        <p>{confirmation.event.detail}</p><p>{confirmation.event.scope === "shared" ? "适用于所有表达模式" : `只适用于「${name(confirmation.event.persona_id)}」`} · {SCENE[confirmation.event.scene]}</p>
        <p>{confirmation.action === "apply" ? "同范围、同场景、同字段的旧偏好会被替代；原始证据仍保留。" : "不会删除共同记忆或历史证据。"}</p>
        {notice && <p className="growth-notice" role="alert">{notice}</p>}
        <div className="persona-modal-actions"><button className="secondary-button" disabled={busy} onClick={() => setConfirmation(null)}>取消</button><button className="persona-primary" disabled={busy}
          onClick={() => void mutate(() => lianxinApi.reviewGrowth(selectedId, confirmation.event.id, confirmation.action), "成长记录已更新；下一条请求使用新偏好。")}>{busy ? "处理中…" : "确认"}</button></div>
      </section></div>}
    </section>
  </div>;
}
