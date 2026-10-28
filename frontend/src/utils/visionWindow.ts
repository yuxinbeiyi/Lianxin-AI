import { WebviewWindow } from "@tauri-apps/api/webviewWindow";

function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/** 在独立系统窗口里打开视觉理解；非 Tauri 环境返回 false，调用方回退到内嵌面板。 */
export async function openVisionWindow(): Promise<boolean> {
  if (!inTauri()) return false;
  try {
    const existing = await WebviewWindow.getByLabel("vision");
    if (existing) {
      await existing.unminimize().catch(() => undefined);
      await existing.show().catch(() => undefined);
      await existing.setFocus().catch(() => undefined);
      return true;
    }
    const opened = new WebviewWindow("vision", {
      url: "index.html#vision",
      title: "莲心 · 视觉理解",
      width: 1180,
      height: 780,
      minWidth: 1000,
      minHeight: 560,
      resizable: true,
      decorations: false,
      center: true,
    });
    return await new Promise<boolean>((resolve) => {
      const timer = window.setTimeout(() => resolve(true), 2000);
      opened.once("tauri://created", () => {
        window.clearTimeout(timer);
        resolve(true);
      });
      opened.once("tauri://error", () => {
        window.clearTimeout(timer);
        resolve(false);
      });
    });
  } catch (error) {
    console.warn("无法打开视觉理解窗口，回退到内嵌面板", error);
    return false;
  }
}
