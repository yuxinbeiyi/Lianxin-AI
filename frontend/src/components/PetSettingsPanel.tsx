import { useEffect, useState, type ReactNode } from "react";
import { emit } from "@tauri-apps/api/event";
import { Save, X } from "lucide-react";
import { lianxinApi, type PetSettingsState, type PetSettingsValues } from "../services/lianxinApi";
import "../styles/pet-settings.css";

const DEFAULTS: PetSettingsValues = {
  fontSize: 12, fontBold: false, typingSpeed: 40, panelWidth: 360, panelHeight: 300,
  panelOpacity: 92, spriteScale: 100, speakingBounce: true, autoShowDialog: true,
};

function Card({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return <section className="global-settings-card"><header><h3>{title}</h3>{hint && <p>{hint}</p>}</header>{children}</section>;
}
function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return <label className="global-settings-toggle"><span><strong>{label}</strong></span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><i aria-hidden="true" /></label>;
}
function NumberField({ label, value, min, max, unit, onChange }: { label: string; value: number; min: number; max: number; unit: string; onChange: (value: number) => void }) {
  return <label className="global-settings-field"><span>{label}</span><input type="number" min={min} max={max} value={value} aria-label={label} onChange={(event) => onChange(Math.max(min, Math.min(max, Number(event.target.value) || min)))} /><b>{unit}</b></label>;
}

export function PetSettingsPanel({ onClose }: { onClose: () => void }) {
  const [values, setValues] = useState<PetSettingsValues>(DEFAULTS);
  const [triggers, setTriggers] = useState<Record<string, string>>({});
  const [state, setState] = useState<PetSettingsState | null>(null);
  const [notice, setNotice] = useState("正在读取桌宠设置…");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let live = true;
    void lianxinApi.petSettings().then((result) => {
      if (!live) return;
      setState(result);
      setValues(result.values);
      setTriggers(result.triggers);
      setNotice("桌宠设置已载入");
    }).catch((cause) => {
      if (!live) return;
      setError(String(cause));
      setNotice("桌宠设置读取失败");
    });
    return () => { live = false; };
  }, []);

  const patch = <K extends keyof PetSettingsValues>(key: K, value: PetSettingsValues[K]) => setValues((current) => ({ ...current, [key]: value }));

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const saved = await lianxinApi.savePetSettings({ values, triggers });
      setState(saved);
      setValues(saved.values);
      setTriggers(saved.triggers);
      await emit("pet-settings-updated").catch(() => undefined);
      onClose();
    } catch (cause) {
      setError(String(cause));
      setNotice("保存失败，请检查错误后重试");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="global-settings-overlay">
      <section className="global-settings-shell" role="dialog" aria-modal="true" aria-labelledby="pet-settings-title">
        <header className="global-settings-header">
          <div>
            <span className="eyebrow">桌面陪伴</span>
            <h2 id="pet-settings-title">桌宠与 Live2D 设置</h2>
            <p>外观与行为动作触发对两种角色模式共用；保存后立即应用。</p>
          </div>
          <button data-settings-close className="global-settings-close" onClick={onClose} aria-label="关闭桌宠设置"><X /></button>
        </header>
        <main className="global-settings-content">
          <Card title="外观与对话" hint="字号、逐字速度与气泡大小会立即作用于聊天气泡；角色缩放同时影响精灵和 Live2D。">
            <NumberField label="字体大小" value={values.fontSize} min={8} max={24} unit="pt" onChange={(value) => patch("fontSize", value)} />
            <Toggle label="字体加粗" checked={values.fontBold} onChange={(value) => patch("fontBold", value)} />
            <NumberField label="逐字速度" value={values.typingSpeed} min={5} max={200} unit="ms/字" onChange={(value) => patch("typingSpeed", value)} />
            <NumberField label="面板透明度" value={values.panelOpacity} min={30} max={100} unit="%" onChange={(value) => patch("panelOpacity", value)} />
            <NumberField label="角色显示比例" value={values.spriteScale} min={50} max={300} unit="%" onChange={(value) => patch("spriteScale", value)} />
            <NumberField label="面板宽度" value={values.panelWidth} min={240} max={900} unit="px" onChange={(value) => patch("panelWidth", value)} />
            <NumberField label="面板高度" value={values.panelHeight} min={200} max={900} unit="px" onChange={(value) => patch("panelHeight", value)} />
            <Toggle label="说话时弹跳" checked={values.speakingBounce} onChange={(value) => patch("speakingBounce", value)} />
            <Toggle label="收到莲心消息时自动显示对话框" checked={values.autoShowDialog} onChange={(value) => patch("autoShowDialog", value)} />
            <p className="memory-note">启动/隐藏快捷键：Ctrl+Alt+X（全局热键）。</p>
          </Card>
          <Card title="行为动作触发" hint="不同事件发生时角色播放的动画；Live2D 使用自己的表情与点头/摇头交互，精灵使用以下动作。">
            {(state?.triggerItems ?? []).map((item) => (
              <label className="global-settings-field" key={item.key}>
                <span>{item.label}</span>
                <select value={triggers[item.key] ?? state?.defaults[item.key] ?? "idle"} onChange={(event) => setTriggers((current) => ({ ...current, [item.key]: event.target.value }))}>
                  {(state?.selectableStates ?? []).map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
                </select>
              </label>
            ))}
          </Card>
        </main>
        <footer className="global-settings-footer">
          <p role="status" aria-live="polite" className={error ? "global-settings-error" : ""}>{error || notice}</p>
          <button className="secondary" onClick={onClose}>取消</button>
          <button className="primary" disabled={saving || !state} onClick={() => void save()}>{saving ? "保存中…" : <><Save size={15} />保存设置</>}</button>
        </footer>
      </section>
    </div>
  );
}
