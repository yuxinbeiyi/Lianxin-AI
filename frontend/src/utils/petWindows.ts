import { emit } from "@tauri-apps/api/event";
import { WebviewWindow } from "@tauri-apps/api/webviewWindow";
import { currentMonitor, primaryMonitor, type BackgroundThrottlingPolicy, type Monitor } from "@tauri-apps/api/window";

export type PetMode = "sprite" | "live2d";

export const PET_LABELS = { sprite: "pet-sprite", live2d: "pet-live2d", chat: "pet-chat" } as const;
/** 标题必须和 api_server.py 热键的 FindWindowW 完全一致。 */
export const PET_TITLES = { sprite: "莲心 - 桌宠模式", live2d: "莲心 - Live2D", chat: "莲心 - 对话" } as const;
export const PET_DEFAULT_SIZES = {
  sprite: { width: 110, height: 200 },
  live2d: { width: 370, height: 660 },
  chat: { width: 360, height: 300 },
} as const;

export function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

async function mainMonitor(): Promise<Monitor | null> {
  try {
    return (await currentMonitor()) ?? (await primaryMonitor());
  } catch {
    return null;
  }
}

async function createPetWindow(
  label: string,
  title: string,
  hash: string,
  width: number,
  height: number,
  x: number,
  y: number,
  resizable = false,
): Promise<boolean> {
  const existing = await WebviewWindow.getByLabel(label);
  if (existing) {
    await existing.unminimize().catch(() => undefined);
    await existing.show().catch(() => undefined);
    return true;
  }
  const window = new WebviewWindow(label, {
    url: `index.html#${hash}`,
    title,
    width,
    height,
    x,
    y,
    minWidth: resizable ? 240 : undefined,
    minHeight: resizable ? 150 : undefined,
    transparent: true,
    decorations: false,
    shadow: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    noRedirectionBitmap: true,
    focus: false,
    resizable,
    backgroundThrottling: "disabled" as BackgroundThrottlingPolicy,
  });
  return await new Promise<boolean>((resolve) => {
    void window.once("tauri://created", () => resolve(true)).catch(() => resolve(false));
    void window.once("tauri://error", () => resolve(false)).catch(() => resolve(false));
  });
}

async function workArea(): Promise<{ left: number; top: number; right: number; bottom: number }> {
  const monitor = await mainMonitor();
  if (!monitor) return { left: 0, top: 0, right: 1920, bottom: 1080 };
  const scale = monitor.scaleFactor;
  const position = monitor.workArea.position.toLogical(scale);
  const size = monitor.workArea.size.toLogical(scale);
  return {
    left: position.x, top: position.y,
    right: position.x + size.width, bottom: position.y + size.height,
  };
}

export async function openPetMode(mode: PetMode): Promise<boolean> {
  if (!inTauri()) return false;
  const other = mode === "sprite" ? PET_LABELS.live2d : PET_LABELS.sprite;
  const otherWindow = await WebviewWindow.getByLabel(other).catch(() => null);
  if (otherWindow) await otherWindow.close().catch(() => undefined);

  const area = await workArea();
  const sprite = PET_DEFAULT_SIZES.sprite;
  const live2d = PET_DEFAULT_SIZES.live2d;
  const size = mode === "sprite" ? sprite : live2d;
  const petX = Math.max(area.left, area.right - size.width - 40);
  const petY = Math.max(area.top, area.bottom - size.height - 60);
  const opened = await createPetWindow(
    PET_LABELS[mode], PET_TITLES[mode], `pet/${mode}`, size.width, size.height, petX, petY,
  );
  if (!opened) return false;

  const chat = PET_DEFAULT_SIZES.chat;
  const chatX = Math.max(area.left, Math.min(petX - chat.width - 20, area.right - chat.width));
  const chatY = Math.max(area.top, Math.min(petY + size.height - chat.height, area.bottom - chat.height));
  await createPetWindow(PET_LABELS.chat, PET_TITLES.chat, "pet/chat", chat.width, chat.height, chatX, chatY, true);
  return true;
}

export async function toggleActivePetMode(): Promise<boolean> {
  if (!inTauri()) return false;
  const sprite = await WebviewWindow.getByLabel(PET_LABELS.sprite).catch(() => null);
  const live2d = await WebviewWindow.getByLabel(PET_LABELS.live2d).catch(() => null);
  const pet = sprite ?? live2d;
  if (!pet) return openPetMode("sprite");
  const visible = await pet.isVisible().catch(() => false);
  // 两个窗口各自监听 pet-set-visible，隐藏/显示保持同步；主窗口不需要额外窗口权限。
  await emit("pet-set-visible", { visible: !visible }).catch(() => undefined);
  return true;
}

export async function closePetWindows(): Promise<void> {
  if (!inTauri()) return;
  for (const label of Object.values(PET_LABELS)) {
    const window = await WebviewWindow.getByLabel(label).catch(() => null);
    if (window) await window.close().catch(() => undefined);
  }
}
