# 莲心新版界面原型

这是 React + TypeScript + Vite + Tauri 2 的第一阶段 UI 原型。

当前版本使用 fixture 数据，目标是验证 `莲心预览图2.png` 的主界面布局：

- 左侧一级空间导航
- 中央对话区
- 右侧伴随状态栏
- 底部统一输入区
- 管理中心分组菜单

## 浏览器预览

```powershell
npm install
npm run dev
```

## 构建前端

```powershell
npm run build
```

## Tauri

机器安装 Rust/Cargo 后执行：

```powershell
npm run tauri dev
```

当前阶段不接 Python 后端；后续通过 HTTP JSON、WebSocket 或 SSE 接入 AgentCore、音乐、任务和主动消息事件。
# 莲心新版界面

## 启动真实 Python 核心

在项目根目录启动兼容 API：

```powershell
python api_server.py
```

默认监听 `http://127.0.0.1:8766`。它复用现有 `AgentCore`、`HistoryManager`、备忘本和任务存储，不会替换旧版 PyQt5 主界面。

再启动 Tauri 界面：

```powershell
cd frontend
npm run tauri dev
```

未启动 API 时，界面会进入离线模式并保留聊天原型回退；启动后会自动轮询连接状态、当前会话和网易云播放状态。
