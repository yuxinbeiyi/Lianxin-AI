import { WebviewWindow } from "@tauri-apps/api/webviewWindow";

export const settingsWindows = {
  settings: { title: "全局设置", width: 1120, height: 900 },
  api: { title: "API Key", width: 1120, height: 900 },
  proactive: { title: "主动聊天", width: 1120, height: 900 },
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
    const { title, width, height } = settingsWindows[id];
    const opened = new WebviewWindow(label, {
      url: `index.html#settings/${id}`,
      title: `莲心 · ${title}`,
      width,
      height,
      minWidth: 760,
      minHeight: 560,
      resizable: true,
      decorations: false,
      center: true,
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
