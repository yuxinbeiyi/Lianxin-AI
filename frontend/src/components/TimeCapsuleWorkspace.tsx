import { useEffect, useMemo, useState } from "react";
import { Archive, CalendarDays, Check, RefreshCw, Save } from "lucide-react";
import { lianxinApi } from "../services/lianxinApi";

type CapsuleDay = { date?: string; user_content?: string; lianxin_content?: string; sealed?: boolean; favorite?: boolean; traces?: Array<{ author?: string; content?: string; created_at?: string }> };

export function TimeCapsuleWorkspace() {
  const today = new Date().toISOString().slice(0, 10);
  const [day, setDay] = useState(today);
  const [content, setContent] = useState("");
  const [timeline, setTimeline] = useState<CapsuleDay[]>([]);
  const [sealed, setSealed] = useState(false);
  const [loading, setLoading] = useState(true);
  const load = (selected = day) => { setLoading(true); void lianxinApi.timeCapsule(selected).then((result) => { const item = result.day as CapsuleDay; setContent(item.user_content || ""); setSealed(Boolean(item.sealed)); setTimeline(result.timeline || []); }).finally(() => setLoading(false)); };
  useEffect(() => { load(today); }, []);
  const entries = useMemo(() => timeline.filter((item) => item.user_content || item.lianxin_content), [timeline]);
  const save = () => void lianxinApi.saveTimeCapsule(day, content).then(() => load(day));
  const seal = () => void lianxinApi.sealTimeCapsule(day, content).then(() => load(day));
  return <section className="workspace capsule-workspace"><div className="capsule-header"><div><span className="eyebrow">莲心空间</span><h1>时间胶囊</h1><p>把今天留给未来的自己和莲心。</p></div><div className="capsule-actions"><button className="secondary-button" onClick={() => load(day)}><RefreshCw size={15} />刷新</button><button className="secondary-button" onClick={save} disabled={sealed}><Save size={15} />保存</button><button className="primary-button" onClick={seal} disabled={sealed}><Archive size={15} />{sealed ? "已封存" : "封存今天"}</button></div></div><div className="capsule-grid"><article className="capsule-editor"><div className="capsule-date"><CalendarDays size={16} /><input type="date" value={day} onChange={(event) => { setDay(event.target.value); load(event.target.value); }} /></div><textarea value={content} onChange={(event) => setContent(event.target.value)} disabled={sealed} placeholder="写下今天想留下的话……" /><div className="capsule-status">{loading ? "正在读取时间胶囊…" : sealed ? <><Check size={14} />这一天已经封存</> : "内容会沿用旧版时间胶囊数据库保存"}</div></article><aside className="capsule-timeline"><h3>最近的胶囊</h3>{entries.length ? entries.slice(0, 12).map((item) => <button className="capsule-entry" key={item.date} onClick={() => { setDay(item.date || today); load(item.date || today); }}><strong>{item.date}</strong><span>{(item.user_content || item.lianxin_content || "").replace(/\s+/g, " ").slice(0, 90)}</span>{item.sealed && <Check size={14} />}</button>) : <p className="muted-copy">还没有留下时间胶囊。</p>}</aside></div></section>;
}
