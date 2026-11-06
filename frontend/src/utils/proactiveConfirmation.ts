import { confirm } from "@tauri-apps/plugin-dialog";

export async function confirmProactiveAction(message: string): Promise<boolean> {
  if (!("__TAURI_INTERNALS__" in window)) return Boolean(await window.confirm(message));
  try {
    return await confirm(message, { title: "莲心 · 主动聊天", kind: "warning", okLabel: "确认", cancelLabel: "取消" });
  } catch {
    throw new Error("无法打开桌面确认框，请完全退出并重启莲心以加载窗口权限；本次操作未执行");
  }
}
