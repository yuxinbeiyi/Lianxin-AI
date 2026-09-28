import { useEffect, useState } from "react";
import { lianxinApi } from "../services/lianxinApi";

type Selection = { forcedTool?: string; preferredTool?: string };

export function ToolPanel({ selected, onSelect, onClose }: { selected: Selection; onSelect: (selection: Selection) => void; onClose: () => void }) {
  const [items, setItems] = useState<Array<{ name: string; display_name: string; category: string; enabled: boolean; available: boolean }>>([]);
  const [mode, setMode] = useState<"preferred" | "forced">(selected.forcedTool ? "forced" : "preferred");
  const [query, setQuery] = useState("");
  useEffect(() => { void lianxinApi.capabilities().then((result) => setItems(result.items)).catch(() => undefined); }, []);
  const visible = items.filter((item) => item.enabled && item.available && `${item.display_name} ${item.name} ${item.category}`.toLowerCase().includes(query.toLowerCase()));
  const current = selected.forcedTool || selected.preferredTool;
  return <div className="overlay"><section className="overlay-panel"><button className="overlay-close" onClick={onClose}>×</button><div className="panel-heading"><div><span className="eyebrow">工具调用</span><h2>工具选择器</h2></div><button className="secondary-button" onClick={() => { onSelect({}); onClose(); }}>自动选择</button></div><input className="panel-search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索工具" /><div className="tool-mode-tabs"><button className={mode === "preferred" ? "is-selected" : ""} onClick={() => setMode("preferred")}>建议使用</button><button className={mode === "forced" ? "is-selected" : ""} onClick={() => setMode("forced")}>强制使用</button></div><div className="module-list">{visible.slice(0, 80).map((item) => <button className="module-entry" key={item.name} onClick={() => { onSelect(mode === "forced" ? { forcedTool: item.name } : { preferredTool: item.name }); onClose(); }}><span><span className={`module-dot ${current === item.name ? "is-ready" : ""}`} />{item.display_name || item.name}</span><small>{item.category}</small></button>)}</div></section></div>;
}
