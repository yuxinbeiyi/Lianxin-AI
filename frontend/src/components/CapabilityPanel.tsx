import { useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronRight, FolderOpen, RefreshCw, Star, X } from "lucide-react";
import { open } from "@tauri-apps/plugin-dialog";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { lianxinApi, type CapabilityExtension, type CapabilityLog, type CapabilityPanelState, type CapabilityPluginInfo, type CapabilityTool, type CapabilityUsageState } from "../services/lianxinApi";
import { confirmCapabilityAction, useCapabilityInMain } from "../utils/capabilityWindow";
import "../styles/global-settings.css";
import "../styles/capability.css";

const groups = [
  { id: "library", label: "能力库", hint: "工具、收藏与对话建议" },
  { id: "usage", label: "使用情况", hint: "调用、成功率与耗时" },
  { id: "extensions", label: "扩展管理", hint: "Skills、MCP、安装与日志" },
] as const;
const extensionTabs = [["skills", "Skills"], ["mcp", "MCP 服务"], ["install", "安装插件"], ["logs", "日志"]] as const;
type Group = typeof groups[number]["id"];
type ExtensionTab = typeof extensionTabs[number][0];
type Action = Parameters<typeof lianxinApi.capabilityAction>[0];

function errorText(error: unknown) {
  if (!(error instanceof Error)) return "操作失败，请稍后重试";
  try { return JSON.parse(error.message).error || error.message; } catch { return error.message; }
}
function Card({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return <section className="global-settings-card"><header><h3>{title}</h3>{hint && <p>{hint}</p>}</header>{children}</section>;
}
function ToolDetails({ tools }: { tools: CapabilityTool[] }) {
  return <details className="capability-tool-details"><summary>工具与参数 · {tools.length} 项</summary>{tools.length ? tools.map(tool => <details key={tool.name}><summary>{tool.name}</summary><p>{tool.description || "暂无说明"}</p><pre>{JSON.stringify(tool.parameters || {}, null, 2)}</pre></details>) : <p>没有可展示的工具定义；纯知识 Skill 仍可参与上下文。</p>}</details>;
}
function Logs({ items }: { items: CapabilityLog[] }) {
  return <div className="capability-log" role="log">{items.length ? items.map((item, index) => <div key={`${index}-${item.timestamp}`} data-level={item.level}><time>{item.timestamp.replace("T", " ")}</time><span>{item.message}</span></div>) : <p>暂无日志。</p>}</div>;
}

export function CapabilityPanel({ onClose, onUse }: { onClose: () => void; onUse?: (name: string) => void }) {
  const [data, setData] = useState<CapabilityPanelState | null>(null);
  const [active, setActive] = useState<Group>("library");
  const [extension, setExtension] = useState<ExtensionTab>("skills");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [days, setDays] = useState("30");
  const [usage, setUsage] = useState<CapabilityUsageState | null>(null);
  const [usageVersion, setUsageVersion] = useState(0);
  const [source, setSource] = useState("");
  const [detected, setDetected] = useState<CapabilityPluginInfo | null>(null);
  const [detectError, setDetectError] = useState("");
  const [browserOpen, setBrowserOpen] = useState(false);
  const [browserRows, setBrowserRows] = useState<Array<Record<string, string>>>([]);
  const [browserError, setBrowserError] = useState("");
  const [browserVersion, setBrowserVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("正在读取共享能力注册表…");
  const busyRef = useRef(false);
  const content = useRef<HTMLElement>(null);

  useEffect(() => {
    let live = true;
    lianxinApi.capabilityPanel().then(result => { if (live) { setData(result); setNotice("启停、收藏等操作即时保存；打开面板不会执行工具或安装插件"); } }).catch(cause => { if (live) setError(errorText(cause)); });
    return () => { live = false; };
  }, []);
  useEffect(() => {
    if (!data?.mcpScanning) return;
    let live = true;
    const timer = window.setInterval(() => { if (!busyRef.current) void lianxinApi.capabilityPanel().then(result => { if (live) setData(result); }).catch(cause => { if (live) setError(errorText(cause)); }); }, 3000);
    return () => { live = false; window.clearInterval(timer); };
  }, [data?.mcpScanning]);
  useEffect(() => {
    if (active !== "usage" || browserOpen) return;
    let live = true;
    setUsage(null);
    lianxinApi.capabilityUsage(days === "all" ? null : Number(days)).then(result => { if (live) setUsage(result); }).catch(cause => { if (live) setError(errorText(cause)); });
    return () => { live = false; };
  }, [active, days, usageVersion, browserOpen]);
  useEffect(() => {
    let live = true;
    setDetected(null); setDetectError("");
    if (!source.trim()) return;
    const timer = window.setTimeout(() => { void lianxinApi.inspectCapabilityPlugin(source).then(result => { if (live) setDetected(result); }).catch(cause => { if (live) setDetectError(errorText(cause)); }); }, 350);
    return () => { live = false; window.clearTimeout(timer); };
  }, [source]);
  useEffect(() => {
    if (!browserOpen) return;
    let live = true;
    const refresh = () => void lianxinApi.capabilityBrowserLog().then(result => { if (live) { setBrowserRows(result.items); setBrowserError(""); } }).catch(cause => { if (live) setBrowserError(errorText(cause)); });
    refresh();
    const timer = window.setInterval(refresh, 1500);
    return () => { live = false; window.clearInterval(timer); };
  }, [browserOpen, browserVersion]);
  useEffect(() => {
    if (!("__TAURI_INTERNALS__" in window) || active !== "extensions" || extension !== "install") return;
    let live = true;
    let unlisten: (() => void) | undefined;
    void getCurrentWindow().onDragDropEvent(event => {
      if (live && !busyRef.current && event.payload.type === "drop" && event.payload.paths.length === 1) setSource(event.payload.paths[0]);
    }).then(stop => { if (live) unlisten = stop; else stop(); }).catch(cause => { if (live) setError(errorText(cause)); });
    return () => { live = false; unlisten?.(); };
  }, [active, extension]);
  useEffect(() => {
    const prevent = (event: BeforeUnloadEvent) => { if (source.trim() || busyRef.current) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [source]);

  const run = async (operation: () => Promise<void>) => {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setError("");
    try { await operation(); } catch (cause) {
      setError(errorText(cause)); setNotice("操作未完成，请查看错误提示及扩展日志");
      try { setData(await lianxinApi.capabilityPanel()); } catch { setNotice("操作未完成，最新状态暂不可读；请勿假定修改已回滚或重复安装"); }
    }
    finally { busyRef.current = false; setBusy(false); }
  };
  const mutate = (action: Action, payload: Record<string, unknown>, noticeText: string, confirmation = "") => void run(async () => {
    if (confirmation && !await confirmCapabilityAction(confirmation)) return;
    setData(await lianxinApi.capabilityAction(action, { ...payload, ...(confirmation ? { confirmed: true } : {}) }));
    setUsageVersion(value => value + 1); setNotice(noticeText);
  });
  const close = () => void run(async () => {
    if (source.trim() && !await confirmCapabilityAction("插件源目录尚未安装，确定放弃此路径草稿并关闭？已完成的启停、收藏和安装不会撤销。")) return;
    await lianxinApi.capabilityAction("save"); onClose();
  });
  const useTool = (name: string) => void run(async () => {
    await lianxinApi.capabilityAction("save");
    if (onUse) onUse(name);
    else { await useCapabilityInMain(name); onClose(); }
  });
  const changeSource = (value: string) => { setDetected(null); setDetectError(""); setSource(value); };
  const browse = () => void run(async () => {
    if (!("__TAURI_INTERNALS__" in window)) { setNotice("浏览器预览请粘贴后端可访问的本地目录；桌面版使用原生目录选择框"); return; }
    const selected = await open({ directory: true, multiple: false, title: "选择插件文件夹" });
    if (typeof selected === "string") changeSource(selected);
  });
  const install = () => void run(async () => {
    if (!detected || !["skill", "mcp"].includes(detected.type)) return;
    if (!await confirmCapabilityAction(`即将安装并加载 ${detected.name}（${detected.type}）。\n源目录：${detected.source}\n\n插件可运行本地代码和连接外部服务。请仅安装可信来源，继续？`)) return;
    setData(await lianxinApi.capabilityAction("install", { source: detected.source, expectedName: detected.name, expectedType: detected.type, confirmed: true }));
    changeSource(""); setNotice("文件已安装并请求热加载，请检查能力库与扩展状态；不代表所有业务工具测试通过");
  });
  const matches = (text: string) => query.toLowerCase().trim().split(/\s+/).every(term => text.toLowerCase().includes(term));
  const visible = data?.items.filter(item => (!category || item.category === category) && (!sourceFilter || item.source_kind === sourceFilter) && matches(`${item.name} ${item.display_name} ${item.description} ${item.provider_name} ${item.category}`)) || [];
  const extensionCard = (item: CapabilityExtension, kind: "skill" | "mcp") => <Card key={item.name} title={item.display_name} hint={item.description}>
    <div className="capability-meta"><code>{item.name}</code><span>{item.version ? `版本 ${item.version}` : "未声明版本"}</span><span>{item.enabled ? "已启用" : "已停用"}</span>{kind === "mcp" && <span>{item.external ? item.connected ? "外部 · 已连接" : "外部 · 未连接" : item.connected ? "本地运行" : "本地 · 未就绪"}</span>}{item.license && <span>许可证：{item.license}</span>}</div>
    {item.error && <p className="global-settings-error">{item.error}</p>}
    <ToolDetails tools={item.tools} />
    <div className="capability-actions"><button onClick={() => mutate("toggle", { kind, name: item.name, enabled: !item.enabled }, "来源开关已保存并应用")}>{item.enabled ? "停用" : "启用"}</button>{kind === "mcp" && <button onClick={() => mutate("mcp-test", { name: item.name }, "检查完成，请查看扩展日志中的工具数量与结果；未执行业务工具")}>测试连接</button>}<button className="is-danger" onClick={() => mutate("uninstall", { kind, name: item.name }, "卸载已完成", `确定卸载 ${item.display_name}（${item.name}）？将永久删除本地插件目录，不可恢复。`)}>卸载</button></div>
  </Card>;

  return <div className="global-settings-overlay capability-overlay"><div className="global-settings-shell">
    <header className="global-settings-header"><div><div className="eyebrow">CAPABILITIES / EXTENSIONS</div><h2>能力中枢</h2><p>共享实际工具与扩展 · 操作即时保存，不会自动发送对话</p><div className="capability-overview"><span>能力 <strong>{data?.items.length ?? "—"}</strong></span><span>可用 <strong>{data?.items.filter(item => item.enabled && item.available).length ?? "—"}</strong></span><span title={data?.skills.map(item => `${item.name}：${item.enabled ? "启用" : "停用"}`).join("\n")}>Skills <strong>{data ? `${data.skills.filter(item => item.enabled).length}/${data.skills.length}` : "—"}</strong></span><span title={data?.mcp.map(item => `${item.name}：${item.enabled ? "启用" : "停用"}`).join("\n")}>MCP <strong>{data ? `${data.mcp.filter(item => item.enabled).length}/${data.mcp.length}` : "—"}</strong></span></div></div><button className="global-settings-close" data-settings-close aria-label="关闭能力中枢" onClick={close}><X size={18} /></button></header>
    <div className="global-settings-body"><nav className="global-settings-nav" aria-label="能力中枢分组">{groups.map(group => <button key={group.id} className={active === group.id && !browserOpen ? "is-active" : ""} aria-current={active === group.id && !browserOpen ? "page" : undefined} onClick={() => { setActive(group.id); setBrowserOpen(false); if (content.current) content.current.scrollTop = 0; }}><span><strong>{group.label}</strong><small>{group.hint}</small></span><ChevronRight size={14} /></button>)}</nav>
      <main className="global-settings-content" ref={content}>
        {error && <p className="global-settings-error" role="alert">{error}</p>}
        {data?.logs.some(item => item.level === "error") && <p className="capability-muted">最近操作错误：{data.logs.filter(item => item.level === "error").at(-1)?.message}</p>}
        <div className="capability-toolbar"><span>{data?.mcpScanning ? "启动扫描进行中…" : `上次重新扫描：${data?.lastRefresh || "本次面板尚未重新扫描"}`}</span><button disabled={busy} onClick={() => void run(async () => { setData(await lianxinApi.capabilityPanel()); setNotice("已重新读取共享状态；未重新扫描或覆盖插件源草稿"); })}>重新读取</button><button disabled={busy || !data || data.mcpScanning} onClick={() => mutate("refresh", {}, "已重新扫描；请检查各来源实际状态及扩展日志", "重新扫描会重新激活允许自动激活的Skills，并断开后重连MCP服务。请勿在工具执行中操作，继续？")}><RefreshCw size={14} />刷新能力</button><button disabled={busy} onClick={() => { setBrowserOpen(true); if (content.current) content.current.scrollTop = 0; }}>浏览器日志</button></div>
        {!data && <p role="status">尚未读取能力数据，请等待或重新读取；旧后端需要重启以加载新接口。</p>}
        {data && <fieldset className="capability-fieldset" disabled={busy}>
          {browserOpen ? <Card title="浏览器任务日志" hint="最近120条脱敏事件 · 每1.5秒刷新 · 只读，不启动浏览器"><div className="capability-actions"><button onClick={() => setBrowserOpen(false)}>返回原页面</button><button onClick={() => setBrowserVersion(value => value + 1)}>刷新日志</button></div>{browserError && <p role="alert" className="global-settings-error">{browserError}</p>}<div className="capability-browser-log">{browserRows.length ? browserRows.map((row, index) => <article key={index}><header><time>{row.timestamp}</time><strong>{row.event || "事件"}</strong><span>{row.duration_ms ? `${row.duration_ms} ms` : ""}</span></header><dl><dt>任务</dt><dd>{row.task_id || "—"}</dd><dt>步骤 / 工具</dt><dd>{row.step || "—"} / {row.tool || "—"}</dd><dt>页面</dt><dd>{row.url || "—"}</dd><dt>快照</dt><dd>{row.snapshot_id || "—"}</dd><dt>结果 / 错误</dt><dd>{row.error_code || row.result_preview || row.reason || "—"}</dd></dl></article>) : <p>暂无浏览器任务事件。</p>}</div></Card> : <>
            {active === "library" && <><div className="capability-filters"><input aria-label="搜索能力" placeholder="搜索名称、说明、工具或来源" value={query} onChange={event => setQuery(event.target.value)} /><select aria-label="能力来源" value={sourceFilter} onChange={event => setSourceFilter(event.target.value)}><option value="">全部来源</option><option value="builtin">莲心内置</option><option value="skill">Skills</option><option value="mcp">MCP</option></select><select aria-label="能力分类" value={category} onChange={event => setCategory(event.target.value)}><option value="">全部分类</option>{data.categories.filter(value => data.items.some(item => item.category === value)).map(value => <option key={value}>{value}</option>)}</select></div><p className="capability-muted">当前 {visible.length} 项；Skill / MCP 的启停会作用于整个来源。</p>{data.categories.map(value => {
              const items = visible.filter(item => item.category === value);
              return items.length ? <section key={value} className="capability-category"><h3>{value}</h3>{items.map(item => <Card key={item.name} title={item.display_name} hint={item.description}><div className="capability-meta"><code>{item.name}</code><span>{item.status} · {item.provider_name}</span>{item.version && <span>版本 {item.version}</span>}</div><p className="capability-muted">调用 {item.usage.call_count} 次{item.usage.call_count > 0 && ` · 成功率 ${(item.usage.success_rate * 100).toFixed(0)}% · 平均 ${item.usage.avg_duration_ms.toFixed(0)} ms`}</p><ToolDetails tools={[item]} /><div className="capability-actions"><button aria-label={`${item.favorite ? "取消收藏" : "收藏"}${item.display_name}`} aria-pressed={item.favorite} onClick={() => mutate("favorite", { name: item.name }, "收藏状态已保存")}><Star size={14} fill={item.favorite ? "currentColor" : "none"} />{item.favorite ? "已收藏" : "收藏"}</button><button title={item.source_kind === "builtin" ? "切换此内置工具" : "切换整个能力来源"} onClick={() => mutate("toggle", { kind: "capability", name: item.name, enabled: !item.enabled }, "启用状态已保存并应用")}>{item.enabled ? "停用" : "启用"}</button><button disabled={!item.enabled || !item.available} onClick={() => useTool(item.name)}>在对话中使用</button></div></Card>)}</section> : null;
            })}{!visible.length && <p>当前筛选条件下没有能力。</p>}</>}
            {active === "usage" && <Card title="工具使用统计" hint="沿用原版时间口径；清空会删除全部本地统计，而不是仅删除当前范围"><div className="capability-actions"><select aria-label="统计范围" value={days} onChange={event => setDays(event.target.value)}><option value="1">今天</option><option value="7">近 7 天</option><option value="30">近 30 天</option><option value="all">全部时间</option></select><button onClick={() => setUsageVersion(value => value + 1)}>刷新统计</button><button className="is-danger" onClick={() => mutate("usage-reset", {}, "已清空全部本地工具使用统计", "确定清空全部本地工具使用统计？不只是当前筛选范围，此操作不可恢复。")}>清空统计</button></div>{usage ? <><div className="capability-overview"><span>调用 {usage.overview.call_count} 次</span><span>成功率 {(usage.overview.success_rate * 100).toFixed(0)}%</span><span>平均 {usage.overview.avg_duration_ms.toFixed(0)} ms</span><span>使用过 {usage.overview.used_tool_count} 项</span><span>未使用 {usage.unused} 项</span></div>{usage.items.length ? usage.items.map(item => <div key={item.tool_name} className="capability-usage-row"><div><strong>{item.display_name}</strong><small>{item.tool_name} · {item.provider_name}</small></div><span>{item.call_count} 次 · {(item.success_rate * 100).toFixed(0)}% · {item.avg_duration_ms.toFixed(0)} ms</span></div>) : <p>这个时间范围内还没有工具调用。</p>}</> : <p role="status">正在读取统计…</p>}</Card>}
            {active === "extensions" && <><div className="capability-tabs" role="tablist" aria-label="扩展管理">{extensionTabs.map(([id, label]) => <button key={id} role="tab" aria-selected={extension === id} className={extension === id ? "is-active" : ""} onClick={() => { setExtension(id); if (content.current) content.current.scrollTop = 0; }}>{label}</button>)}</div>
              {(extension === "skills" || extension === "mcp") && <><input className="capability-extension-search" aria-label="搜索扩展" value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索扩展名称或说明" />{(extension === "skills" ? data.skills : data.mcp).filter(item => matches(`${item.name} ${item.display_name} ${item.description}`)).map(item => extensionCard(item, extension === "skills" ? "skill" : "mcp"))}{!(extension === "skills" ? data.skills : data.mcp).some(item => matches(`${item.name} ${item.display_name} ${item.description}`)) && <p>暂无匹配扩展。{extension === "skills" ? "将含 SKILL.md 的目录安装到 skills/。" : "MCP 需要可加载的 mcp-manifest.json 和服务依赖。"}</p>}</>}
              {extension === "install" && <><Card title="安装社区插件" hint="选择、输入或拖入一个目录以识别Skills/MCP；识别不会安装。安装会复制文件并加载插件代码，仅使用可信来源。"><label className="global-settings-field"><span>插件源目录</span><input aria-label="插件源目录" value={source} onChange={event => changeSource(event.target.value)} placeholder="选择或粘贴本地插件目录" /></label><div className="capability-actions"><button onClick={browse}><FolderOpen size={14} />浏览目录</button><button disabled={!detected || !["skill", "mcp"].includes(detected.type)} onClick={install}>一键安装</button></div>{detectError && <p className="global-settings-error" role="alert">{detectError}</p>}{source.trim() && !detected && !detectError && <p role="status">正在识别目录…</p>}{detected && <div className="capability-detection"><strong>{detected.type === "unknown" ? "无法识别：未找到支持的插件说明或清单" : `检测到 ${detected.type === "skill" ? "Skill" : "MCP"} · ${detected.name}`}</strong><p>{detected.description}</p><small>版本：{detected.version || "未声明"}</small><code>{detected.source}</code></div>}</Card><Card title="安装日志"><div className="capability-actions"><button onClick={() => mutate("logs-clear", { target: "install" }, "已清空安装日志显示；不会删除插件或全局日志")}>清空安装日志</button></div><Logs items={data.installLogs} /></Card></>}
              {extension === "logs" && <Card title="加载与操作日志" hint="当前后端会话中的共享管理事件；不展示MCP环境变量或技能知识正文"><div className="capability-actions"><button onClick={() => void run(async () => { setData(await lianxinApi.capabilityPanel()); setNotice("已读取最新日志"); })}>刷新日志</button><button onClick={() => mutate("logs-clear", { target: "global" }, "已清空管理日志显示；未删除浏览器审计文件、聊天或配置")}>清空日志</button></div><Logs items={data.logs} /></Card>}
            </>}
          </>}
        </fieldset>}
      </main>
    </div>
    <footer className="global-settings-footer"><div className="capability-feedback" role={error ? "alert" : "status"}><p>{busy ? "正在处理，请稍候；关闭窗口不会取消正在执行的文件或服务操作…" : notice}</p>{error && <p className="global-settings-error">{error}</p>}</div><button className="primary" disabled={busy} onClick={close}>保存并关闭</button></footer>
  </div></div>;
}
