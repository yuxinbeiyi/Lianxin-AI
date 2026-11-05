import { useEffect, useRef, useState } from "react";
import { BrainCircuit } from "lucide-react";
import { lianxinApi, type DecisionRuntime, type DecisionSettings } from "../services/lianxinApi";
import "./decisionPanel.css";

const localLabels: Record<string, string> = {
  ready: "已就绪", starting: "启动中，正在加载模型", stopped: "未启动", failed: "启动失败，请检查 logs/decision_local.log",
  unexpected_service: "端口上不是预期模型", authentication_required: "该端口服务需要认证，请换用独立端口",
  different_port: "托管服务仍在原端口运行，请先停止再更改端口",
};
const errors: Record<string, string> = {
  missing_api_key: "尚未填写 API Key。", cloud_context_not_allowed: "请先勾选允许云端上下文。",
  credential_unreadable: "已保存密钥无法解密，请重新填写并保存。",
  http_401: "密钥无效或已失效（401）。", http_403: "服务拒绝访问（403）。",
  http_429: "服务限流或配额不足（429），不自动换模型绕过。", transport_error: "连接失败或超时。",
  http_408: "服务请求超时。", http_500: "服务端异常。", http_502: "上游服务异常。",
  http_503: "服务暂不可用。", http_504: "上游服务超时。", budget_exhausted: "本轮决策预算已用完，已停止请求。",
  cancelled: "本轮已取消。", invalid_api_model: "不支持的 API 模型，请重新选择。",
  permission_denied: "决策已关闭或云端上下文授权已撤回，已停止后续请求。",
  local_starting: "本地模型正在启动，请等待已就绪后再测试。",
  local_stopped: "本地模型尚未启动，请先启动。", local_failed: "本地模型启动失败，请查看本地日志。",
  local_paused: "本地决策已暂停，请点击启动／恢复本地。",
  local_python_or_server_not_found: "本地 Python 或 d3_server.py 不存在，请检查路径。",
  local_start_failed: "无法启动本地进程，请检查路径与权限。",
};
const processLabels: Record<string, string> = {
  not_managed: "未关联托管进程", driver_unavailable: "驱动未提供（Windows WDDM可能不支持）",
  not_reported: "驱动未报告该进程，可能尚未加载或使用CPU", unavailable: "暂不可获取",
};
const memory = (value: number | null | undefined) => value == null ? "未知" :
  value >= 1024 ? `${(value / 1024).toFixed(2)} GiB` : `${Math.round(value)} MiB`;

export function DecisionPanel({ onClose }: { onClose: () => void }) {
  const [settings, setSettings] = useState<DecisionSettings | null>(null);
  const [runtime, setRuntime] = useState<DecisionRuntime | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [localBusy, setLocalBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [runtimeError, setRuntimeError] = useState(false);
  const mounted = useRef(true);
  const runtimeVersion = useRef(0);
  const configOperation = useRef(false);
  const localOperation = useRef(false);

  const refreshRuntime = async () => {
    const version = ++runtimeVersion.current;
    try {
      const value = await lianxinApi.decisionRuntime();
      if (mounted.current && version === runtimeVersion.current) {
        setRuntime(value);
        setRuntimeError(false);
      }
    } catch {
      if (mounted.current && version === runtimeVersion.current) setRuntimeError(true);
    }
  };

  useEffect(() => {
    mounted.current = true;
    let active = true;
    let polling = false;
    void lianxinApi.decisionSettings().then((value) => { if (active) setSettings(value); })
      .catch(() => { if (active) setNotice("无法读取配置；请确认后端已更新并重启。"); });
    const poll = async () => {
      if (polling || document.visibilityState === "hidden") return;
      polling = true;
      await refreshRuntime();
      polling = false;
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 3000);
    return () => {
      active = false;
      mounted.current = false;
      ++runtimeVersion.current;
      window.clearInterval(timer);
    };
  }, []);

  const update = <Key extends keyof DecisionSettings>(key: Key, value: DecisionSettings[Key]) => {
    setSettings((previous) => previous ? { ...previous, [key]: value } : previous);
  };

  const run = async (action: "save" | "test" | "clear") => {
    if (!settings || configOperation.current || localOperation.current) return;
    configOperation.current = true;
    setBusy(true);
    setNotice("");
    try {
      const { enabled, provider, api_model, api_fallback_enabled, allow_cloud_context, timeout_seconds, budget_seconds,
        local_model_dir, local_python, local_port, local_device, local_auto_start } = settings;
      const saved = await lianxinApi.saveDecisionSettings(action === "clear" ? { clear_api_key: true } : {
        enabled, provider, api_model, api_fallback_enabled, allow_cloud_context, timeout_seconds, budget_seconds,
        local_model_dir, local_python, local_port, local_device, local_auto_start,
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      });
      if (!mounted.current) return;
      if (action === "clear") {
        setSettings((previous) => previous ? { ...previous, api_key_configured: saved.api_key_configured,
          api_key_source: saved.api_key_source, api_key_error: saved.api_key_error } : saved);
      } else setSettings(saved);
      setApiKey("");
      if (action === "test") {
        const result = await lianxinApi.testDecision();
        if (!mounted.current) return;
        setNotice(result.ok ? `${result.model}：${result.summary}` : errors[result.error || ""] || `测试未通过：${result.error}`);
      } else {
        setNotice(action === "clear" ? "已删除保存的密钥；环境变量如有配置仍会作为后备。未保存其他表单改动。" :
          "配置已保存，下一轮对话生效；不会改变正在进行的请求。");
      }
      await refreshRuntime();
    } catch {
      if (mounted.current) setNotice("保存或测试失败；请检查后端、配置和网络。配置可能已保存。");
    } finally {
      configOperation.current = false;
      if (mounted.current) setBusy(false);
    }
  };

  const runLocal = async (action: "start" | "stop" | "refresh") => {
    if (!settings || localOperation.current || action === "start" && configOperation.current) return;
    localOperation.current = true;
    ++runtimeVersion.current;
    setLocalBusy(true);
    setNotice("");
    try {
      if (action === "refresh") {
        await refreshRuntime();
        return;
      }
      if (action === "start") {
        const { local_model_dir, local_python, local_port, local_device, local_auto_start } = settings;
        await lianxinApi.saveDecisionSettings({ local_model_dir, local_python, local_port, local_device, local_auto_start });
        if (!mounted.current) return;
      }
      const local = await lianxinApi.decisionLocal(action);
      if (!mounted.current) return;
      ++runtimeVersion.current;
      setSettings((previous) => previous ? { ...previous, local } : previous);
      setRuntime((previous) => previous ? { ...previous, local } : previous);
      setNotice(action === "start" ? "已保存本地配置并提交启动／恢复；请等待就绪。未保存云端密钥或切换运行通道。" :
        "已停止莲心托管的本地模型，本次后端运行中暂停自动拉起；点击启动／恢复可重新启用。其他GPU程序不受影响。");
      await refreshRuntime();
    } catch {
      if (mounted.current) setNotice("本地操作失败，请检查服务状态、路径或权限；停止失败时仍保留托管进程信息，请刷新核对。");
      await refreshRuntime();
    } finally {
      localOperation.current = false;
      if (mounted.current) setLocalBusy(false);
    }
  };

  const local = runtime?.local ?? settings?.local;
  const last = runtime?.last_decision;
  return <div className="overlay"><section className="overlay-panel decision-hub" role="dialog" aria-modal="true" aria-labelledby="decision-title">
    <button className="overlay-close" onClick={onClose} aria-label="关闭决策中枢">×</button>
    <div className="panel-heading"><div><span className="eyebrow"><BrainCircuit size={14} /> 莲心决策层</span><h2 id="decision-title">决策中枢</h2></div></div>
    <p className="decision-help">辅助文本回忆与瞬忆，不替代情感、权限或 Codex 执行。失败时保留既有链路；图片、视频决策尚未接入。</p>
    {!settings ? <p>正在读取配置…</p> : <>
      <fieldset className="decision-fields" disabled={busy || localBusy}>
        <section className="settings-section">
          <h3>决策链路</h3>
          <label className="setting-check"><input type="radio" name="decision-route" checked={!settings.enabled} onChange={() => update("enabled", false)} />原有记忆决策链路</label>
          <label className="setting-check"><input type="radio" name="decision-route" checked={settings.enabled} onChange={() => update("enabled", true)} />决策模型辅助链路</label>
          <p className="decision-help">原有链路不发起新的决策请求；切换链路不会自动卸载本地模型。</p>
        </section>
        <section className="settings-section">
          <h3>运行通道</h3>
          <label className="setting-check"><input type="radio" name="decision-provider" checked={settings.provider === "api"} onChange={() => update("provider", "api")} />云端 API · 可选择d3系列模型</label>
          <label className="setting-check"><input type="radio" name="decision-provider" checked={settings.provider === "local"} onChange={() => update("provider", "local")} />本地部署 · d3-edge</label>
        </section>
        {settings.provider === "api" ? <section className="settings-section">
          <h3>云端配置</h3>
          <label>主模型<select value={settings.api_model} onChange={(event) => update("api_model", event.target.value)}>
            {(settings.api_models ?? []).map((model) => <option key={model.id} value={model.id}>{model.id.split("/").pop()} · {model.size}</option>)}
          </select></label>
          <label className="setting-check"><input type="checkbox" checked={settings.api_fallback_enabled} onChange={(event) => update("api_fallback_enabled", event.target.checked)} />ultra故障时最多尝试3次，再尝试flash一次</label>
          <p className="decision-help">仅主模型为ultra时启用；共享本轮预算和重试额度，认证、配额、参数错误不盲目重试。瞬忆不额外重试，自动降级不改写主模型配置。</p>
          <label>API Key<input type="password" autoComplete="new-password" spellCheck={false} value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={settings.api_key_configured ? "已配置；留空保留，输入新值可替换" : "填写 Decision API Key"} /></label>
          <p className="decision-help">{settings.api_key_error ? errors[settings.api_key_error] : settings.api_key_configured ? `密钥已配置（${settings.api_key_source === "saved" ? "当前 Windows 用户加密保存" : "环境变量"}）。` : "尚未配置密钥。"} 密钥不回显、不写入前端存储或源码。</p>
          <label className="setting-check"><input type="checkbox" checked={settings.allow_cloud_context} onChange={(event) => update("allow_cloud_context", event.target.checked)} />允许将精简聊天背景和检索候选发送到云端</label>
          <p className="decision-help">服务方可能记录请求并用于训练。脱敏不是完整匿名化，请勿发送未经允许的公司机密。</p>
          <button className="secondary-button" type="button" onClick={() => void run("clear")}>删除已保存密钥</button>
        </section> : <section className="settings-section">
          <h3>本地配置 · d3-edge</h3>
          <label>模型目录<input value={settings.local_model_dir} onChange={(event) => update("local_model_dir", event.target.value)} /></label>
          <label>模型环境 Python 路径<input value={settings.local_python} onChange={(event) => update("local_python", event.target.value)} /></label>
          <div className="decision-grid">
            <label>监听端口<input type="number" min={1024} max={65535} value={settings.local_port} onChange={(event) => update("local_port", Number(event.target.value))} /></label>
            <label>计算设备<input value={settings.local_device} onChange={(event) => update("local_device", event.target.value)} placeholder="cuda:0 或 cpu" /></label>
          </div>
          <label className="setting-check"><input type="checkbox" checked={settings.local_auto_start} onChange={(event) => update("local_auto_start", event.target.checked)} />需要决策时自动启动本地服务</label>
          <p className="decision-help">固定 batch-size 1；仅监听127.0.0.1，不打开Gradio，不附带云端密钥。首次加载期间可能回退，请先启动至就绪。</p>
        </section>}
      </fieldset>
      <section className="settings-section decision-runtime" aria-labelledby="decision-runtime-title">
        <h3 id="decision-runtime-title">运行状态与GPU资源</h3>
        {runtimeError && <p className="decision-notice" role="status">状态更新失败，以下快照可能过期；请确认后端已更新并刷新。</p>}
        <dl className="decision-status-grid">
          <dt>本地主模型</dt><dd>d3-edge · {localLabels[local?.status || ""] || local?.status || "状态未知"}</dd>
          <dt>进程归属</dt><dd>{local?.managed ? `莲心托管 · PID ${local.pid ?? "未知"} · ${local.device ?? "设备未知"} · 端口 ${local.port ?? "未知"}` : local?.ready ? "外部启动，仅监测，不结束外部进程" : "暂无可确认的托管进程"}</dd>
          <dt>自动拉起</dt><dd>{local?.auto_start_paused ? "本次后端运行中已暂停，点击启动／恢复后再启用" : "未暂停（是否自动启动以保存配置为准）"}</dd>
        </dl>
        {runtime?.gpu.status === "ok" ? <div className="decision-gpus">{runtime.gpu.devices.map((gpu) => <div className="decision-gpu" key={gpu.uuid}>
          <strong>GPU {gpu.index} · {gpu.name}</strong>
          <p>整卡显存：{memory(gpu.used_mb)} / {memory(gpu.total_mb)} · 剩余 {memory(gpu.free_mb)}</p>
          <p>整卡利用率：{gpu.utilization_percent == null ? "未知" : `${gpu.utilization_percent}%`}</p>
        </div>)}</div> : <p className="decision-help">{runtime ? "无法读取NVIDIA显卡指标，不代表占用为0。" : "正在读取资源快照…"}</p>}
        <p className="decision-process-memory">d3托管进程显存：{runtime?.gpu.process.status === "ok" ? memory(runtime.gpu.process.used_mb) : processLabels[runtime?.gpu.process.status || ""] || "未知"}</p>
        <p className="decision-help">整卡指标包含其他程序；进程显存不可获取时不显示为0，也不用整卡显存代替。API调用本身不加载本地模型，但此前加载的服务仍可能占用显存。</p>
        <div className="decision-actions">
          <button className="secondary-button" type="button" disabled={busy || localBusy} onClick={() => void runLocal("start")}>保存本地配置并启动／恢复</button>
          <button className="secondary-button decision-stop" type="button" disabled={localBusy || !local?.managed} onClick={() => void runLocal("stop")}>停止本地模型并释放显存</button>
          <button className="secondary-button" type="button" disabled={localBusy} onClick={() => void runLocal("refresh")}>刷新状态</button>
          {localBusy && <span role="status">本地操作处理中…</span>}
        </div>
        <div className="decision-last-call">
          <h4>最近决策请求（全局）</h4>
          {last ? <>
            <p>配置模型：{last.configured_model} · 实际请求：{last.model} · {last.provider === "api" ? "云端API" : "本地"}</p>
            <p>{last.stage} · 第{last.attempt}次尝试 · {last.status === "running" ? "请求中" : last.status === "done" ? "校验通过" : "请求失败"}{last.elapsedMs != null ? ` · ${(last.elapsedMs / 1000).toFixed(2)}秒` : ""}</p>
            {last.fallback_reason && <p>本轮已由ultra降级到flash：{errors[last.fallback_reason] || last.fallback_reason}</p>}
            {last.error && <p>{errors[last.error] || `错误类型：${last.error}`}</p>}
          </> : <p className="decision-help">本次后端启动后尚未记录实际决策请求；主对话中的调用卡片为逐次执行记录。</p>}
        </div>
      </section>
      <fieldset className="decision-fields" disabled={busy || localBusy}>
        <details className="settings-section decision-advanced"><summary>高级设置 · 有界预算</summary>
          <div className="decision-grid">
            <label>单次请求超时（1–15秒）<input type="number" min={1} max={15} value={settings.timeout_seconds} onChange={(event) => update("timeout_seconds", Number(event.target.value))} /></label>
            <label>整轮决策预算（1–30秒）<input type="number" min={1} max={30} value={settings.budget_seconds} onChange={(event) => update("budget_seconds", Number(event.target.value))} /></label>
          </div>
          <p className="decision-help">回忆准备、证据评估和瞬忆共享预算。重试会按剩余时间缩短单次超时，预算不足不强行凑足次数。</p>
        </details>
        <div className="decision-actions">
          <button className="secondary-button" type="button" onClick={() => void run("save")}>保存配置</button>
          <button className="secondary-button" type="button" onClick={() => void run("test")}>保存并测试连接</button>
          {busy && <span role="status">配置／测试处理中…</span>}
        </div>
      </fieldset>
    </>}
    <p role="status" aria-live="polite" className="decision-notice">{notice}</p>
  </section></div>;
}
