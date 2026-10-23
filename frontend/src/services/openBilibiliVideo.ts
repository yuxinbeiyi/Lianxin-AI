import { invoke, isTauri } from "@tauri-apps/api/core";
import { ApiRequestError, lianxinApi } from "./lianxinApi";

export async function openBilibiliVideo(bvid: string): Promise<void> {
  if (!/^BV[A-Za-z0-9]{10}$/.test(bvid)) {
    throw new Error("视频编号无效，没有打开浏览器。");
  }
  const url = `https://www.bilibili.com/video/${bvid}`;
  if (isTauri()) {
    try {
      await invoke("plugin:shell|open", { path: url, with: null });
      return;
    } catch {}
  }

  let result: { ok: boolean; error?: string };
  try {
    result = await lianxinApi.openBilibiliVideo(bvid);
  } catch (error) {
    if (error instanceof ApiRequestError && error.status === 404) {
      throw new Error("莲心后台仍在运行旧版本，未加载打开视频接口。请退出莲心后重新启动，再点击这条消息即可，不需要重新冲浪。");
    }
    if (error instanceof ApiRequestError) {
      throw new Error(`打开视频接口返回错误（HTTP ${error.status}），可以复制视频链接后手动打开。`);
    }
    throw new Error("未能连接打开视频服务，请检查莲心后台是否正常运行；也可以复制视频链接后手动打开。");
  }
  if (!result.ok) {
    throw new Error(result.error || "系统浏览器未能打开，可以复制视频链接后手动打开。");
  }
}
