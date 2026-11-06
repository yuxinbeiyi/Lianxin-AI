import { useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronRight, Eye, EyeOff, KeyRound, LoaderCircle, RefreshCw, Save, X } from "lucide-react";
import { lianxinApi, type ApiConfigDraft, type ApiConfigSection, type ApiConfigState } from "../services/lianxinApi";
import "../styles/api-config.css";

const groups = [
  { id: "chat", label: "聊天 API", hint: "提供商、连接参数与配置档案" },
  { id: "qq_bridge", label: "NapCat QQ", hint: "QQ 桥接与主人信息" },
  { id: "siliconflow", label: "视觉理解", hint: "SiliconFlow 多模态连接" },
  { id: "image_gen", label: "创作生图", hint: "图片生成与共享凭据" },
  { id: "video_gen", label: "创作视频", hint: "视频生成与帧数设置" },
  { id: "qweather", label: "和风天气", hint: "天气服务与主动提醒" },
] as const;
type Group = typeof groups[number]["id"];
const providers = [{ id: "deepseek", label: "自定义云端", detail: "DeepSeek / 兼容 API" }, { id: "agnes", label: "Agnes AI", detail: "独立聊天密钥" }, { id: "local", label: "本地 Ollama", detail: "本地纯聊天" }];

function Card({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return <section className="global-settings-card"><header><h3>{title}</h3>{hint && <p>{hint}</p>}</header>{children}</section>;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    try { return JSON.parse(error.message).error || error.message; } catch { return error.message; }
  }
  return "操作失败，请稍后重试";
}

export function ApiConfigPanel({ onClose }: { onClose: () => void }) {
  const [draft, setDraft] = useState<ApiConfigState | null>(null);
  const [saved, setSaved] = useState<ApiConfigState | null>(null);
  const [active, setActive] = useState<Group>("chat");
  const [selectedProfile, setSelectedProfile] = useState("");
  const [sourceProfile, setSourceProfile] = useState("");
  const [profileName, setProfileName] = useState("");
  const [clearSecrets, setClearSecrets] = useState<string[]>([]);
  const [visible, setVisible] = useState<Record<string, boolean>>({});
  const [revealed, setRevealed] = useState<Record<string, string>>({});
  const [models, setModels] = useState<string[]>([]);
  const [modelFilter, setModelFilter] = useState("");
  const [busy, setBusy] = useState("");
  const busyRef = useRef(false);
  const [notice, setNotice] = useState("正在读取 API 配置…");
  const [error, setError] = useState("");
  const [testResult, setTestResult] = useState<{ message: string; reply?: string; path?: string } | null>(null);
  const contentRef = useRef<HTMLElement>(null);
  const dirty = !!draft && !!saved && (JSON.stringify(draft.sections) !== JSON.stringify(saved.sections) || clearSecrets.length > 0 || !!sourceProfile);

  const accept = (state: ApiConfigState) => {
    setDraft(structuredClone(state)); setSaved(structuredClone(state));
    setSelectedProfile(state.activeProfile); setProfileName(state.activeProfile); setSourceProfile("");
    setClearSecrets([]); setVisible({}); setRevealed({}); setModels([]); setTestResult(null);
  };
  useEffect(() => {
    let live = true;
    lianxinApi.apiConfig().then((state) => { if (live) { accept(state); setNotice("配置已载入；修改后请保存应用"); } }).catch((cause) => { if (live) setError(errorMessage(cause)); });
    return () => { live = false; };
  }, []);
  useEffect(() => {
    const prevent = (event: BeforeUnloadEvent) => { if (dirty || busyRef.current) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [dirty]);

  const run = async (label: string, operation: () => Promise<void>) => {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(label); setError("");
    try { await operation(); } catch (cause) { setError(errorMessage(cause)); }
    finally { busyRef.current = false; setBusy(""); }
  };
  const close = () => {
    if (busyRef.current) { setNotice("操作尚未结束，请稍候；关闭窗口不会取消供应商任务"); return; }
    if (!dirty || window.confirm("有未保存的 API 修改，确定放弃并关闭？")) onClose();
  };
  const patch = (section: ApiConfigSection, field: string, value: string | number | boolean) => {
    setDraft((current) => current ? { ...current, sections: { ...current.sections, [section]: { ...current.sections[section], [field]: value } } } : current);
    if (field === "provider") { setModels([]); setTestResult(null); }
  };
  const payload = (chatOnly = false): ApiConfigDraft => ({
    revision: draft!.revision, sourceProfile,
    sections: Object.fromEntries(Object.entries(draft!.sections).filter(([section]) => !chatOnly || section === "deepseek").map(([section, values]) => [section, Object.fromEntries(Object.entries(values).filter(([field]) => !field.endsWith("_set")))])),
    clearSecrets: clearSecrets.filter((identifier) => !chatOnly || identifier.startsWith("deepseek.")),
  });
  const save = () => run("正在保存并应用…", async () => {
    const updated = await lianxinApi.saveApiConfig("save", { ...payload(), name: selectedProfile });
    accept(updated); setNotice("配置已保存并应用，不会重建当前会话"); setError((updated.warnings || []).join("；"));
  });
  const reload = () => {
    if (dirty && !window.confirm("重新加载会放弃未保存的修改，是否继续？")) return;
    void run("正在重新加载…", async () => { accept(await lianxinApi.apiConfig()); setNotice("已重新加载本地配置"); });
  };
  const selectProfile = (name: string) => {
    const profile = draft?.profiles.find((item) => item.name === name);
    if (!draft || !saved || name && !profile) return;
    const next = profile ? { ...draft.sections.deepseek, ...Object.fromEntries(Object.entries(profile).filter(([field]) => field !== "name")), provider: "deepseek" } : { ...saved.sections.deepseek, provider: "deepseek" };
    if (window.confirm("加载档案将替换当前聊天云端草稿，尚未切换生效；其它分组草稿不变。是否继续？")) {
      setDraft({ ...draft, sections: { ...draft.sections, deepseek: next } });
      setSelectedProfile(name); setProfileName(name); setSourceProfile(name);
      setClearSecrets(clearSecrets.filter((identifier) => !identifier.startsWith("deepseek.")));
      setVisible({}); setRevealed({}); setModels([]); setTestResult(null); setNotice("档案已载入草稿；点击切换并生效或底部保存后才会更换线路");
    }
  };
  const profileAction = (action: "profile-save" | "profile-apply" | "profile-delete") => {
    const name = action === "profile-save" ? profileName.trim() : selectedProfile;
    if (!name) { setError("请先填写或选择档案名称"); return; }
    if (action === "profile-save" && draft?.profiles.some((item) => item.name === name) && !window.confirm(`覆盖档案「${name}」？仅更新该档案，不切换运行线路。`)) return;
    if (action === "profile-apply" && !window.confirm(`立即切换到已保存的「${name}」？当前聊天云端草稿不保存，其它分组草稿保留。`)) return;
    if (action === "profile-delete" && !window.confirm(`删除档案「${name}」？运行中密钥保留，聊天云端草稿恢复为当前生效配置。`)) return;
    void run("正在处理档案…", async () => {
      const updated = await lianxinApi.saveApiConfig(action, { ...(action === "profile-save" ? payload(true) : { revision: draft!.revision }), name });
      setSaved(structuredClone(updated));
      setDraft((current) => current ? { ...updated, sections: { ...current.sections, ...(action === "profile-save" ? {} : { deepseek: updated.sections.deepseek }) } } : updated);
      setSelectedProfile(action === "profile-delete" ? "" : name); setProfileName(action === "profile-delete" ? "" : name);
      setSourceProfile(action === "profile-save" ? name : "");
      if (action !== "profile-save") { setClearSecrets(clearSecrets.filter((identifier) => !identifier.startsWith("deepseek."))); setVisible({}); setRevealed({}); setModels([]); }
      setNotice(action === "profile-apply" ? "已切换到所选档案；当前会话和其它草稿均保留" : action === "profile-save" ? "档案已保存；运行线路未切换" : "档案已删除；运行中的连接参数保留");
      setError((updated.warnings || []).join("；"));
    });
  };
  const test = (kind: "connection" | "image" | "video") => {
    if (kind !== "connection" && !window.confirm(`将使用当前草稿实际生成${kind === "image" ? "图片" : "视频"}，可能产生费用，并保存到本地。是否继续？${kind === "video" ? "\n视频最多等待 5 分钟，超时不代表供应商已取消。" : ""}`)) return;
    void run(kind === "video" ? "视频生成中，请勿重复创建…" : kind === "image" ? "正在生成测试图片…" : "正在测试当前草稿连接…", async () => {
      setTestResult(null);
      const result = await lianxinApi.testApiConfig({ ...payload(), kind, confirmed: kind !== "connection" });
      setTestResult(result); setNotice("测试完成；测试不会保存配置");
    });
  };

  const field = (section: ApiConfigSection, key: string, label: string, options?: { number?: boolean; min?: number; max?: number; step?: number; placeholder?: string }) => <label className="global-settings-field"><span>{label}</span><input type={options?.number ? "number" : "text"} value={String(draft!.sections[section][key])} min={options?.min} max={options?.max} step={options?.step} placeholder={options?.placeholder} onChange={(event) => patch(section, key, options?.number ? Number(event.target.value) : event.target.value)} /></label>;
  const select = (section: ApiConfigSection, key: string, label: string, choices: Array<string | number | [string | number, string]>) => <label className="global-settings-field"><span>{label}</span><select value={String(draft!.sections[section][key])} onChange={(event) => patch(section, key, typeof draft!.sections[section][key] === "number" ? Number(event.target.value) : event.target.value)}>{choices.map((choice) => { const [value, text] = Array.isArray(choice) ? choice : [choice, choice]; return <option key={value} value={value}>{text}</option>; })}</select></label>;
  const toggle = (section: ApiConfigSection, key: string, label: string, hint?: string) => <label className="global-settings-toggle"><span><strong>{label}</strong>{hint && <small>{hint}</small>}</span><input type="checkbox" checked={!!draft!.sections[section][key]} onChange={(event) => patch(section, key, event.target.checked)} /><i aria-hidden="true" /></label>;
  const secret = (section: ApiConfigSection, key = "api_key", label = "API Key") => {
    const identifier = `${section}.${key}`;
    const values = draft!.sections[section];
    const clearing = clearSecrets.includes(identifier);
    const show = () => void run("正在读取密钥…", async () => {
      if (visible[identifier]) { setVisible({ ...visible, [identifier]: false }); setRevealed({ ...revealed, [identifier]: "" }); return; }
      if (!values[key] && values[`${key}_set`] && !clearing) {
        const response = await lianxinApi.revealApiKey({ revision: draft!.revision, section, field: key, sourceProfile: section === "deepseek" ? sourceProfile : "" });
        setRevealed({ ...revealed, [identifier]: response.value });
      }
      setVisible({ ...visible, [identifier]: true });
    });
    return <div className="api-config-secret"><label className="global-settings-field"><span>{label}</span><div className="api-config-secret-input"><input aria-label={label} autoComplete="off" spellCheck={false} type={visible[identifier] ? "text" : "password"} disabled={clearing} value={String(values[key] || (visible[identifier] ? revealed[identifier] || "" : ""))} placeholder={clearing ? "保存后清除已存密钥" : values[`${key}_set`] ? "已配置 · 留空保留" : "尚未配置"} onChange={(event) => { patch(section, key, event.target.value); setRevealed({ ...revealed, [identifier]: "" }); }} /><button type="button" aria-label={`${visible[identifier] ? "隐藏" : "显示"}${label}`} disabled={clearing} onClick={show}>{visible[identifier] ? <EyeOff size={16} /> : <Eye size={16} />}</button></div></label><label className="api-config-clear"><input type="checkbox" checked={clearing} onChange={(event) => { setClearSecrets(event.target.checked ? [...clearSecrets, identifier] : clearSecrets.filter((item) => item !== identifier)); if (event.target.checked) { patch(section, key, ""); setVisible({ ...visible, [identifier]: false }); setRevealed({ ...revealed, [identifier]: "" }); } }} />保存时清除已存密钥{section === "image_gen" && "（恢复使用聊天 Agnes Key）"}</label></div>;
  };
  const fetchModels = () => void run("正在获取模型列表…", async () => { const result = await lianxinApi.apiConfigModels(payload()); setModels(result.models); setModelFilter(""); setNotice(`已获取 ${result.models.length} 个模型，可筛选选用或继续手填`); });
  const modelPicker = () => <><div className="api-config-actions"><button type="button" onClick={fetchModels}><RefreshCw size={14} />获取当前提供商模型</button><button type="button" onClick={() => test("connection")}>测试当前草稿连接</button></div>{models.length > 0 && <div className="api-config-models"><label className="global-settings-field"><span>筛选模型</span><input value={modelFilter} placeholder="按名称筛选" onChange={(event) => setModelFilter(event.target.value)} /></label><label className="global-settings-field"><span>选用模型</span><select aria-label="选用模型" value="" onChange={(event) => { if (event.target.value) patch(provider === "agnes" ? "agnes" : "deepseek", provider === "local" ? "local_model_name" : "model", event.target.value); }}><option value="">请选择（不影响手填）</option>{models.filter((model) => model.toLowerCase().includes(modelFilter.toLowerCase())).map((model) => <option key={model} value={model}>{model}</option>)}</select></label></div>}</>;

  const provider = String(draft?.sections.deepseek.provider || "deepseek");
  const liveProvider = String(saved?.sections.deepseek.provider || "deepseek");
  const liveModel = saved ? String(liveProvider === "agnes" ? saved.sections.agnes.model : saved.sections.deepseek[liveProvider === "local" ? "local_model_name" : "model"]) : "";
  const group = groups.find((item) => item.id === active)!;
  const times = Array.from({ length: 34 }, (_, index) => `${String(6 + Math.floor(index / 2)).padStart(2, "0")}:${index % 2 ? "30" : "00"}`);
  if (draft && !times.includes(String(draft.sections.qweather.remind_time))) times.push(String(draft.sections.qweather.remind_time));

  return <div className="global-settings-overlay api-config-overlay"><div className="global-settings-shell">
    <header className="global-settings-header"><div><div className="eyebrow">CONNECTIONS / API KEY</div><h2>API Key</h2><p>管理聊天、视觉、创作与天气连接 · 草稿不会自动切换线路</p></div><button type="button" className="global-settings-close" data-settings-close aria-label="关闭 API Key" onClick={close}><X size={18} /></button></header>
    <div className="api-config-current"><KeyRound size={14} /><span>当前生效：{saved ? `${providers.find((item) => item.id === liveProvider)?.label} · ${liveModel}${saved.activeProfile && liveProvider === "deepseek" ? ` · 档案 ${saved.activeProfile}` : ""}` : "正在读取…"}</span>{saved && <small>{liveProvider === "local" ? "本地免 Key" : saved.sections[liveProvider === "agnes" ? "agnes" : "deepseek"].api_key_set ? "密钥已配置" : "未配置密钥"}</small>}{dirty && <b>有未应用草稿</b>}</div>
    <div className="global-settings-body"><nav className="global-settings-nav" aria-label="API 配置分组">{groups.map((item) => <button key={item.id} type="button" className={active === item.id ? "is-active" : ""} aria-current={active === item.id ? "page" : undefined} onClick={() => { setActive(item.id); if (contentRef.current) contentRef.current.scrollTop = 0; }}><span>{item.label}</span><ChevronRight size={14} /></button>)}</nav>
      <main className="global-settings-content" ref={contentRef}>
        {!draft ? <p className="api-config-help">{error ? "配置未载入，请重新加载；不会覆盖已有配置。" : "正在读取本地配置…"}</p> : <fieldset className="api-config-fields" disabled={!!busy}>
          <div className="global-settings-group-title"><span>{String(groups.findIndex((item) => item.id === active) + 1).padStart(2, "0")}</span><h2>{group.label}</h2></div><p className="api-config-help">{group.hint}</p>
          {active === "chat" && <>
            <Card title="聊天提供商" hint="选择仅修改草稿；保存应用后，下一轮聊天使用新线路。"><div className="api-config-providers" role="group" aria-label="聊天提供商">{providers.map((item) => <button type="button" key={item.id} aria-pressed={provider === item.id} className={provider === item.id ? "is-active" : ""} onClick={() => patch("deepseek", "provider", item.id)}><strong>{item.label}</strong><small>{item.detail}</small></button>)}</div></Card>
            {provider === "deepseek" && <>
              <Card title="云端配置档案" hint="载入、保存档案和切换生效是三个不同动作；切换不清空聊天记录。"><label className="global-settings-field"><span>选择档案</span><select value={selectedProfile} onChange={(event) => selectProfile(event.target.value)}><option value="">手动配置 / 当前参数</option>{draft.profiles.map((profile) => <option key={profile.name} value={profile.name}>{profile.name}{saved?.activeProfile === profile.name ? "（当前生效）" : ""}</option>)}</select></label><label className="global-settings-field"><span>新建 / 覆盖档案名</span><input maxLength={100} value={profileName} placeholder="为这条云端线路命名" onChange={(event) => setProfileName(event.target.value)} /></label><div className="api-config-actions"><button type="button" onClick={() => profileAction("profile-save")}>保存为档案</button><button type="button" className="is-primary" disabled={!selectedProfile} onClick={() => profileAction("profile-apply")}>切换并生效</button><button type="button" className="is-danger" disabled={!selectedProfile} onClick={() => profileAction("profile-delete")}>删除档案</button></div></Card>
              <Card title="DeepSeek / 自定义兼容 API" hint="密钥默认隐藏；留空保留当前密钥，若载入档案则保留该档案密钥。">{secret("deepseek")}{field("deepseek", "base_url", "Base URL")}{field("deepseek", "model", "模型名称")}{select("deepseek", "api_format", "API 格式", [["openai", "OpenAI 兼容"], ["anthropic", "Anthropic 原生"]])}{field("deepseek", "max_tokens", "最大输出 tokens", { number: true, min: 512, max: 32768, step: 512 })}{modelPicker()}</Card>
            </>}
            {provider === "agnes" && <Card title="Agnes AI 聊天" hint="此密钥同时供创作视频使用；创作生图未设置专用 Key 时也会回退至此。">{secret("agnes", "api_key", "Agnes API Key")}{field("agnes", "model", "聊天模型")}{field("deepseek", "max_tokens", "最大输出 tokens", { number: true, min: 512, max: 32768, step: 512 })}{modelPicker()}</Card>}
            {provider === "local" && <Card title="Ollama 本地模型" hint="请先启动 Ollama。本地聊天沿用原有纯聊天能力，不启用云端工具链；路由模型留空回退到规则路由。">{field("deepseek", "local_base_url", "Ollama 地址")}{field("deepseek", "local_model_name", "聊天模型")}{field("deepseek", "router_model", "路由模型（可选）", { placeholder: "留空使用规则路由" })}{modelPicker()}</Card>}
          </>}
          {active === "qq_bridge" && <Card title="NapCat QQ 桥接" hint="先在 NapCat 中启用 OneBot WebSocket。独立 QQ 设置页的语音、分段、时序与令牌不会被此处覆盖。">{field("qq_bridge", "qq_account", "机器人 QQ 号")}{field("qq_bridge", "ws_url", "WebSocket 地址")}{field("qq_bridge", "owner_qq", "主人 QQ 号")}{field("qq_bridge", "owner_name", "主人的称呼")}{toggle("qq_bridge", "enabled", "程序启动时自动连接 QQ", "保存后更新桥接参数；不会因测试而自动连接。")}</Card>}
          {active === "siliconflow" && <Card title="SiliconFlow 视觉连接" hint="使用独立视觉密钥和模型；同时为 SiliconFlow 创作生图提供凭据。">{secret("siliconflow", "api_key", "SiliconFlow API Key")}{field("siliconflow", "base_url", "Base URL")}{field("siliconflow", "vision_model", "视觉模型")}</Card>}
          {active === "image_gen" && <>
            <Card title="图片生成服务">{toggle("image_gen", "enabled", "启用创作生图")}{select("image_gen", "provider", "生图提供商", [["agnes", "Agnes AI"], ["siliconflow", "SiliconFlow（硅基流动）"]])}</Card>
            {draft.sections.image_gen.provider === "agnes" ? <Card title="Agnes 图片生成" hint="专用密钥留空时使用聊天 Agnes Key；清除专用密钥可恢复共享。">{secret("image_gen", "agnes_api_key", "生图专用 API Key")}{field("image_gen", "model", "生图模型")}</Card> : <Card title="SiliconFlow 图片生成" hint="共用「视觉理解」中的 API Key / Base URL。横竖图尺寸沿用旧版映射，4K 会映射为 1024×1024。">{field("image_gen", "siliconflow_model", "生图模型")}{field("image_gen", "num_inference_steps", "推理步数", { number: true, min: 1, max: 100, step: 1 })}{field("image_gen", "guidance_scale", "引导系数", { number: true, min: 0, max: 30, step: 0.5 })}</Card>}
            <Card title="图片默认参数" hint="质量选项保留；当前 Agnes 接口按原有生产链路不发送 quality。">{select("image_gen", "default_size", "图片尺寸", [["1024x1024", "1024 × 1024（方图）"], ["1792x1024", "1792 × 1024（横图）"], ["1024x1792", "1024 × 1792（竖图）"], ["4k", "4K"]])}{select("image_gen", "default_quality", "默认质量", [["standard", "标准 standard"], ["hd", "高清 hd"]])}</Card><Card title="图片生成测试" hint="使用当前草稿和固定示例提示词，不上传聊天内容。生成可能计费，结果保存到本机。"><div className="api-config-actions"><button type="button" onClick={() => test("image")}>生成测试图片（可能计费）</button></div></Card>
          </>}
          {active === "video_gen" && <><Card title="Agnes 视频生成" hint="共用聊天页的 Agnes API Key；此处不新增另一份密钥。测试最多等待 5 分钟，超时任务可能仍在供应商端运行。">{toggle("video_gen", "enabled", "启用创作视频")}{field("video_gen", "model", "视频模型")}{select("video_gen", "default_duration", "视频时长（秒）", [3, 5, 10, 18])}{select("video_gen", "default_frame_rate", "帧率 fps", [16, 24, 30, 60])}<p className="api-config-help">实际请求帧数：{Math.floor((Math.min(Number(draft.sections.video_gen.default_duration) * Number(draft.sections.video_gen.default_frame_rate), 441) - 1) / 8) * 8 + 1}（8n+1，最多 441 帧）</p></Card><Card title="视频生成测试" hint="显式创建供应商任务，可能计费。结果自动保存到本机；超时后请先检查供应商控制台，避免重复付费。"><div className="api-config-actions"><button type="button" onClick={() => test("video")}>生成测试视频（可能计费）</button></div></Card></>}
          {active === "qweather" && <Card title="和风天气" hint="默认城市为空时仍可由对话、记忆或工具参数提供地点；提醒开关与原有调度共用。">{secret("qweather", "api_key", "和风天气 API Key")}{field("qweather", "api_host", "API Host", { placeholder: "例如 abc.qweatherapi.com" })}{field("qweather", "dev_id", "开发者 ID")}{field("qweather", "default_city", "默认城市")}{toggle("qweather", "auto_remind", "主动推送天气提醒")}{select("qweather", "remind_time", "提醒时间", times.sort())}</Card>}
        </fieldset>}
        {testResult && <section className="global-settings-card api-config-result" role="status"><h3>{testResult.message}</h3>{testResult.reply && <p>{testResult.reply}</p>}{testResult.path && <><p>文件已保存到本机：</p><code>{testResult.path}</code></>}</section>}
      </main>
    </div>
    <footer className="global-settings-footer"><div className="api-config-feedback" role={error ? "alert" : "status"}>{busy ? <span><LoaderCircle size={14} className="api-config-spin" />{busy}</span> : <><p>{notice}</p>{error && <p className="api-config-error">{error}</p>}</>}</div><div className="api-config-actions"><button type="button" className="secondary" disabled={!!busy} onClick={reload}><RefreshCw size={14} />重新加载</button><button type="button" className="secondary" disabled={!!busy} onClick={close}>取消</button><button type="button" className="primary" disabled={!draft || !!busy} onClick={() => void save()}><Save size={14} />保存并应用</button></div></footer>
  </div></div>;
}
