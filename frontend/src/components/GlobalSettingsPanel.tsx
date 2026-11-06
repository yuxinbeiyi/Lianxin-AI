import { useEffect, useRef, useState, type ReactNode } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { Check, ChevronRight, ImagePlus, Plus, RotateCcw, Save, Trash2, X } from "lucide-react";
import { lianxinApi, type AvatarState, type BackgroundState, type SettingsPanelState } from "../services/lianxinApi";

type Props = { onClose: () => void };
type Group = "general" | "avatars" | "launch" | "performance" | "chat";
type AppItem = SettingsPanelState["quickLaunch"][number];
const groups: Array<{ id: Group; label: string }> = [{ id: "general", label: "常规" }, { id: "avatars", label: "头像风格" }, { id: "launch", label: "快捷启动" }, { id: "performance", label: "性能" }, { id: "chat", label: "桌面聊天" }];
const backgroundKeys = ["backgroundEnabled", "backgroundSource", "backgroundSourceType", "backgroundOpacity", "chatBackgroundOpacity", "backgroundFitMode"] as const;

function Card({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) { return <section className="global-settings-card"><header><h3>{title}</h3>{hint && <p>{hint}</p>}</header>{children}</section>; }
function Toggle({ label, checked, onChange, hint }: { label: string; checked: boolean; onChange: (value: boolean) => void; hint?: string }) { return <label className="global-settings-toggle"><span><strong>{label}</strong>{hint && <small>{hint}</small>}</span><input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} /><i aria-hidden="true" /></label>; }

export function GlobalSettingsPanel({ onClose }: Props) {
  const contentRef = useRef<HTMLElement>(null);
  const [state, setState] = useState<SettingsPanelState | null>(null);
  const [original, setOriginal] = useState<SettingsPanelState | null>(null);
  const [images, setImages] = useState<AvatarState | null>(null);
  const [background, setBackground] = useState<BackgroundState | null>(null);
  const [mainAvatarPreview, setMainAvatarPreview] = useState("");
  const [active, setActive] = useState<Group>("general");
  const [notice, setNotice] = useState("正在读取设置…");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [appDraft, setAppDraft] = useState<AppItem | null>(null);
  const [appDraftIndex, setAppDraftIndex] = useState<number | null>(null);
  const [crop, setCrop] = useState<{ role: "assistant" | "user" | "character"; url: string; sourcePath?: string; zoom: number; x: number; y: number } | null>(null);

  useEffect(() => {
    let live = true;
    Promise.all([lianxinApi.settingsPanels(), lianxinApi.avatars(true), lianxinApi.background(true)]).then(([loaded, avatar, wallpaper]) => {
      if (!live) return;
      setState(structuredClone(loaded)); setOriginal(structuredClone(loaded)); setImages(avatar); setBackground(wallpaper); setNotice("设置已载入");
    }).catch((cause) => { if (live) { setError(String(cause)); setNotice("设置读取失败"); } });
    return () => { live = false; };
  }, []);

  const patchGlobal = <K extends keyof SettingsPanelState["global"]>(key: K, value: SettingsPanelState["global"][K]) => setState((s) => s ? { ...s, global: { ...s.global, [key]: value } } : s);
  const patchAvatar = <K extends keyof SettingsPanelState["avatar"]>(key: K, value: SettingsPanelState["avatar"][K]) => setState((s) => s ? { ...s, avatar: { ...s.avatar, [key]: value } } : s);
  const previewBackground = async (values: Partial<SettingsPanelState["global"]>) => {
    try { await lianxinApi.saveSettingsPanels({ global: values, preview: true }); setBackground(await lianxinApi.background(true)); window.dispatchEvent(new Event("lianxin-background-refresh")); setNotice("背景实时预览中；取消时会还原"); }
    catch (cause) { setError(String(cause)); }
  };
  const close = async () => {
    if (state && original && backgroundKeys.some((key) => state.global[key] !== original.global[key])) {
      try {
        const restore = Object.fromEntries(backgroundKeys.map((key) => [key, original.global[key]])) as Partial<SettingsPanelState["global"]>;
        await lianxinApi.saveSettingsPanels({ global: restore, preview: true }); setBackground(await lianxinApi.background(true)); window.dispatchEvent(new Event("lianxin-background-refresh"));
      } catch (cause) { setError(`恢复背景失败：${String(cause)}`); return; }
    }
    onClose();
  };
  const pickPath = async (kind: "image" | "character" | "folder" | "note" | "app") => {
    try {
      const result = await open(kind === "folder" ? { directory: true, multiple: false } : { multiple: false, filters: [{ name: kind === "app" ? "应用程序" : kind === "note" ? "文本文件" : "图片", extensions: kind === "app" ? ["exe"] : kind === "note" ? ["txt"] : ["png", "jpg", "jpeg", "webp", "bmp", "gif"] }] });
      if (typeof result !== "string") return;
      if (kind === "note") patchGlobal("noteFilePath", result);
      else if (kind === "app" && appDraft) setAppDraft({ ...appDraft, path: result });
      else if (kind === "character") { patchGlobal("avatarPath", result); setMainAvatarPreview(await invoke<string>("read_image_preview", { path: result })); }
      else { const type = kind === "image" ? "single" : state?.global.backgroundSourceType === "folder_random" ? "folder_random" : "folder_first"; patchGlobal("backgroundSource", result); patchGlobal("backgroundSourceType", type); void previewBackground({ backgroundSource: result, backgroundSourceType: type }); }
    } catch (cause) { setError(`选择路径失败：${String(cause)}`); }
  };
  const importAvatar = async (role: "assistant" | "user") => {
    try {
      const path = await open({ multiple: false, filters: [{ name: "图片", extensions: ["png", "jpg", "jpeg", "webp", "bmp", "gif"] }] });
      if (typeof path === "string") setCrop({ role, url: await invoke<string>("read_image_preview", { path }), sourcePath: path, zoom: 1, x: 50, y: 50 });
    } catch (cause) { setError(`读取图片失败：${String(cause)}`); }
  };
  const importMainAvatar = async () => {
    try {
      const path = await open({ multiple: false, filters: [{ name: "图片", extensions: ["png", "jpg", "jpeg", "webp", "bmp", "gif"] }] });
      if (typeof path === "string") setCrop({ role: "character", url: await invoke<string>("read_image_preview", { path }), sourcePath: path, zoom: 1, x: 50, y: 50 });
    } catch (cause) { setError(`读取主头像图片失败：${String(cause)}`); }
  };
  const saveCrop = async () => {
    if (!crop) return;
    const image = new Image(); image.src = crop.url;
    await new Promise<void>((resolve, reject) => { image.onload = () => resolve(); image.onerror = () => reject(new Error("图片读取失败")); });
    const ratio = crop.role === "character" ? 810 / 1290 : 1;
    const cropWidth = Math.min(image.naturalWidth, image.naturalHeight * ratio) / crop.zoom;
    const cropHeight = cropWidth / ratio;
    const canvas = document.createElement("canvas"); canvas.width = crop.role === "character" ? 810 : 512; canvas.height = crop.role === "character" ? 1290 : 512;
    const ctx = canvas.getContext("2d"); if (!ctx) return;
    ctx.drawImage(image, (image.naturalWidth - cropWidth) * crop.x / 100, (image.naturalHeight - cropHeight) * crop.y / 100, cropWidth, cropHeight, 0, 0, canvas.width, canvas.height);
    try {
      const dataUrl = canvas.toDataURL("image/png");
      const updated = await lianxinApi.saveAvatarImage(crop.role, dataUrl, crop.sourcePath);
      setImages((current) => current ? { ...current, ...(crop.role === "assistant" ? { assistantDataUrl: dataUrl } : crop.role === "user" ? { userDataUrl: dataUrl } : { characterDataUrl: dataUrl }) } : current);
      if (crop.role === "character") { patchGlobal("avatarMode", "static"); patchGlobal("avatarPath", updated.uploadedPath || ""); patchGlobal("avatarSourcePath", crop.sourcePath || ""); setMainAvatarPreview(dataUrl); }
      else patchAvatar(crop.role === "assistant" ? "assistant_path" : "user_path", updated.uploadedPath || "");
      setCrop(null); setNotice("裁剪头像已保存"); setError("");
    }
    catch (cause) { setError(`头像保存失败：${String(cause)}`); }
  };
  const saveApps = async (list: AppItem[]) => {
    if (!state) return;
    try { setState(await lianxinApi.saveSettingsPanels({ quickLaunch: list })); setNotice("快捷启动列表已即时保存"); setError(""); }
    catch (cause) { setError(String(cause)); }
  };
  const save = async () => {
    if (!state) return;
    if (state.global.segmentPauseChatMin > state.global.segmentPauseChatMax || state.global.segmentPauseSemanticMin > state.global.segmentPauseSemanticMax) { setError("每组停顿的最短值不能大于最长值"); return; }
    setSaving(true); setError("");
    try { const saved = await lianxinApi.saveSettingsPanels({ global: state.global, avatar: state.avatar }); const avatar = await lianxinApi.avatars(true); setState(saved); setOriginal(structuredClone(saved)); setImages(avatar); setMainAvatarPreview(""); setNotice("设置已保存"); }
    catch (cause) { setNotice("保存失败，请检查错误并重试"); setError(String(cause)); }
    finally { setSaving(false); }
  };

  if (!state) return <div className="global-settings-overlay"><section className="global-settings-shell"><p role="status">{notice}</p>{error && <p role="alert">{error}</p>}<button data-settings-close onClick={onClose}>关闭</button></section></div>;
  const g = state.global;
  return <div className="global-settings-overlay" role="dialog" aria-modal="true" aria-labelledby="global-settings-title"><section className="global-settings-shell">
    <header className="global-settings-header"><div><span className="eyebrow">系统配置</span><h2 id="global-settings-title">全局设置</h2><p>原有配置项与生效语义保留；壁纸可实时预览并在取消时回滚。</p></div><button data-settings-close className="global-settings-close" onClick={() => void close()} aria-label="关闭全局设置"><X /></button></header>
    <div className="global-settings-body"><nav className="global-settings-nav" aria-label="设置分组">{groups.map((item) => <button key={item.id} className={active === item.id ? "is-active" : ""} onClick={() => {
      setActive(item.id);
      const content = contentRef.current;
      const target = content?.querySelector<HTMLElement>(`#global-settings-${item.id}`);
      if (content && target) {
        content.scrollTo({ top: target.offsetTop - content.offsetTop - parseFloat(getComputedStyle(content).paddingTop) });
      }
    }}>{item.label}<ChevronRight size={15} /></button>)}</nav>
    <main ref={contentRef} className="global-settings-content">
      <section id="global-settings-general" className="global-settings-group"><div className="global-settings-group-title"><span>01</span><h2>常规</h2></div>
        <Card title="对话与称呼"><label className="global-settings-field"><span>表情图片发送概率 <b>{Math.round(g.emotionProbability * 100)}%</b></span><input type="range" min="0" max="100" value={g.emotionProbability * 100} onChange={(e) => { const value = Number(e.target.value) / 100; patchGlobal("emotionProbability", value); void lianxinApi.saveSettingsPanels({ global: { emotionProbability: value } }).then(() => setNotice("表情图片概率已即时保存")).catch((cause) => setError(String(cause))); }} /></label><label className="global-settings-field"><span>用户称呼</span><input maxLength={20} value={g.userName} onChange={(e) => patchGlobal("userName", e.target.value)} /></label><Toggle label="退出时确认" checked={g.showExitConfirmation} onChange={(v) => patchGlobal("showExitConfirmation", v)} /></Card>
        <Card title="启动与窗口"><Toggle label="启动时检查" checked={g.startupCheckEnabled} onChange={(v) => patchGlobal("startupCheckEnabled", v)} /><label className="global-settings-field"><span>启动模式</span><select value={g.startupMode} onChange={(e) => patchGlobal("startupMode", e.target.value as SettingsPanelState["global"]["startupMode"])}><option value="fast">快速启动</option><option value="complete">完整启动</option></select></label><Toggle label="启用系统托盘" checked={g.trayEnabled} onChange={(v) => patchGlobal("trayEnabled", v)} /><label className="global-settings-field"><span>关闭窗口时</span><select value={g.closeBehavior} onChange={(e) => patchGlobal("closeBehavior", e.target.value as SettingsPanelState["global"]["closeBehavior"])}><option value="ask">询问</option><option value="tray">最小化到托盘</option><option value="quit">退出程序</option></select></label>{([["minimizeToTray", "最小化到托盘"], ["restoreWindowState", "恢复上次窗口状态"], ["alwaysOnTop", "窗口始终置顶"], ["reducedMotion", "减少动画"], ["desktopNotifications", "桌面通知"]] as const).map(([key, label]) => <Toggle key={key} label={label} checked={g[key]} onChange={(v) => patchGlobal(key, v)} />)}<label className="global-settings-field"><span>字体大小 <b>{g.fontSize}px</b></span><input type="range" min="10" max="20" value={g.fontSize} onChange={(e) => patchGlobal("fontSize", Number(e.target.value))} /></label><Toggle label="开机时启动莲心" checked={g.autostart} onChange={(v) => patchGlobal("autostart", v)} /></Card>
        <Card title="备忘路径与初识日期"><div className="global-settings-path"><input placeholder="默认备忘文件" value={g.noteFilePath} onChange={(e) => patchGlobal("noteFilePath", e.target.value)} /><button onClick={() => void pickPath("note")}>浏览</button></div><label className="global-settings-field"><span>初识日期</span><div className="global-settings-inline"><input type="date" value={g.firstMeetDate || ""} onChange={(e) => patchGlobal("firstMeetDate", e.target.value)} /><button onClick={() => { const today = new Date(); patchGlobal("firstMeetDate", `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`); }}>今天</button><button onClick={() => patchGlobal("firstMeetDate", "")}>清除</button></div></label></Card>
        <Card title="主界面头像"><div className="global-settings-inline"><select aria-label="头像模式" value={g.avatarMode} onChange={(e) => patchGlobal("avatarMode", e.target.value as SettingsPanelState["global"]["avatarMode"])}><option value="animated">动态形象</option><option value="static">静态图片</option></select><div className="global-settings-path"><input placeholder="静态图片路径" value={g.avatarPath} onChange={(e) => patchGlobal("avatarPath", e.target.value)} /><button onClick={() => void importMainAvatar()}>浏览并裁剪</button><button onClick={() => { patchGlobal("avatarMode", "animated"); patchGlobal("avatarPath", ""); setMainAvatarPreview(""); }}>恢复动态</button></div></div><div className="global-settings-preview">{mainAvatarPreview || images?.characterDataUrl ? <img src={mainAvatarPreview || images?.characterDataUrl} alt="主界面头像预览" /> : <span>当前形象预览</span>}</div></Card>
        <Card title="主界面背景" hint="更改即时应用；取消或关闭时恢复进入面板前的背景。"><Toggle label="启用背景壁纸" checked={g.backgroundEnabled} onChange={(v) => { patchGlobal("backgroundEnabled", v); void previewBackground({ backgroundEnabled: v }); }} /><label className="global-settings-field"><span>来源类型</span><select value={g.backgroundSourceType} onChange={(e) => { const v = e.target.value as SettingsPanelState["global"]["backgroundSourceType"]; patchGlobal("backgroundSourceType", v); void previewBackground({ backgroundSourceType: v }); }}><option value="single">单张图片</option><option value="folder_random">文件夹随机</option><option value="folder_first">文件夹首张</option></select></label><div className="global-settings-path"><input value={g.backgroundSource} onChange={(e) => { patchGlobal("backgroundSource", e.target.value); void previewBackground({ backgroundSource: e.target.value }); }} /><button onClick={() => void pickPath(g.backgroundSourceType === "single" ? "image" : "folder")}>浏览</button><button onClick={() => { patchGlobal("backgroundSource", ""); void previewBackground({ backgroundSource: "" }); }}>清除</button><button onClick={() => { const values = { backgroundSource: g.defaultBackgroundSource, backgroundSourceType: "single" as const }; patchGlobal("backgroundSource", values.backgroundSource); patchGlobal("backgroundSourceType", values.backgroundSourceType); void previewBackground(values); }}>恢复默认</button></div><label className="global-settings-field"><span>壁纸透明度 {Math.round(g.backgroundOpacity * 100)}%</span><input type="range" min="0" max="100" value={g.backgroundOpacity * 100} onChange={(e) => { const v = Number(e.target.value) / 100; patchGlobal("backgroundOpacity", v); void previewBackground({ backgroundOpacity: v }); }} /></label><label className="global-settings-field"><span>聊天区域遮罩 {Math.round(g.chatBackgroundOpacity * 100)}%</span><input type="range" min="0" max="100" value={g.chatBackgroundOpacity * 100} onChange={(e) => { const v = Number(e.target.value) / 100; patchGlobal("chatBackgroundOpacity", v); void previewBackground({ chatBackgroundOpacity: v }); }} /></label><label className="global-settings-field"><span>图片适配</span><select value={g.backgroundFitMode} onChange={(e) => { const v = e.target.value as SettingsPanelState["global"]["backgroundFitMode"]; patchGlobal("backgroundFitMode", v); void previewBackground({ backgroundFitMode: v }); }}><option value="cover">铺满（裁剪）</option><option value="contain">完整显示</option><option value="stretch">拉伸</option></select></label><div className="global-settings-preview">{background?.dataUrl ? <img src={background.dataUrl} alt="背景预览图" /> : <span>暂无背景预览图（请确认图片路径有效）</span>}</div></Card>
      </section>
      <section id="global-settings-avatars" className="global-settings-group"><div className="global-settings-group-title"><span>02</span><h2>头像风格</h2></div><Card title="聊天头像" hint="导入后可调整缩放与裁剪位置；原始图片不会被修改。"><div className="global-settings-avatar-pair">{([["assistant", "莲心头像", images?.assistantDataUrl], ["user", "我的头像", images?.userDataUrl]] as const).map(([role, label, src]) => <div className="global-settings-avatar-item" key={role}><div className="global-settings-avatar-preview">{src ? <img src={src} alt={label} /> : <span>{label}</span>}</div><strong>{label}</strong><div className="global-settings-actions"><button onClick={() => void importAvatar(role)}><ImagePlus size={15} />浏览并裁剪</button><button onClick={() => patchAvatar(role === "assistant" ? "assistant_path" : "user_path", "")}><RotateCcw size={15} />恢复默认</button></div></div>)}</div>{([["enabled", "在聊天气泡中显示头像"], ["interactions_enabled", "启用双击头像拍一拍互动"], ["dynamic_response", "动态生成拍一拍回应"], ["counter_tap", "允许莲心反拍或反摸头像"], ["animation_enabled", "启用头像震动动画"], ["response_in_chat", "拍一拍回应显示为聊天消息"]] as const).map(([key, label]) => <Toggle key={key} label={label} checked={state.avatar[key]} onChange={(v) => patchAvatar(key, v)} />)}<label className="global-settings-field"><span>冷却时间（秒）</span><input type="number" min="0.5" max="10" step="0.5" value={state.avatar.tap_cooldown_seconds} onChange={(e) => patchAvatar("tap_cooldown_seconds", Math.max(.5, Math.min(10, Number(e.target.value))))} /></label><label className="global-settings-field"><span>头像尺寸 / 气泡间距</span><div className="global-settings-inline"><input type="number" min="40" max="100" value={state.avatar.size} onChange={(e) => patchAvatar("size", Math.max(40, Math.min(100, Number(e.target.value))))} /><input type="number" min="4" max="28" value={state.avatar.gap} onChange={(e) => patchAvatar("gap", Math.max(4, Math.min(28, Number(e.target.value))))} /><Toggle label="显示边框" checked={state.avatar.border} onChange={(v) => patchAvatar("border", v)} /></div></label></Card></section>
      <section id="global-settings-launch" className="global-settings-group"><div className="global-settings-group-title"><span>03</span><h2>快捷启动</h2></div><Card title="应用列表" hint="名称必填；完整路径和可执行文件名至少填写一个。修改后即时保存。"><div className="global-settings-actions"><button onClick={() => { setAppDraftIndex(null); setAppDraft({ name: "", exe_name: "", path: "" }); }}><Plus size={15} />添加应用</button></div>{state.quickLaunch.map((app, index) => <div className="global-settings-quick-row" key={`${app.name}-${index}`}><div><strong>{app.name}</strong><small>{app.path || app.exe_name}</small></div><button onClick={() => { setAppDraftIndex(index); setAppDraft(app); }}>编辑</button><button className="danger" aria-label={`删除${app.name}`} onClick={() => { if (window.confirm(`确定删除「${app.name}」吗？`)) void saveApps(state.quickLaunch.filter((_, i) => i !== index)); }}><Trash2 size={15} /></button></div>)}</Card></section>
      <section id="global-settings-performance" className="global-settings-group"><div className="global-settings-group-title"><span>04</span><h2>性能</h2></div><Card title="设备偏好" hint="更改即时保存，重启莲心后生效。">{([["whisper", "语音识别（Whisper）", "faster-whisper 本地语音转文字"], ["funasr", "语音识别（FunASR）", "SenseVoice 全双工语音识别"], ["rag", "记忆搜索（RAG）", "BGE 语义记忆搜索模型"]] as const).map(([key, label, desc]) => <div className="global-settings-performance-row" key={key}><div><strong>{label}</strong><small>{desc}</small></div><select value={state.performance[key]} onChange={async (e) => { try { setState(await lianxinApi.saveSettingsPanels({ performance: { [key]: e.target.value as "auto" | "cpu" | "cuda" } })); setNotice(`${label}已即时保存`); } catch (cause) { setError(String(cause)); } }}><option value="auto">自动</option><option value="cpu">CPU</option><option value="cuda">GPU</option></select></div>)}</Card></section>
      <section id="global-settings-chat" className="global-settings-group"><div className="global-settings-group-title"><span>05</span><h2>桌面聊天</h2></div><Card title="分段发送停顿" hint="停顿在朗读完上一段后生效；保存后即时应用，无需重启。">{([["对话式回复", "segmentPauseChatMin", "segmentPauseChatMax", 10], ["长文 / 结构化回复", "segmentPauseSemanticMin", "segmentPauseSemanticMax", 30]] as const).map(([label, minKey, maxKey, max]) => <div className="global-settings-pause" key={label}><div><strong>{label}</strong><small>{label.startsWith("对话") ? "日常闲聊、简短回复" : "代码、列表、长段落"}</small></div><label>最短<input type="number" min="0.1" max={max} step="0.05" value={g[minKey]} onChange={(e) => patchGlobal(minKey, Number(e.target.value))} />秒</label><label>最长<input type="number" min="0.1" max={max} step="0.05" value={g[maxKey]} onChange={(e) => patchGlobal(maxKey, Number(e.target.value))} />秒</label></div>)}</Card></section>
    </main></div>
    <footer className="global-settings-footer"><p role="status" aria-live="polite">{error || notice}</p><button className="secondary" onClick={() => void close()}>取消</button><button className="primary" disabled={saving} onClick={() => void save()}>{saving ? "保存中…" : <><Save size={15} />保存设置</>}</button></footer>
    {appDraft && <div className="global-settings-modal-backdrop"><div className="global-settings-modal" role="dialog" aria-modal="true" aria-labelledby="quick-launch-title"><button className="global-settings-close" onClick={() => setAppDraft(null)} aria-label="关闭"><X /></button><h3 id="quick-launch-title">{appDraftIndex === null ? "添加应用" : "编辑应用"}</h3><label className="global-settings-field"><span>应用名称</span><input maxLength={50} value={appDraft.name} onChange={(e) => setAppDraft({ ...appDraft, name: e.target.value })} /></label><label className="global-settings-field"><span>可执行文件名</span><input maxLength={100} value={appDraft.exe_name} onChange={(e) => setAppDraft({ ...appDraft, exe_name: e.target.value })} /></label><div className="global-settings-path"><input placeholder="完整路径" value={appDraft.path} onChange={(e) => setAppDraft({ ...appDraft, path: e.target.value })} /><button onClick={() => void pickPath("app")}>浏览</button></div><div className="global-settings-modal-actions"><button className="secondary" onClick={() => setAppDraft(null)}>取消</button><button className="primary" onClick={() => { if (!appDraft.name.trim()) { setError("应用名称不能为空"); return; } if (!appDraft.path.trim() && !appDraft.exe_name.trim()) { setError("完整路径和可执行文件名至少填写一个"); return; } const list = [...state.quickLaunch]; if (appDraftIndex !== null) list[appDraftIndex] = { ...appDraft, name: appDraft.name.trim() }; else list.push({ ...appDraft, name: appDraft.name.trim() }); void saveApps(list); setAppDraft(null); }}>保存应用</button></div></div></div>}
    {crop && <div className="global-settings-modal-backdrop"><div className="global-settings-modal global-settings-crop-modal" role="dialog" aria-modal="true" aria-labelledby="crop-title"><button className="global-settings-close" onClick={() => setCrop(null)} aria-label="关闭"><X /></button><h3 id="crop-title">裁剪头像</h3><div className={`global-settings-crop-preview ${crop.role === "character" ? "is-portrait" : ""}`}><img src={crop.url} alt="待裁剪图片" style={{ width: `${crop.zoom * 100}%`, height: `${crop.zoom * 100}%`, left: `${50 - crop.x}%`, top: `${50 - crop.y}%` }} /></div><label className="global-settings-field"><span>缩放</span><input type="range" min="1" max="3" step="0.01" value={crop.zoom} onChange={(e) => setCrop({ ...crop, zoom: Number(e.target.value) })} /></label><label className="global-settings-field"><span>水平位置</span><input type="range" min="0" max="100" value={crop.x} onChange={(e) => setCrop({ ...crop, x: Number(e.target.value) })} /></label><label className="global-settings-field"><span>垂直位置</span><input type="range" min="0" max="100" value={crop.y} onChange={(e) => setCrop({ ...crop, y: Number(e.target.value) })} /></label><div className="global-settings-modal-actions"><button className="secondary" onClick={() => setCrop(null)}>取消</button><button className="primary" onClick={() => void saveCrop()}><Check size={15} />应用裁剪</button></div></div></div>}
  </section></div>;
}
