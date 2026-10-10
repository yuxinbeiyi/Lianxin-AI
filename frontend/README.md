# 莲心新版界面

这是莲心 AI 的新版桌面界面，技术栈为 **React + TypeScript + Vite + Tauri 2**，源码位于本目录（`frontend/`）。

界面布局对应 `莲心预览图2.png`：

- 左侧一级空间导航
- 中央对话区
- 右侧伴随状态栏
- 底部统一输入区
- 管理中心分组菜单

## 一键启动（推荐）

在项目根目录运行：

```powershell
.\run.bat
```

`run.bat` 会检查 Python / Node.js / cargo，首次运行自动执行 `npm install`，然后拉起后端 `api_server.py` 并打开新版界面窗口。它不写死任何路径，可随项目目录一起移动。

## 手动启动

先在项目根目录启动后端接口：

```powershell
python api_server.py
```

默认监听 `http://127.0.0.1:8766`。它复用现有 `AgentCore`、`HistoryManager`、备忘本和任务存储，不替换旧版 PyQt5 主界面。

再在本目录启动界面：

```powershell
npm install        # 仅首次需要
npm run tauri dev
```

未启动后端时，界面会进入离线模式并保留聊天原型回退；启动后会自动轮询连接状态、当前会话和网易云播放状态。

## 其他命令

```powershell
npm run dev        # 只在浏览器里预览界面（无 Tauri 原生窗口）
npm run build      # 构建前端静态产物
```

## 环境要求

- Node.js 18+
- Rust 工具链（rustup + cargo），Tauri 2 需要它编译原生窗口
- WebView2 Runtime（Windows 10/11 通常自带）
