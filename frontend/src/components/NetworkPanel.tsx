import { useRef, useState } from "react";
import { lianxinApi, type NativeConfigState } from "../services/lianxinApi";
import { ConfigCard, ConfigField, NativeConfigFrame, useNativeConfig } from "./NativeConfigPanel";
import { confirmConfigAction } from "../utils/nativeConfig";

const groups: Array<[string, string]> = [["tavily", "Tavily 搜索"], ["firecrawl", "Firecrawl 爬虫"], ["zhihu", "知乎搜索"], ["order", "工具调用顺序"], ["proxy", "代理设置"], ["bilibili", "B站账号"]];

export function NetworkPanel({ onClose }: { onClose: () => void }) {
  const controller = useNativeConfig("network", onClose);
  const [active, setActive] = useState("tavily");
  const dragging = useRef<{ kind: "search" | "fetch"; id: string } | null>(null);
  const draft = controller.draft;
  const field = (section: string, key: string, label: string, secret = false) => <ConfigField controller={controller} section={section} field={key} label={label} secret={secret} />;
  const move = (kind: "search" | "fetch", id: string, target: string) => {
    const key = `${kind}_order`;
    const order = [...draft!.sections.network_tool_order[key] as string[]];
    const source = order.indexOf(id), destination = order.indexOf(target);
    if (source < 0 || destination < 0 || source === destination) return;
    order.splice(source, 1); order.splice(destination, 0, id);
    controller.patch("network_tool_order", key, order);
  };
  const toggle = (id: string, checked: boolean) => controller.setDraft(current => {
    if (!current?.catalog) return current;
    const ids = id === "browser_fetch" || id === "browser_search" ? ["browser_fetch", "browser_search"] : [id];
    return { ...current, catalog: Object.fromEntries(Object.entries(current.catalog).map(([kind, tools]) => [kind, tools.map(item => ids.includes(item.id) ? { ...item, checked } : item)])) as NativeConfigState["catalog"] };
  });
  const reset = () => void controller.run(async () => {
    if (!await confirmConfigAction("立即恢复推荐联网顺序、启用全部联网来源、重试2次和额度自动回退？不会清除API Key、代理或Cookie，其它草稿保留；取消关窗不撤销此动作。")) return;
    const remote = await lianxinApi.nativeConfigAction("network", "reset", { revision: draft!.revision, confirmed: true });
    controller.accept(remote, true, true);
    controller.setDraft(current => current ? { ...current, catalog: remote.catalog, sections: { ...current.sections, network_tool_order: remote.sections.network_tool_order } } : current);
    controller.setNotice("默认联网策略已即时恢复；其它未保存草稿保留");
  });
  const save = () => void controller.submit("save", { switches: Object.fromEntries(Object.values(draft!.catalog || {}).flat().map(item => [item.id, item.checked])) });
  return <NativeConfigFrame controller={controller} title="网络设置" eyebrow="NETWORK / SEARCH ROUTING" hint="沿用统一联网路由与能力中枢启停状态；普通配置保存才生效，凭据只存本地。" groups={groups} active={active} setActive={setActive} save={save} footerActions={draft && <button className="secondary" disabled={controller.locked} onClick={reset}>恢复默认排序</button>}>
    {draft && <>{["tavily", "firecrawl", "zhihu"].includes(active) && <ConfigCard title={groups.find(([id]) => id === active)![1]} hint={active === "tavily" ? "关键词搜索，用于查找实时信息；额度以服务商控制台为准。" : active === "firecrawl" ? "针对已有URL读取网页正文并转为Markdown；不是关键词搜索源。" : "知乎问答、文章和中文社区信息搜索；Access Secret从开发者平台获取。"}>{field(active, active === "zhihu" ? "access_secret" : "api_key", active === "zhihu" ? "Access Secret" : "API Key", true)}<div className="native-config-actions"><button onClick={() => controller.help(active)}>打开注册 / 凭据入口</button></div><p className="native-config-status">启用勾选与凭据是否齐全分别显示。留空保留原凭据，勾选清除后保存才删除；本页不会自动测试或发起搜索。</p></ConfigCard>}
    {active === "order" && <><ConfigCard title="工具调用顺序" hint="从上到下为优先级。拖动整行或用上移/下移排序；取消勾选禁止参与联网路由。浏览器搜索与网页读取共用一个内置开关。">{(["search", "fetch"] as const).map(kind => {
      const order = draft.sections.network_tool_order[`${kind}_order`] as string[];
      return <section key={kind}><h4>{kind === "search" ? "搜索来源（关键词搜索）" : "网页读取（已有URL后）"}</h4><ol className="native-config-order" aria-label={kind === "search" ? "搜索来源顺序" : "网页读取顺序"}>{order.map((id, index) => {
        const tool = draft.catalog?.[kind].find(item => item.id === id);
        return tool && <li key={id} draggable={!controller.locked} data-tool={id} onDragStart={event => { dragging.current = { kind, id }; event.dataTransfer.setData("text/plain", id); event.dataTransfer.effectAllowed = "move"; }} onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); if (dragging.current?.kind === kind) move(kind, dragging.current.id, id); dragging.current = null; }} onDragEnd={() => { dragging.current = null; }}><input type="checkbox" aria-label={`启用${tool.name}`} checked={tool.checked} onChange={event => toggle(id, event.target.checked)} /><div><strong>{index + 1}. {tool.name}</strong><small>{tool.description}</small><small>已保存状态：{tool.state}{tool.checked !== controller.saved?.catalog?.[kind].find(item => item.id === id)?.checked ? " · 启停草稿尚未保存" : ""}</small></div><div className="native-config-actions"><button disabled={index === 0} aria-label={`上移${tool.name}`} onClick={() => move(kind, id, order[index - 1])}>上移</button><button disabled={index === order.length - 1} aria-label={`下移${tool.name}`} onClick={() => move(kind, id, order[index + 1])}>下移</button></div></li>;
      })}</ol></section>;
    })}<div className="native-config-actions"><button onClick={reset}>恢复默认排序与启用状态</button></div><ConfigField controller={controller} section="network_tool_order" field="retry_count" label="临时失败重试次数" min={0} max={5} step={1} />{field("network_tool_order", "fallback_on_quota", "额度不足自动换下一个")}</ConfigCard></>}
    {active === "proxy" && <ConfigCard title="智能代理" hint="先直连，失败后按现有策略尝试代理；不是把所有请求强制改为代理。">{field("proxy", "enabled", "启用智能代理")}{field("proxy", "http_proxy", "HTTP 代理地址", true)}{field("proxy", "https_proxy", "HTTPS 代理地址", true)}{field("proxy", "no_proxy", "直连白名单")}<p className="native-config-status">常用本机代理示例：http://127.0.0.1:7890；白名单用逗号分隔。代理URL可能包含账号密码，因此也按敏感凭据保护。</p></ConfigCard>}
    {active === "bilibili" && <ConfigCard title="B站账号 Cookie" hint="登录态用于视频字幕等既有功能；不改变主动聊天的B站互动授权开关。">{field("bilibili", "sessdata", "SESSDATA", true)}{field("bilibili", "bili_jct", "bili_jct", true)}<p className="native-config-status">获取方式：浏览器登录B站 → F12 → Application → Cookies → bilibili.com。过期需重新获取；未配置时可能只有视频基本信息而无字幕。凭据保存在本地用户目录，不写入仓库。</p></ConfigCard>}</>}
  </NativeConfigFrame>;
}
