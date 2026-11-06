import { useState } from "react";
import { ConfigCard, ConfigField, NativeConfigFrame, useNativeConfig } from "./NativeConfigPanel";
import type { ConfigPanelValue } from "../services/lianxinApi";

const groups: Array<[string, string]> = [["funasr", "FunASR 本地"], ["volcano", "火山引擎"], ["aliyun", "阿里云"], ["whisper", "Whisper"]];
const devices: Array<[ConfigPanelValue, string]> = [["auto", "自动选择"], ["cuda", "NVIDIA GPU"], ["cpu", "CPU"]];

export function SttPanel({ onClose }: { onClose: () => void }) {
  const controller = useNativeConfig("stt", onClose);
  const [active, setActive] = useState("funasr");
  const [audioPath, setAudioPath] = useState("");
  const draft = controller.draft;
  const field = (section: string, key: string, label: string, secret = false) => <ConfigField controller={controller} section={section} field={key} label={label} secret={secret} />;
  const action = (name: string, text: string) => void controller.submit(name, { engine: active, audioPath }, text, true);
  const browse = () => void controller.run(async () => { const path = await controller.choose(); if (path) setAudioPath(path); });
  return <NativeConfigFrame controller={controller} title="语音转录" eyebrow="SPEECH / TRANSCRIPTION" hint="管理四种识别引擎；保存后用于后续语音识别，本地已加载模型切换设备可能需要重启。" groups={groups} active={active} setActive={setActive} save={() => void controller.submit("save")}>
    {draft && <><ConfigCard title="识别调度" hint="自动引擎与降级顺序沿用现有后端；页面打开不会录音、测试连接或加载模型。"><label className="global-settings-field"><span>默认识别引擎</span><select aria-label="默认识别引擎" value={String(draft.sections.stt.default_engine)} onChange={event => { controller.patch("stt", "default_engine", event.target.value); if (event.target.value !== "auto") setActive(event.target.value); }}><option value="auto">自动选择</option>{groups.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>{field("stt", "auto_fallback", "失败后自动降级")}<div className="native-config-actions"><button onClick={() => action("diagnose", "检查本地模型、GPU遥测及当前草稿中的云端凭据完整性？不会测试云端连接，但检查可能导入本地依赖。")}>一键诊断</button>{draft.migration && <button onClick={() => void controller.submit("migrate", {}, "发现旧语音配置。迁移将保存当前草稿，并仅为尚未配置的新引擎补齐旧凭据；旧配置保留，是否继续？")}>迁移旧配置</button>}</div>{draft.migration && <p className="native-config-note">检测到尚未处理的旧配置；不会在打开面板时自动迁移或覆盖新凭据。</p>}</ConfigCard>
    <ConfigCard title={groups.find(([id]) => id === active)![1]} hint={active === "funasr" ? "SenseVoice-Small，本地识别；首次下载可能联网并加载模型。" : active === "whisper" ? "开源本地识别；模型越大，下载和硬件资源需求通常越高。" : "云端识别需有效凭据和可用额度；测试请求可能计入平台用量。"}>
      {field(active, "enabled", "启用此引擎")}
      {active === "funasr" && <><ConfigField controller={controller} section="funasr" field="device" label="识别设备" options={devices} /><p className="native-config-status">保留的模型路径：{draft.modelPath || "使用后端默认模型缓存"}。检查/下载/测试使用当前已保存的FunASR设备；请先保存设备修改，已有模型可能需重启。</p><div className="native-config-actions"><button onClick={() => action("check", "检查FunASR现有依赖与模型状态？不主动合成或录音。")}>检查状态</button><button onClick={() => action("download", "下载/更新FunASR模型并尝试加载？可能联网、执行模型代码并占用GPU/CPU；使用已保存设备。")}>下载 / 更新模型</button></div></>}
      {active === "volcano" && <>{field("volcano", "appid", "AppID")}{field("volcano", "access_token", "Access Token", true)}<div className="native-config-actions"><button onClick={() => controller.help("volcano")}>控制台 / 获取额度</button></div><p className="native-config-status">使用当前草稿凭据测试，不隐式保存；额度与有效期以火山控制台为准。</p></>}
      {active === "aliyun" && <>{field("aliyun", "access_key_id", "AccessKey ID", true)}{field("aliyun", "access_key_secret", "AccessKey Secret", true)}{field("aliyun", "app_key", "AppKey", true)}<div className="native-config-actions"><button onClick={() => controller.help("aliyun-key")}>获取 AccessKey</button><button onClick={() => controller.help("aliyun-project")}>创建语音项目</button><button onClick={() => action("install", "在当前莲心Python环境安装仓库内的阿里云SDK？安装会运行可信本地setup.py并修改该环境。")}>安装 SDK</button></div><p className="native-config-status">连接测试只验证Token鉴权，不代表AppKey项目权限或完整语音转录已通过。</p></>}
      {active === "whisper" && <><ConfigField controller={controller} section="whisper" field="model_size" label="模型规模" options={["tiny", "base", "small", "medium", "large"].map(value => [value, value])} /><ConfigField controller={controller} section="whisper" field="language" label="识别语言" options={[["zh", "中文"], ["en", "英文"], ["ja", "日文"], ["ko", "韩文"], ["", "自动检测"]]} /><ConfigField controller={controller} section="whisper" field="device" label="识别设备" options={devices} /><div className="native-config-actions"><button onClick={() => action("install", "在当前莲心Python环境运行pip安装openai-whisper？可能联网并修改依赖；不是后台自动安装。")}>安装 Whisper</button></div></>}
      {active !== "aliyun" && <><label className="native-config-status" htmlFor="stt-test-audio">可选测试WAV：16kHz、16bit单声道，最长120秒 / 20MB。不选时使用旧版1秒静音探测，空返回不能当作识别成功。</label><div className="native-config-inline"><input id="stt-test-audio" aria-label="测试音频路径" value={audioPath} onChange={event => setAudioPath(event.target.value)} placeholder="留空使用静音探测（不录音）" /><div className="native-config-actions"><button onClick={browse}>选择 WAV</button><button onClick={() => setAudioPath("")}>清除</button></div></div></>}
      <div className="native-config-actions"><button onClick={() => action("test", active === "aliyun" ? "使用当前草稿凭据请求阿里云Token？不会隐式保存，也不会自动录音。" : "使用所选WAV或静音进行识别测试？本地可能下载/加载模型，云端会发送音频并可能产生用量；不隐式保存草稿。")}>{active === "aliyun" || active === "volcano" ? "测试连接" : "测试识别"}</button></div>
    </ConfigCard></>}
  </NativeConfigFrame>;
}
