import { useEffect, useState } from "react";
import { BrainCircuit } from "lucide-react";
import { lianxinApi, type DecisionSettings } from "../services/lianxinApi";
import "./decisionPanel.css";

const localLabels: Record<string, string> = {
  ready: "已就绪", starting: "启动中，正在加载模型", stopped: "未启动", failed: "启动失败，请检查 logs/decision_local.log",
  unexpected_service: "端口上不是预期模型", authentication_required: "该端口服务需要认证，请换用独立端口",
};
const errors: Record<string, string> = {
  missing_api_key: "尚未填写 API Key。", cloud_context_not_allowed: "请先勾选允许云端上下文。",
  credential_unreadable: "已保存密钥无法解密，请重新填写并保存。",
  http_401: "密钥无效或已失效（401）。", http_403: "服务拒绝访问（403）。",
  http_429: "服务限流或配额不足（429）。", transport_error: "连接失败或超时。",
  local_starting: "本地模型正在启动，请等待状态变为已就绪，再测试。",
  local_stopped: "本地模型尚未启动，请先启动。", local_failed: "本地模型启动失败，请查看本地日志。",
  local_python_or_server_not_found: "本地 Python 或 d3_server.py 不存在，请检查路径。",
  local_start_failed: "无法启动本地进程，请检查路径与权限。",
};

export function DecisionPanel({ onClose }: { onClose: () => void }) {
  const [settings, setSettings] = useState<DecisionSettings | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    void lianxinApi.decisionSettings().then((value) => { if (active) setSettings(value); })
      .catch(() => { if (active) setNotice("无法读取配置；请确认后端已更新并重启。"); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (settings?.local.status !== "starting") return;
    let active = true;
    const timer = window.setInterval(() => {
      void lianxinApi.decisionSettings().then((value) => {
        if (active) setSettings((previous) => previous ? { ...previous, local: value.local } : previous);
      }).catch(() => undefined);
    }, 1500);
    return () => { active = false; window.clearInterval(timer); };
  }, [settings?.local.status]);

  const update = <Key extends keyof DecisionSettings>(key: Key, value: DecisionSettings[Key]) => {
    setSettings((previous) => previous ? { ...previous, [key]: value } : previous);
  };

  const run = async (action: "save" | "test" | "start" | "stop" | "clear" | "refresh") => {
    if (!settings || busy) return;
    setBusy(true);
    setNotice("");
    try {
      if (action === "refresh") {
        const value = await lianxinApi.decisionSettings();
        setSettings((previous) => previous ? { ...previous, local: value.local } : value);
        setNotice("本地状态已刷新。");
        return;
      }
      const { enabled, provider, allow_cloud_context, timeout_seconds, budget_seconds,
        local_model_dir, local_python, local_port, local_device, local_auto_start } = settings;
      const saved = await lianxinApi.saveDecisionSettings({ enabled, provider, allow_cloud_context,
        timeout_seconds, budget_seconds, local_model_dir, local_python, local_port, local_device, local_auto_start,
        ...(action === "clear" ? { clear_api_key: true } : apiKey.trim() ? { api_key: apiKey.trim() } : {}) });
      setSettings(saved);
      setApiKey("");
      if (action === "test") {
        const result = await lianxinApi.testDecision();
        setNotice(result.ok ? `${result.model}：${result.summary}` : errors[result.error || ""] || `测试未通过：${result.error}`);
        const value = await lianxinApi.decisionSettings();
        setSettings((previous) => previous ? { ...previous, local: value.local } : value);
      } else if (action === "start" || action === "stop") {
        const local = await lianxinApi.decisionLocal(action);
        setSettings((previous) => previous ? { ...previous, local } : previous);
        setNotice(action === "start" ? "已提交本地启动，请等待就绪；首次加载不保证立即完成。" : "已停止本次后端托管的本地服务。");
      } else {
        setNotice(action === "clear" ? "已删除加密保存的密钥；如存在环境变量，仍会作为后备来源。" : "已永久保存，下一轮对话生效，无需再次设置环境变量。");
      }
    } catch {
      setNotice("操作失败，请检查后端是否已更新、端口、模型目录及 Python 路径；配置可能已保存，可刷新核对。");
    } finally {
      setBusy(false);
    }
  };

  return <div className="overlay"><section className="overlay-panel decision-hub" role="dialog" aria-modal="true" aria-labelledby="decision-title">
    <button className="overlay-close" onClick={onClose} aria-label="关闭决策中枢">×</button>
    <div className="panel-heading"><div><span className="eyebrow"><BrainCircuit size={14} /> 莲心决策层</span><h2 id="decision-title">决策中枢</h2></div></div>
    <p className="decision-help">当前接管文本回忆准备，不替代情感、权限或 Codex 执行。模型调用和记忆查询分别显示卡片；失败时保留原有链路。</p>
    {!settings ? <p>正在读取配置…</p> : <fieldset className="decision-fields" disabled={busy}>
      <section className="settings-section">
        <h3>回忆决策链路</h3>
        <label className="setting-check"><input type="radio" name="decision-route" checked={!settings.enabled} onChange={() => update("enabled", false)} />原有记忆决策链路</label>
        <label className="setting-check"><input type="radio" name="decision-route" checked={settings.enabled} onChange={() => update("enabled", true)} />决策模型链路</label>
        <p className="decision-help">选择原有链路后不再发起决策模型请求；不会自动关闭已经加载的本地服务。</p>
      </section>
      <section className="settings-section">
        <h3>决策模型运行方式</h3>
        <label className="setting-check"><input type="radio" name="decision-provider" checked={settings.provider === "api"} onChange={() => update("provider", "api")} />云端 API · d3-ultra（不占本机决策显存）</label>
        <label className="setting-check"><input type="radio" name="decision-provider" checked={settings.provider === "local"} onChange={() => update("provider", "local")} />本地部署 · d3-edge（使用所选本机设备）</label>
      </section>
      {settings.provider === "api" ? <section className="settings-section">
        <h3>云端连接</h3>
        <p className="decision-help">模型：{settings.api_model}。云端与本地不是同一权重，不承诺判断效果一致。</p>
        <label>API Key<input type="password" autoComplete="new-password" spellCheck={false} value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={settings.api_key_configured ? "已配置；留空保留，输入新值可替换" : "填写 Decision API Key"} /></label>
        <p className="decision-help">{settings.api_key_error ? errors[settings.api_key_error] : settings.api_key_configured ? `密钥已配置（${settings.api_key_source === "saved" ? "当前 Windows 用户加密保存" : "环境变量"}）。` : "尚未配置密钥。"} 密钥不回显、不写入前端存储或源码。</p>
        <label className="setting-check"><input type="checkbox" checked={settings.allow_cloud_context} onChange={(event) => update("allow_cloud_context", event.target.checked)} />允许将精简聊天背景和检索候选发送到云端</label>
        <p className="decision-help">服务方可能记录请求并用于训练。脱敏不是完整匿名化，请勿发送未经允许的公司机密。</p>
        <button className="secondary-button" type="button" onClick={() => void run("clear")}>删除已保存密钥</button>
      </section> : <section className="settings-section">
        <h3>本地服务 · {localLabels[settings.local.status] || settings.local.status}</h3>
        <label>模型目录<input value={settings.local_model_dir} onChange={(event) => update("local_model_dir", event.target.value)} /></label>
        <label>模型环境 Python 路径<input value={settings.local_python} onChange={(event) => update("local_python", event.target.value)} /></label>
        <div className="decision-grid">
          <label>监听端口<input type="number" min={1024} max={65535} value={settings.local_port} onChange={(event) => update("local_port", Number(event.target.value))} /></label>
          <label>计算设备<input value={settings.local_device} onChange={(event) => update("local_device", event.target.value)} placeholder="cuda:0 或 cpu" /></label>
        </div>
        <label className="setting-check"><input type="checkbox" checked={settings.local_auto_start} onChange={(event) => update("local_auto_start", event.target.checked)} />需要决策时自动启动本地服务</label>
        <p className="decision-help">复用模型目录中的 d3_server.py，仅监听 127.0.0.1，不打开 Gradio，不附带云端密钥。首次加载期间当前对话可能回退，建议先启动至就绪后测试。</p>
        <div className="decision-actions">
          <button className="secondary-button" type="button" onClick={() => void run("start")}>保存并启动本地</button>
          <button className="secondary-button" type="button" disabled={!settings.local.managed} onClick={() => void run("stop")}>停止托管服务</button>
          <button className="secondary-button" type="button" onClick={() => void run("refresh")}>刷新状态</button>
        </div>
      </section>}
      <section className="settings-section">
        <h3>有界预算</h3>
        <div className="decision-grid">
          <label>单次请求超时（1–15 秒）<input type="number" min={1} max={15} value={settings.timeout_seconds} onChange={(event) => update("timeout_seconds", Number(event.target.value))} /></label>
          <label>决策总预算（1–30 秒）<input type="number" min={1} max={30} value={settings.budget_seconds} onChange={(event) => update("budget_seconds", Number(event.target.value))} /></label>
        </div>
      </section>
      <div className="decision-actions">
        <button className="secondary-button" type="button" onClick={() => void run("save")}>保存配置</button>
        <button className="secondary-button" type="button" onClick={() => void run("test")}>保存并测试连接</button>
        {busy && <span>处理中…</span>}
      </div>
    </fieldset>}
    <p role="status" aria-live="polite" className="decision-notice">{notice}</p>
  </section></div>;
}
