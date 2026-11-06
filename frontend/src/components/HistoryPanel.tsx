import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, Clock3, Download, Loader2, Pin, RefreshCw, Search, Sparkles, Trash2, X } from "lucide-react";
import { lianxinApi, type HistoryDetail, type HistoryJob, type HistorySession } from "../services/lianxinApi";
import "../styles/history-panel.css";

function highlight(text: string, query: string) {
  const keyword = query.trim();
  if (!keyword) return text;
  const lower = text.toLocaleLowerCase();
  const needle = keyword.toLocaleLowerCase();
  const pieces = [];
  let offset = 0;
  let match = lower.indexOf(needle);
  while (match >= 0) {
    pieces.push(<Fragment key={match}>{text.slice(offset, match)}<mark>{text.slice(match, match + keyword.length)}</mark></Fragment>);
    offset = match + keyword.length;
    match = lower.indexOf(needle, offset);
  }
  pieces.push(text.slice(offset));
  return pieces;
}

type Confirmation = { title: string; message: string; danger?: boolean; action: (value: string) => Promise<void>; rename?: string };

export function HistoryPanel({ onClose, onOpen, onChanged, chatBusy }: {
  onClose: () => void;
  onOpen: (id: number) => Promise<void>;
  onChanged: () => Promise<void>;
  chatBusy: boolean;
}) {
  const [items, setItems] = useState<HistorySession[]>([]);
  const [query, setQuery] = useState("");
  const [date, setDate] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const [checked, setChecked] = useState<number[]>([]);
  const [detail, setDetail] = useState<HistoryDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false);
  const [job, setJob] = useState<HistoryJob | null>(null);
  const [jobError, setJobError] = useState("");
  const [jobKnown, setJobKnown] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [rename, setRename] = useState("");
  const [revision, setRevision] = useState(0);
  const listEpoch = useRef(0);
  const detailEpoch = useRef(0);
  const operation = useRef(false);
  const preview = useRef<HTMLDivElement>(null);
  const root = useRef<HTMLElement>(null);
  const confirmInput = useRef<HTMLInputElement>(null);
  const previousJob = useRef<HistoryJob | null>(null);
  const jobEpoch = useRef(0);
  const refresh = useCallback(() => setRevision(value => value + 1), []);
  const generating = job?.status === "running";
  const blocked = pending || loading || detailLoading || !jobKnown || Boolean(generating);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    root.current?.focus();
    return () => previous?.focus();
  }, []);

  useEffect(() => {
    if (confirmation) {
      setRename(confirmation.rename ?? "");
      const timer = window.setTimeout(() => {
        const control = confirmInput.current ?? root.current?.querySelector<HTMLButtonElement>(".history-confirm button");
        control?.focus();
      }, 0);
      return () => window.clearTimeout(timer);
    }
  }, [confirmation]);

  useEffect(() => {
    const epoch = ++listEpoch.current;
    setLoading(true);
    const timer = window.setTimeout(() => {
      void lianxinApi.historySessions(query, date).then(result => {
        if (epoch !== listEpoch.current) return;
        setItems(result.items);
        setSelected(current => result.items.some(item => item.id === current) ? current : result.items[0]?.id ?? null);
        setChecked(current => current.filter(id => result.items.some(item => item.id === id)));
        setError("");
      }).catch(reason => {
        if (epoch === listEpoch.current) { setItems([]); setSelected(null); setError(String(reason)); }
      }).finally(() => { if (epoch === listEpoch.current) setLoading(false); });
    }, 180);
    return () => { window.clearTimeout(timer); listEpoch.current += 1; };
  }, [query, date, revision]);

  useEffect(() => {
    const epoch = ++detailEpoch.current;
    setDetail(null);
    if (!selected) { setDetailLoading(false); return; }
    setDetailLoading(true);
    void lianxinApi.historyDetail(selected).then(result => {
      if (epoch === detailEpoch.current) {
        setDetail(result);
        if (preview.current) preview.current.scrollTop = 0;
      }
    }).catch(reason => { if (epoch === detailEpoch.current) setError(String(reason)); })
      .finally(() => { if (epoch === detailEpoch.current) setDetailLoading(false); });
    return () => { detailEpoch.current += 1; };
  }, [selected, revision]);

  useEffect(() => {
    let alive = true;
    let timer = 0;
    const poll = async () => {
      const epoch = jobEpoch.current;
      try {
        const result = await lianxinApi.historyJob();
        if (!alive || epoch !== jobEpoch.current) return;
        const previous = previousJob.current;
        if (previous?.status === "running" && result.job?.id === previous.id && result.job.status !== "running") refresh();
        if (previous && !result.job) setNotice("后端已重启，之前生成任务的结果未知，请刷新核对，勿直接重复生成。");
        previousJob.current = result.job;
        setJob(result.job);
        setJobError("");
        setJobKnown(true);
      } catch (reason) {
        if (alive && epoch === jobEpoch.current) { setJobError(`无法确认生成任务状态：${String(reason)}`); setJobKnown(false); }
      } finally {
        if (alive) timer = window.setTimeout(poll, 1200);
      }
    };
    void poll();
    return () => { alive = false; window.clearTimeout(timer); };
  }, [refresh]);

  const act = async (action: () => Promise<void>) => {
    if (operation.current) return;
    operation.current = true;
    setPending(true); setError(""); setNotice(""); setConfirmation(null);
    try { await action(); }
    catch (reason) { setError(String(reason)); }
    finally { operation.current = false; setPending(false); }
  };

  const startJob = async (payload: Parameters<typeof lianxinApi.historyStartJob>[0]) => {
    jobEpoch.current += 1;
    const result = await lianxinApi.historyStartJob(payload);
    previousJob.current = result.job;
    setJob(result.job);
  };

  const askRename = (item: HistorySession) => setConfirmation({
    title: "重命名会话", message: "修改历史会话标题，不改变会话内容。", rename: item.title,
    action: async title => { await lianxinApi.historyUpdate({ id: item.id, title }); refresh(); },
  });

  const askDelete = (ids: number[]) => {
    const count = items.filter(item => ids.includes(item.id)).reduce((sum, item) => sum + item.message_count, 0);
    setConfirmation({ title: ids.length > 1 ? "批量删除会话" : "删除会话", danger: true,
      message: `将永久删除 ${ids.length} 个会话、${count} 条消息，无法恢复。若删除当前聊天，将新建空白会话。`,
      action: async () => { await lianxinApi.historyDelete(ids); refresh(); await onChanged(); setNotice("已删除所选会话。"); },
    });
  };

  const askImport = async () => {
    if (!detail) return;
    const source = detail;
    const status = await lianxinApi.status();
    setConfirmation({ title: "导入记忆到当前对话",
      message: `将这 ${source.messages.length} 条消息（${source.character_count} 字符）逐条加上 [回顾]，追加到当前会话 #${status.sessionId}。这不是提取长期记忆；原会话不变，可重复导入，请避免误操作。`,
      action: async () => {
        await lianxinApi.historyImport(source.session.id, status.sessionId);
        refresh(); await onChanged(); setNotice("已追加回顾消息到当前聊天。");
      },
    });
  };

  const askDiary = async () => {
    const ids = [...checked];
    const targetDate = date;
    const state = await lianxinApi.historyDiary(targetDate);
    setConfirmation({ title: state.exists ? "覆盖已有书页" : "留下书页", danger: state.exists,
      message: `${targetDate}：将所选 ${ids.length} 个会话交给现有日记模型生成右侧书页，消耗模型额度。${state.exists ? "该日期已有书页，确认将覆盖原内容。" : "生成后可在时间胶囊查看。"}`,
      action: async () => startJob({ kind: "diary", ids, date: targetDate, overwrite: state.exists }),
    });
  };

  const exportLocal = async (format: "txt" | "json", all = false) => {
    setNotice("请在本地“另存为”窗口选择保存位置，或取消导出。");
    const result = await lianxinApi.historyExport({ id: selected ?? undefined, format, all });
    if (result.cancelled) setNotice("已取消导出，未写入文件。");
    else if (result.saved && result.path) setNotice(`已保存到本地：${result.path}`);
    else throw new Error("导出结果未确认，请检查本地目标文件。");
  };

  return <div className="history-overlay">
    <section className="history-panel" role="dialog" aria-modal="true" aria-labelledby="history-title" tabIndex={-1} ref={root}
      onKeyDown={event => {
        if (event.key === "Escape" && !pending) { event.stopPropagation(); confirmation ? setConfirmation(null) : onClose(); }
        if (event.key === "Tab") {
          const scope = confirmation ? root.current?.querySelector(".history-confirm") : root.current;
          const controls = scope?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex="0"]');
          const first = controls?.[0]; const last = controls?.[controls.length - 1];
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        }
      }}>
      <header className="history-heading"><div><span className="history-eyebrow">LIANXIN · ARCHIVE</span><h2 id="history-title"><Clock3 size={21} />历史记录 <small>新版试用</small></h2><p>{jobKnown ? "后端已连接" : "连接状态待确认"} · 浏览、整理和回顾，不改变正在进行的对话</p></div>
        <div className="history-heading-actions"><button onClick={refresh} disabled={pending}><RefreshCw size={15} />刷新</button><button onClick={() => onClose()} disabled={pending} aria-label="关闭历史记录"><X size={19} /></button></div></header>
      <div className="history-toolbar"><label className="history-search"><Search size={16} /><input aria-label="搜索历史记录" placeholder="搜索标题、摘要或消息内容" value={query} onChange={event => setQuery(event.target.value)} /></label>
        <label>创建日期 <input aria-label="筛选创建日期" type="date" value={date} onChange={event => { setDate(event.target.value); setChecked([]); }} /></label>
        <button onClick={() => { setDate(""); setChecked([]); }} disabled={!date}>清除日期</button>
      </div>
      {(error || notice || pending) && <div className={`history-banner ${error ? "is-error" : ""}`} role={error ? "alert" : "status"}>{error || notice || "正在处理，请稍候…"}</div>}
      {jobError && <div className="history-banner is-error" role="alert">{jobError}。恢复连接前暂不允许启动新生成任务。</div>}
      {job && <div className="history-job" role="status">{generating && <Loader2 className="history-spin" size={15} />}<strong>{job.kind === "diary" ? "书页生成" : job.kind === "batch-summary" ? "批量摘要" : "会话摘要"}</strong>
        <span>{job.processed}/{job.total} · 成功 {job.succeeded} · {({ running: "进行中，可关闭面板稍后查看", completed: "已完成", partial: "部分失败", failed: "失败" })[job.status]}</span>
        <progress max={job.total} value={job.processed} aria-label="生成进度" />
        {job.errors.length > 0 && <details><summary>查看 {job.errors.length} 项失败</summary>{job.errors.map((item, index) => <p key={index}>{item.id ? `会话 #${item.id}：` : ""}{item.message}</p>)}</details>}
      </div>}
      <div className="history-body">
        <aside className="history-sidebar"><div className="history-list-meta"><span>{loading ? "正在读取…" : `${items.length} 个会话`}</span>{date && <label><input type="checkbox" aria-label="全选当前日期会话" disabled={loading || !items.length} checked={!!items.length && checked.length === items.length} onChange={event => setChecked(event.target.checked ? items.map(item => item.id) : [])} />全选</label>}</div>
          <div className="history-session-list">{loading ? <div className="history-empty"><Loader2 className="history-spin" />读取历史记录…</div> : items.length ? items.map(item => <div className={`history-session ${selected === item.id ? "is-selected" : ""}`} key={item.id}>
            {date && <input aria-label={`选择会话 ${item.title}`} type="checkbox" disabled={loading} checked={checked.includes(item.id)} onChange={event => setChecked(current => event.target.checked ? [...current, item.id] : current.filter(id => id !== item.id))} />}
            <button className="history-session-select" onClick={() => setSelected(item.id)} onDoubleClick={() => !blocked && askRename(item)} aria-pressed={selected === item.id}>
              <strong>{Boolean(item.is_pinned) && <Pin size={12} />}{highlight(item.title || "未命名会话", query)}</strong><small>{item.created_at} · {item.message_count} 条</small><span>{highlight(item.summary || "暂无摘要", query)}</span>
            </button></div>) : <div className="history-empty">{error ? "历史记录读取失败，请刷新重试" : "暂无符合条件的会话"}</div>}</div>
        </aside>
        <main className="history-preview" ref={preview} aria-label="历史消息预览">{detailLoading ? <div className="history-empty"><Loader2 className="history-spin" />加载消息…</div> : detail ? <>
          <div className="history-preview-heading"><h3>{highlight(detail.session.title, query)}</h3><span>{detail.session.created_at} · {detail.messages.length} 条消息 · {detail.character_count} 字符</span>{detail.session.summary && <p>{highlight(detail.session.summary, query)}</p>}</div>
          {detail.messages.length ? detail.messages.map((message, index) => <article className={`history-message ${message.role === "user" ? "is-user" : ""}`} key={index}><header><strong>{message.role === "user" ? "主人" : message.role === "assistant" ? "莲心" : message.role}</strong><time>{message.timestamp}</time></header><div>{highlight(message.content, query)}</div></article>) : <div className="history-empty">这个会话还没有消息</div>}
        </> : <div className="history-empty"><BookOpen size={30} />从左侧选择会话查看完整内容</div>}</main>
      </div>
      <footer className="history-footer"><div className="history-actions">
        <button disabled={!detail || blocked} onClick={() => detail && askRename(detail.session)}>重命名</button>
        <button disabled={!detail || blocked} onClick={() => void act(async () => { await lianxinApi.historyUpdate({ id: selected!, togglePin: true }); refresh(); })}><Pin size={14} />{detail?.session.is_pinned ? "取消置顶" : "置顶"}</button>
        <button disabled={!detail?.messages.length || blocked} onClick={() => setConfirmation({ title: "生成会话摘要", message: "使用当前配置的对话模型生成摘要，将覆盖此会话原摘要并消耗模型额度。", action: async () => startJob({ kind: "summary", id: selected! }) })}><Sparkles size={14} />摘要</button>
        <button disabled={!detail || pending} title="选择本地文件位置并保存 TXT" onClick={() => void act(() => exportLocal("txt"))}><Download size={14} />TXT</button>
        <button disabled={!detail || pending} title="选择本地文件位置并保存 JSON" onClick={() => void act(() => exportLocal("json"))}>JSON</button>
        <button disabled={!detail?.messages.length || blocked || chatBusy} onClick={() => void act(askImport)}>导入记忆</button>
        <button className="is-danger" disabled={!detail || blocked || chatBusy || (!!date && !checked.length) || loading} onClick={() => askDelete(date ? checked : [selected!])}><Trash2 size={14} />{date ? `删除勾选 (${checked.length})` : "删除"}</button>
        <button disabled={!detail || blocked || chatBusy} onClick={() => void act(async () => { await onOpen(selected!); onClose(); })}>继续此对话</button>
      </div><div className="history-actions history-global-actions">
        {date && <button disabled={!checked.length || blocked || loading} onClick={() => void act(askDiary)}><BookOpen size={14} />留下书页 ({checked.length})</button>}
        <button disabled={blocked} onClick={() => setConfirmation({ title: "批量生成摘要", message: "处理全部历史中消息数 ≥10 且没有摘要的会话（不受当前搜索或日期筛选限制）。逐个调用对话模型、消耗额度，单项失败后继续下一项。", action: async () => startJob({ kind: "batch-summary" }) })}>批量摘要</button>
        <button disabled={pending} title="选择本地文件位置并保存全部会话" onClick={() => void act(() => exportLocal("json", true))}>导出全部 JSON</button>
      </div></footer>
      {confirmation && <div className="history-confirm-shade"><form className="history-confirm" role="alertdialog" aria-modal="true" aria-labelledby="history-confirm-title" aria-describedby="history-confirm-description" onSubmit={event => { event.preventDefault(); void act(() => confirmation.action(rename)); }}>
        <h3 id="history-confirm-title">{confirmation.title}</h3><p id="history-confirm-description">{confirmation.message}</p>
        {confirmation.rename !== undefined && <input ref={confirmInput} aria-label="新的会话名称" value={rename} onChange={event => setRename(event.target.value)} />}
        <div><button type="button" disabled={pending} onClick={() => setConfirmation(null)}>取消</button><button className={confirmation.danger ? "is-danger" : "is-primary"} type="submit" disabled={pending || (confirmation.rename !== undefined && !rename.trim())}>{pending ? "处理中…" : "确认"}</button></div>
      </form></div>}
    </section>
  </div>;
}
