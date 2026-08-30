"""
莲心AI — GUI 版入口
运行方式：python main.py
"""

import sys
import os
import threading
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'
os.environ['TQDM_DISABLE'] = '1'          # 抑制 modelscope/funasr tqdm 进度条

# ── 强制 stdout/stderr UTF-8：防止 Windows GBK 环境下 emoji print() 崩溃 ──
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8')
    except Exception:
        pass

import ctypes

# ── 禁用 Windows 终端快速编辑模式（防止误触终端导致进程卡住） ──
if sys.platform == "win32":
    try:
        _kernel32 = ctypes.windll.kernel32
        _STD_INPUT_HANDLE = -10
        _ENABLE_QUICK_EDIT = 0x0040
        _handle = _kernel32.GetStdHandle(_STD_INPUT_HANDLE)
        _mode = ctypes.c_uint32()
        _kernel32.GetConsoleMode(_handle, ctypes.byref(_mode))
        # 清除快速编辑和插入模式
        _new_mode = _mode.value & ~(_ENABLE_QUICK_EDIT | 0x0020)
        if _new_mode != _mode.value:
            _kernel32.SetConsoleMode(_handle, _new_mode)
    except Exception:
        pass  # 非终端环境（如 IDE 启动）跳过
import warnings
import traceback
import faulthandler

# 启用 faulthandler：即使 C++ 级崩溃（segfault/access violation）也能打印 Python 堆栈
_FAULT_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "fault.log")
os.makedirs(os.path.dirname(_FAULT_LOG), exist_ok=True)
_fault_fd = open(_FAULT_LOG, "a", encoding="utf-8")
faulthandler.enable(file=_fault_fd, all_threads=True)

# 屏蔽 pydub/TTS 临时文件未关闭的 ResourceWarning 刷屏
warnings.simplefilter("ignore", ResourceWarning)

# ── 第7条：工作目录修正 ────────────────────────────────────────
# 通过注册表开机自启时，Windows 默认将 CWD 设为 C:\Windows\System32。
# 在任何 import 之前强制切换到项目根目录，保证相对路径行为一致。
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(_PROJECT_ROOT)

# 启动时轮转日志（超过 5MB 备份，避免日积月累；crash.log 不强制清空以保留崩溃记录）
_LOG_DIR = os.path.join(_PROJECT_ROOT, "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
for _p in ("debug.log",):
    try:
        _fp = os.path.join(_LOG_DIR, _p)
        if os.path.exists(_fp) and os.path.getsize(_fp) > 5 * 1024 * 1024:
            _backup = _fp + ".old"
            if os.path.exists(_backup):
                os.remove(_backup)
            os.rename(_fp, _backup)
        open(_fp, "w").close()
    except Exception:
        pass
# crash.log 不清空，保留上次崩溃记录供诊断
_crash_path = os.path.join(_LOG_DIR, "crash.log")
if os.path.exists(_crash_path):
    try:
        if os.path.getsize(_crash_path) > 5 * 1024 * 1024:
            _backup = _crash_path + ".old"
            if os.path.exists(_backup):
                os.remove(_backup)
            os.rename(_crash_path, _backup)
    except Exception:
        pass

# ── 终端防卡：双通道输出（终端 + 日志文件）─────────────────────
# Windows 终端"快速编辑模式"（点击选中文本）会锁定 stdout 缓冲区，
# 导致 print() 调用阻塞。解决：终端写入走独立线程，日志文件实时落盘。
# 终端可正常查看 print，选中复制不影响程序运行。
if sys.platform == "win32":
    import threading
    import queue as _queue_mod

    _LOG_PATH = os.path.join(_LOG_DIR, "debug.log")

    _REAL_STDOUT = sys.stdout
    _REAL_STDERR = sys.stderr

    class _TeeWriter:
        """同时写入日志文件和终端；终端写入走独立线程，避免 Quick Edit 阻塞。"""
        def __init__(self, log_path, real_stream):
            self._log_path = log_path
            self._log = self._open_log()
            self._real = real_stream
            self._queue = _queue_mod.Queue()
            self._worker = threading.Thread(target=self._drain, daemon=True)
            self._worker._tee_writer_alive = True
            self._worker.start()

        def _open_log(self):
            try:
                # 启动时轮转日志：超过 5MB 就备份
                if os.path.exists(self._log_path):
                    try:
                        if os.path.getsize(self._log_path) > 5 * 1024 * 1024:
                            _backup = self._log_path + ".old"
                            if os.path.exists(_backup):
                                os.remove(_backup)
                            os.rename(self._log_path, _backup)
                    except OSError:
                        pass
                return open(self._log_path, "a", encoding="utf-8", buffering=1)
            except Exception:
                return None

        @staticmethod
        def _to_str(text):
            """字节安全归一化：bytes -> str，避免 '\n' in bytes 崩溃。"""
            if isinstance(text, bytes):
                try:
                    return text.decode("utf-8", errors="replace")
                except Exception:
                    return text.decode("latin-1", errors="replace")
            return text

        def _drain(self):
            while True:
                text = self._queue.get()
                if text is None:
                    break
                if not isinstance(text, str):
                    text = self._to_str(text)
                try:
                    try:
                        self._real.write(text)
                    except UnicodeEncodeError:
                        self._real.write(text.encode("gbk", errors="replace").decode("gbk"))
                except Exception:
                    pass
                # 批量 flush：每 20 条或遇到换行时才刷终端，大幅减少终端 I/O 阻塞
                try:
                    if text and "\n" in text:
                        self._real.flush()
                except Exception:
                    pass

        def write(self, text):
            if not isinstance(text, str):
                text = self._to_str(text)
            if self._log is not None:
                try:
                    self._log.write(text)
                    # 仅在文本不含换行符时才手动 flush（line-buffered 模式已自动处理换行）
                    if "\n" not in text:
                        self._log.flush()
                except Exception:
                    try:
                        self._log.close()
                    except Exception:
                        pass
                    self._log = self._open_log()
            try:
                self._queue.put_nowait(text)
            except _queue_mod.Full:
                pass

        def flush(self):
            if self._log is not None:
                try:
                    self._log.flush()
                except Exception:
                    pass

    sys.stdout = _TeeWriter(_LOG_PATH, _REAL_STDOUT)
    sys.stderr = _TeeWriter(_LOG_PATH, _REAL_STDERR)

# 确保项目根目录在路径中
sys.path.insert(0, _PROJECT_ROOT)

# 禁用 Anthropic SDK 内部 OpenTelemetry 追踪，避免 protobuf UTF-8 序列化报错
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

# 导入迁移函数（必须在切换工作目录后）
from utils.paths import migrate_legacy_files   # 新增

# 执行数据迁移（仅首次运行会移动旧文件）
migrate_legacy_files()

from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtGui import QFont
from PyQt5.QtCore import Qt, QTimer, QObject, pyqtSignal
import qdarkstyle

from gui.main_window import MainWindow
from utils.torch_runtime import TorchInitRequest, register_main_thread_initializer


def _apply_saved_provider_on_startup() -> None:
    """启动时读取用户配置中的 provider，并把它作为当前会话的默认 LLM 路径。"""
    try:
        from config import get_api_config
        cfg = get_api_config()
        provider = str(cfg.get("provider", "deepseek") or "deepseek").strip()
        if provider not in {"deepseek", "agnes", "local"}:
            provider = "deepseek"
        print(f"[启动] 当前 LLM provider: {provider}", flush=True)
    except Exception as exc:
        print(f"[启动] 读取 LLM provider 配置失败: {exc}", flush=True)


# ── 第5条：跨平台多实例保护 ───────────────────────────────────
from utils.platform_capabilities import SingleInstanceGuard

_INSTANCE_GUARD = SingleInstanceGuard()


def _acquire_single_instance_mutex() -> bool:
    """
    尝试创建命名互斥量。
    返回 True 表示当前进程获得唯一运行权；
    返回 False 表示已有另一个实例在运行。
    """
    return _INSTANCE_GUARD.acquire()


def _global_exception_handler(exc_type, exc_value, exc_tb):
    """全局未处理异常捕获：记录到日志文件后优雅退出。"""
    tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    try:
        crash_path = os.path.join(_LOG_DIR, "crash.log")
        with open(crash_path, "a", encoding="utf-8") as f:
            from datetime import datetime
            f.write(f"\n{'='*60}\n")
            f.write(f"崩溃时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"异常类型: {exc_type.__name__}\n")
            f.write(f"异常信息: {exc_value}\n")
            f.write(f"堆栈跟踪:\n{tb_text}\n")
        print(f"\n[致命错误] {exc_type.__name__}: {exc_value}", file=sys.stderr)
        print(f"详细堆栈已写入: {crash_path}", file=sys.stderr)
    except Exception:
        print(f"\n[致命错误] {exc_type.__name__}: {exc_value}", file=sys.stderr)
        print(tb_text, file=sys.stderr)

sys.excepthook = _global_exception_handler

# 捕获后台线程中未处理的异常（Python 3.8+）
_threading_excepthook = threading.excepthook if hasattr(threading, "excepthook") else None


def _thread_exception_handler(args):
    _global_exception_handler(args.exc_type, args.exc_value, args.exc_traceback)


if _threading_excepthook is not None:
    threading.excepthook = _thread_exception_handler


def _show_check_dialog(parent, report: str):
    """非模态显示启动体检报告，不阻塞主窗口。"""
    msg_box = QMessageBox(parent)
    msg_box.setWindowTitle("莲心AI - 启动体检")
    msg_box.setText(report)
    msg_box.setIcon(QMessageBox.Information)
    msg_box.setStandardButtons(QMessageBox.Ok)
    msg_box.setModal(False)
    msg_box.show()


class _TorchRuntimeBridge(QObject):
    """Run deferred Torch initialization on the Qt main thread."""

    initialize_requested = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.initialize_requested.connect(self._initialize)

    def _initialize(self, request: TorchInitRequest):
        from utils.torch_runtime import _initialize_now
        _initialize_now(request)


def main():
    autostart_mode = "--autostart" in sys.argv

    # QWebEngineView is loaded lazily by the Canvas memory constellation.
    # Qt requires shared OpenGL contexts to be enabled before *any* QApplication
    # is constructed, otherwise opening the star-map window raises an import
    # error and terminates the process.
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)

    # ── 第5条：单实例检测（在创建 QApplication 之前执行）────────
    if not _acquire_single_instance_mutex():
        if not autostart_mode:
            _app = QApplication(sys.argv)
            QMessageBox.information(
                None, "莲心AI",
                "莲心已经在运行了哦，请在任务栏找到她~"
            )
        sys.exit(0)

    # 高 DPI 支持（Windows 缩放适配）
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("莲心AI")
    app.setStyleSheet(qdarkstyle.load_stylesheet_pyqt5())

    # ── 全局异常处理：确保 Qt 事件循环中的异常也被捕获 ──
    def _qt_exception_handler(exc_type, exc_value, tb_obj):
        _global_exception_handler(exc_type, exc_value, tb_obj)
        app.quit()
    sys.excepthook = _qt_exception_handler

    _apply_saved_provider_on_startup()

    # 全局字体
    font = QFont("Microsoft YaHei UI", 10)
    app.setFont(font)

    # Register the bridge before any worker can request a local model.  Torch
    # is now initialized only when RAG/FunASR is actually used.
    torch_bridge = _TorchRuntimeBridge()
    register_main_thread_initializer(torch_bridge.initialize_requested.emit)

    # ── 启动健康检查（先跑完、存结果，窗口就绪后非模态显示）─────
    _check_report = None
    if "--skip-check" not in sys.argv:
        try:
            from utils.settings import get_settings as _get_gs
            if _get_gs().startup_check_enabled:
                from utils.startup_check import run_checks, has_warnings, format_report
                print("[启动体检] 正在检测关键依赖…", flush=True)
                results = run_checks()
                if has_warnings(results):
                    _check_report = format_report(results)
                    print(_check_report, flush=True)
                else:
                    print("  全部通过", flush=True)
            else:
                print("[启动体检] 已跳过（设置中关闭）", flush=True)
        except Exception as e:
            print(f"[启动体检] 检测过程异常: {e}", flush=True)

    window = MainWindow(autostart_mode=autostart_mode)

    # ── 自动激活标记为 auto_activate 的技能 ────────────────────
    from brain.skill_manager import activate_all_skills
    activate_all_skills()

    # 虚拟世界网页服务和技能工具必须处于同一进程，才能共享权威 WorldState。
    try:
        from brain.physical.service import start_physical_sim_server, stop_physical_sim_server
        _physical_server = start_physical_sim_server()
        if _physical_server.error:
            print(f"[PhysicalSim] 调试服务启动失败: {_physical_server.error}", flush=True)
        else:
            app.aboutToQuit.connect(stop_physical_sim_server)
            print("[PhysicalSim] 调试服务已启动: http://127.0.0.1:8766/", flush=True)
    except Exception as exc:
        print(f"[PhysicalSim] 调试服务初始化失败: {exc}", flush=True)

    # ── 初始化 MCP 系统 ──────────────────────────────────
    try:
        from brain.mcp.mcp_manager import get_mcp_manager
        _mcp_mgr = get_mcp_manager()
        _mcp_mgr.initialize()
        import atexit
        atexit.register(_mcp_mgr.shutdown)
    except Exception as e:
        print(f"[MCP] 初始化失败，MCP 功能已禁用: {e}")


    # ── QQ 桥接（由 MainWindow 管理，详见 main_window.py）─────

    # ── 第6条：自启动时最小化，不打扰用户 ────────────────────────
    if autostart_mode:
        window.showMinimized()
    else:
        window.show()

    # ── Torch 预热：把懒加载的首卡顿挪到启动窗口期 ───────────────
    # Windows 上 torch 必须在主线程导入（utils/torch_runtime 的约束），
    # 与其让语音/记忆检索首次触发时冻结对话 7 秒，不如在窗口显示后
    # 立刻预热一次；此后 sys.modules 已缓存，本轮不会再卡。
    def _preload_torch_runtime():
        try:
            from utils.torch_runtime import ensure_ready
            import time as _time
            _started = _time.monotonic()
            ensure_ready(timeout=120.0)
            print(f"[预载] Torch 运行时就绪（{_time.monotonic() - _started:.1f}s）", flush=True)
        except Exception as exc:
            print(f"[预载] Torch 预热失败，将在首次使用时重试: {exc}", flush=True)

    QTimer.singleShot(600, _preload_torch_runtime)

    # ── 非模态体检报告（事件循环启动后出现，不阻塞窗口）────────
    if _check_report and not autostart_mode:
        QTimer.singleShot(300, lambda r=_check_report: _show_check_dialog(window, r))

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
