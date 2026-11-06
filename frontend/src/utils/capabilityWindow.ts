import { emitTo } from "@tauri-apps/api/event";
import { WebviewWindow } from "@tauri-apps/api/webviewWindow";
import { confirm } from "@tauri-apps/plugin-dialog";

export const CAPABILITY_TOOL_EVENT = "lianxin-capability-tool";

export function applyPreferredTool(name: string) {
  if (typeof name !== "string" || !name.trim() || name.length > 256) throw new Error("工具名称无效");
  const selection = { preferredTool: name };
  window.sessionStorage.setItem("lianxin-tool-selection", JSON.stringify(selection));
  window.dispatchEvent(new CustomEvent("tool-selection", { detail: selection }));
}

export async function useCapabilityInMain(name: string) {
  if (typeof name !== "string" || !name.trim() || name.length > 256) throw new Error("工具名称无效");
  if (!("__TAURI_INTERNALS__" in window)) { applyPreferredTool(name); return; }
  const main = await WebviewWindow.getByLabel("main");
  if (!main) throw new Error("找不到主聊天窗口，未提交工具建议");
  await main.unminimize();
  await main.show();
  await main.setFocus();
  await emitTo("main", CAPABILITY_TOOL_EVENT, { name });
}

export async function confirmCapabilityAction(message: string) {
  if (!("__TAURI_INTERNALS__" in window)) return Boolean(await window.confirm(message));
  try {
    return await confirm(message, { title: "莲心 · 能力中枢", kind: "warning", okLabel: "确认", cancelLabel: "取消" });
  } catch {
    throw new Error("桌面确认框不可用，请完全退出并重启莲心以加载权限；本次操作未执行");
  }
}
