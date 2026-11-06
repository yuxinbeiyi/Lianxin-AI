import { confirm } from "@tauri-apps/plugin-dialog";

export async function confirmConfigAction(message: string, title = "莲心 · 设置") {
  if (!("__TAURI_INTERNALS__" in window)) return Boolean(await window.confirm(message));
  try {
    return await confirm(message, { title, kind: "warning", okLabel: "确认", cancelLabel: "取消" });
  } catch {
    throw new Error("桌面确认权限不可用，请完全退出并重启莲心；操作未执行");
  }
}
