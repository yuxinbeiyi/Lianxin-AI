import { useEffect, useRef, useState } from "react";
import { lianxinApi, type ConfigPanelValue, type NativeConfigState } from "../services/lianxinApi";
import { ConfigCard, ConfigField, NativeConfigFrame, useNativeConfig } from "./NativeConfigPanel";
import { confirmConfigAction } from "../utils/nativeConfig";

type Runtime = { running: boolean; pid: number | null; gpu: { status: string; devices: Array<{ name: string; used_mb: number | null; total_mb: number | null }>; process: { used_mb: number | null; status: string } } };
const groups: Array<[string, string]> = [["basic", "基本设置"], ["advanced", "高级参数"]];

export function SoundPanel({ onClose }: { onClose: () => void }) {
  const controller = useNativeConfig("sound", onClose);
  const [active, setActive] = useState("basic");
  const [refs, setRefs] = useState<Array<{ path: string; label: string }>>([]);
  const [runtime, setRuntime] = useState<Runtime | null>(null);
  const [pathStatus, setPathStatus] = useState("");
  const [volumeBusy, setVolumeBusy] = useState(false);
  const volumeQueue = useRef<Record<string, number>>({});
  const volumeWriting = useRef(false);
  const draft = controller.draft;
  const refreshRefs = async () => setRefs((await lianxinApi.nativeConfigRead<{ items: Array<{ path: string; label: string }> }>("sound", "references")).items);
  const refreshRuntime = async () => setRuntime(await lianxinApi.nativeConfigRead<Runtime>("sound", "runtime"));
  useEffect(() => {
    void refreshRefs().catch(() => controller.setError("参考音频列表读取失败，可点击刷新重试"));
    void refreshRuntime().catch(() => controller.setError("声音进程/GPU状态读取失败，不代表模型未运行"));
  }, []);
  useEffect(() => { if (controller.task?.status === "done" || controller.task?.status === "error") void refreshRuntime().catch(() => undefined); }, [controller.task?.status]);
  useEffect(() => {
    if (!draft) return;
    let live = true;
    const timer = setTimeout(() => {
      void lianxinApi.nativeConfigAction("sound", "path-check", { path: draft.sections.tts.gpt_sovits_path }).then(result => { if (live) setPathStatus((result as unknown as { message: string }).message); }).catch(() => { if (live) setPathStatus("目录状态暂不可读，请点击检查目录重试"); });
    }, 250);
    return () => { live = false; clearTimeout(timer); };
  }, [draft?.sections.tts.gpt_sovits_path]);
  useEffect(() => {
    if (!volumeBusy) return;
    const prevent = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [volumeBusy]);
  const syncVolumes = (remote: NativeConfigState) => {
    const update = (current: NativeConfigState | null) => current && remote.output ? { ...current, output: { ...current.output!, tts_volume: remote.output.tts_volume, sfx_volume: remote.output.sfx_volume } } : current;
    controller.setSaved(update);
    controller.setDraft(current => {
      const updated = update(current);
      if (!updated?.output) return updated;
      return { ...updated, output: { ...updated.output, ...volumeQueue.current } };
    });
  };
  const volume = (field: "tts_volume" | "sfx_volume", value: number) => {
    volumeQueue.current[field] = value;
    controller.setDraft(current => current ? { ...current, output: { ...current.output!, [field]: value } } : current);
    if (volumeWriting.current) return;
    volumeWriting.current = true; setVolumeBusy(true);
    void (async () => {
      try {
        while (Object.keys(volumeQueue.current).length) {
          const field = Object.keys(volumeQueue.current)[0];
          const value = volumeQueue.current[field];
          delete volumeQueue.current[field];
          syncVolumes(await lianxinApi.nativeConfigAction("sound", "volume", { field, value }));
        }
        controller.setNotice("音量已即时保存；取消不撤销音量修改");
      } catch {
        volumeQueue.current = {};
        controller.setError("音量保存未完成，请重新读取核对实际值；不假定已回滚");
        try { syncVolumes(await lianxinApi.nativeConfig("sound")); } catch { }
      } finally { volumeWriting.current = false; setVolumeBusy(false); }
    })();
  };
  const frameController = { ...controller, close: () => { if (volumeWriting.current) { controller.setError("音量仍在保存，请稍候关闭"); return; } controller.close(); } };
  const field = (key: string, label: string, options?: Array<[ConfigPanelValue, string]>, hint?: string) => <ConfigField controller={controller} section="tts" field={key} label={label} options={options} hint={hint} />;
  const browse = (key: string, directory = false, extensions = ["wav"]) => void controller.run(async () => { const path = await controller.choose(directory, extensions); if (path) controller.patch("tts", key, path); });
  const inspectPath = () => void controller.run(async () => {
    const result = await lianxinApi.nativeConfigAction("sound", "path-check", { path: draft!.sections.tts.gpt_sovits_path }) as unknown as { message: string };
    setPathStatus(result.message);
  });
  const save = () => { if (volumeWriting.current) { controller.setError("请等待音量保存结束"); return; } void controller.submit("save", { output: { silent_mode: draft!.output!.silent_mode } }); };
  return <NativeConfigFrame controller={frameController} title="声音设置" eyebrow="AUDIO / VOICE OUTPUT" hint="统一后端声音引擎；音量即时保存，试听会保存引擎和高级参数，取消不会撤销已执行动作。" groups={groups} active={active} setActive={setActive} save={save}>
    {draft && <ConfigCard title="当前引擎与资源状态"><p className="native-config-status">已保存引擎：{controller.saved?.sections.tts.engine === "gpt_sovits" ? "GPT-SoVITS" : "Edge-TTS"} · GPT worker：{runtime ? runtime.running ? `运行中（PID ${runtime.pid}）` : "已释放 / 未运行" : "读取中"} · 进程显存：{runtime?.gpu.process.used_mb == null ? "未知（不等于0）" : `${runtime.gpu.process.used_mb} MiB`}</p></ConfigCard>}
    {draft && active === "basic" && <p className="native-config-status" role="status">安装目录：{pathStatus || "正在检查目录…"}</p>}
    {draft && <>{active === "basic" ? <><ConfigCard title="播放与音量" hint="音量滑动即时生效并保存；静默模式仅在点击保存后提交。"><label className="global-settings-field"><span>静默模式</span><input aria-label="静默模式" type="checkbox" checked={draft.output!.silent_mode} onChange={event => controller.setDraft(current => current ? { ...current, output: { ...current.output!, silent_mode: event.target.checked } } : current)} /></label>{(["tts_volume", "sfx_volume"] as const).map(key => <label className="native-config-volume" key={key}><span>{key === "tts_volume" ? "语音音量" : "音效音量"}</span><input aria-label={key === "tts_volume" ? "语音音量" : "音效音量"} type="range" min="0" max="1" step="0.01" value={draft.output![key]} onChange={event => volume(key, Number(event.target.value))} /><output>{Math.round(draft.output![key] * 100)}%</output></label>)}{volumeBusy && <small role="status">音量保存中…</small>}</ConfigCard>
    <ConfigCard title="语音合成引擎" hint="只使用选定声线；合成失败不自动切换到另一种引擎。">{field("engine", "合成引擎", [["edge_tts", "Edge-TTS 标准发音"], ["gpt_sovits", "GPT-SoVITS 声音克隆"]])}{field("gpt_sovits_path", "GPT-SoVITS 安装目录")}<div className="native-config-actions"><button onClick={() => browse("gpt_sovits_path", true)}>浏览安装目录</button><button onClick={inspectPath}>检查目录</button></div>{field("gpt_sovits_version", "模型版本", ["v2Pro", "v3", "v4", "v2ProPlus"].map(value => [value, value]))}{field("gpt_sovits_gpt_path", "GPT 权重 (.ckpt)")}<div className="native-config-actions"><button onClick={() => browse("gpt_sovits_gpt_path", false, ["ckpt"])}>选择 GPT 权重</button></div>{field("gpt_sovits_sovits_path", "SoVITS 权重 (.pth)")}<div className="native-config-actions"><button onClick={() => browse("gpt_sovits_sovits_path", false, ["pth"])}>选择 SoVITS 权重</button></div></ConfigCard>
    <ConfigCard title="参考音频与情绪" hint="WAV参考建议5–15秒、24000Hz；支持按情绪自动选择，也可指定本地文件。"><select className="native-config-ref-select" aria-label="参考音频列表" value={String(draft.sections.tts.ref_wav_override)} onChange={event => controller.patch("tts", "ref_wav_override", event.target.value)}><option value="">自动匹配（根据情绪选择）</option>{draft.sections.tts.ref_wav_override && !refs.some(item => item.path === draft.sections.tts.ref_wav_override) && <option value={String(draft.sections.tts.ref_wav_override)}>当前指定：{String(draft.sections.tts.ref_wav_override)}</option>}{refs.map(item => <option key={item.path} value={item.path}>{item.label}</option>)}</select>{field("ref_wav_override", "参考 WAV 路径")}<div className="native-config-actions"><button onClick={() => void controller.run(refreshRefs)}>刷新参考列表</button><button onClick={() => browse("ref_wav_override")}>浏览 WAV</button><button onClick={() => void controller.submit("transcribe", {}, "重新转录后端当前参考音频目录？会加载FunASR并写入转录缓存；当前未保存路径草稿不会生效。", true)}>重新转录参考音频</button></div>{field("default_mood", "默认情绪", [["auto", "自动匹配"], ["casual", "日常温柔"], ["tsundere", "傲娇"], ["romantic", "深情"], ["long", "长句稳定"]])}<ConfigField controller={controller} section="tts" field="speed" label="语速（GPT-SoVITS）" min={.5} max={2} step={.1} /></ConfigCard>
    <ConfigCard title="预热与GPU资源" hint="释放只关闭GPT-SoVITS独立worker；不宣称整卡空闲，也不会停止其它模型。">{field("tts_warmup", "启动时预热引擎")}{field("gpt_sovits_idle_timeout_seconds", "空闲自动释放", [[60, "1分钟"], [300, "5分钟（推荐）"], [900, "15分钟"], [0, "保持运行"]])}<p className="native-config-status">{runtime ? `worker：${runtime.running ? `运行中 · PID ${runtime.pid}` : "未运行"}\n该进程显存：${runtime.gpu.process.used_mb === null ? `不可读（${runtime.gpu.process.status}）` : `${runtime.gpu.process.used_mb} MiB`}\n${runtime.gpu.devices.map(item => `${item.name}：整卡 ${item.used_mb ?? "?"}/${item.total_mb ?? "?"} MiB`).join("\n") || "GPU遥测不可用"}` : "正在读取进程状态…"}</p><div className="native-config-actions"><button onClick={() => void controller.run(refreshRuntime)}>刷新资源状态</button><button onClick={() => void controller.submit("release", {}, "立即释放GPT-SoVITS独立进程及显存？下次使用需重新加载；运行中的聊天/朗读/语音通话会阻止释放。", true)}>立即释放 GPT-SoVITS GPU</button></div></ConfigCard>
    <ConfigCard title="试听" hint="先保存当前引擎、路径、参考音频、情绪、语速与高级参数，再通过主后端合成和播放；静默与启动预热草稿不提交。"><div className="native-config-actions"><button onClick={() => void controller.submit("preview", {}, "试听会把当前引擎和高级参数写入本地配置，再合成播放。取消关闭不会撤销这些已保存值；使用已生效静默模式，继续？", true)}>试听当前配置</button></div></ConfigCard></> : <><ConfigCard title="合成高级参数" hint="仅GPT-SoVITS使用；保存或试听才落盘。默认重置沿用旧面板推荐值，不覆盖已有配置。">{([ ["temperature", "稳定性 (temperature)", .1, 1, .01], ["top_k", "采样范围 (top_k)", 1, 20, 1], ["top_p", "核采样 (top_p)", .1, 1, .01], ["sample_steps", "合成步数 (sample_steps)", 4, 64, 1], ["pause_second", "句间停顿（秒）", 0, 1, .01] ] as const).map(([key, label, min, max, step]) => <ConfigField key={key} controller={controller} section="tts" field={key} label={label} min={min} max={max} step={step} />)}{field("how_to_cut", "切句方式", ["不切", "凑四句一切", "凑50字一切", "按中文句号。切", "按英文句号.切", "按标点符号切"].map(value => [value, value]))}<div className="native-config-actions"><button onClick={() => void controller.run(async () => { if (await confirmConfigAction("将高级参数草稿恢复默认？点击保存或试听后才落盘。")) { for (const [key, value] of Object.entries(draft.advancedDefaults || {})) controller.patch("tts", key, value); } })}>恢复高级参数默认值</button></div></ConfigCard></>}</>}
  </NativeConfigFrame>;
}
