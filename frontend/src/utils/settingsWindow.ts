import { WebviewWindow } from "@tauri-apps/api/webviewWindow";
import { availableMonitors } from "@tauri-apps/api/window";

export const NOTE_GEOMETRY_STORAGE_KEY = "lianxin.note.geometry";

export const settingsWindows = {
  note: { title: "备忘本", width: 640, height: 620, minWidth: 500, minHeight: 420 },
  pet: { title: "桌宠与 Live2D 设置", width: 620, height: 640, minWidth: 520, minHeight: 480 },
  "prism-memory": { title: "棱镜记忆系统", width: 1180, height: 860 },
  alarm: { title: "闹钟与提醒", width: 1120, height: 900 },
  settings: { title: "全局设置", width: 1120, height: 900 },
  api: { title: "API Key", width: 1120, height: 900 },
  proactive: { title: "主动聊天", width: 1120, height: 900 },
  capability: { title: "能力中枢", width: 1180, height: 900 },
  "voice-stt": { title: "语音转录", width: 1120, height: 900 },
  sound: { title: "声音设置", width: 1120, height: 900 },
  network: { title: "网络设置", width: 1120, height: 900 },
  qq: { title: "QQ 聊天", width: 1120, height: 860 },
  wechat: { title: "微信聊天", width: 1120, height: 860 },
  decision: { title: "决策中枢", width: 960, height: 800 },
  persona: { title: "人格枢控", width: 1280, height: 820 },
  constellation: { title: "星图系统", width: 1180, height: 780 },
  ripple: { title: "涟漪情感系统", width: 1180, height: 780 },
  "data-tide": { title: "数据潮汐", width: 1180, height: 780 },
} as const;

export type SettingsWindowId = keyof typeof settingsWindows;

export function isSettingsWindowId(value: string): value is SettingsWindowId {
  return Object.prototype.hasOwnProperty.call(settingsWindows, value);
}

const pendingWindows = new Map<SettingsWindowId, Promise<boolean>>();

/** 备忘本窗口上次的位置/大小（逻辑像素）；其它窗口不恢复几何。 */
async function readNoteGeometry(): Promise<{ x: number; y: number; width: number; height: number } | null> {
  try {
    const raw = localStorage.getItem(NOTE_GEOMETRY_STORAGE_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as Record<string, unknown>;
    const [x, y, width, height] = [value.x, value.y, value.width, value.height].map(Number);
    if (![x, y, width, height].every((item) => Number.isFinite(item)) || width < 300 || height < 240) return null;
    const monitors = await availableMonitors().catch(() => []);
    // 显示器变更后旧坐标可能落在屏幕外，此时回到居中，避免窗口不可见。
    const visible = monitors.some((monitor) => {
      const scale = monitor.scaleFactor;
      const left = monitor.position.toLogical(scale).x;
      const top = monitor.position.toLogical(scale).y;
      const right = left + monitor.size.toLogical(scale).width;
      const bottom = top + monitor.size.toLogical(scale).height;
      return x + 40 > left && x < right - 40 && y + 40 > top && y < bottom - 40;
    });
    return monitors.length && !visible ? null : { x, y, width, height };
  } catch {
    return null;
  }
}

export function openSettingsWindow(id: SettingsWindowId): Promise<boolean> {
  if (!("__TAURI_INTERNALS__" in window)) return Promise.resolve(false);
  const pending = pendingWindows.get(id);
  if (pending) return pending;
  const opening = createSettingsWindow(id).finally(() => pendingWindows.delete(id));
  pendingWindows.set(id, opening);
  return opening;
}

async function createSettingsWindow(id: SettingsWindowId): Promise<boolean> {
  try {
    const label = `settings-${id}`;
    const existing = await WebviewWindow.getByLabel(label);
    if (existing) {
      await existing.unminimize().catch(() => undefined);
      await existing.show().catch(() => undefined);
      await existing.setFocus().catch(() => undefined);
      return true;
    }
    const spec = settingsWindows[id];
    const minWidth = "minWidth" in spec ? spec.minWidth : 760;
    const minHeight = "minHeight" in spec ? spec.minHeight : 560;
    const geometry = id === "note" ? await readNoteGeometry() : null;
    const opened = new WebviewWindow(label, {
      url: `index.html#settings/${id}`,
      title: `莲心 · ${spec.title}`,
      width: geometry?.width ?? spec.width,
      height: geometry?.height ?? spec.height,
      ...(geometry ? { x: geometry.x, y: geometry.y } : {}),
      minWidth,
      minHeight,
      resizable: true,
      decorations: false,
      dragDropEnabled: id !== "network",
      center: !geometry,
    });
    return await new Promise<boolean>((resolve) => {
      void opened.once("tauri://created", () => resolve(true)).catch(() => resolve(false));
      void opened.once("tauri://error", () => resolve(false)).catch(() => resolve(false));
    });
  } catch (error) {
    console.warn("无法打开设置独立窗口，回退到内嵌面板", error);
    return false;
  }
}
