# utils/child_kill_job.py
"""Windows Job Object — 让随莲心启动的子进程在莲心退出时被系统自动清理。

把需要随主进程共生的子进程（例如网易云 MCP 的 node 服务、以及它派生的分离进程
mpv）加入一个带 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE 的 Job Object。主进程无论
以何种方式退出（正常关闭 / 被强杀 / 崩溃），Job 句柄都会随之关闭，Windows 会
自动结束 Job 内所有进程，从而杜绝 detached 孤儿进程（如 mpv）残留。

仅托管显式加入的子进程，不会影响莲心为用户拉起的浏览器/资源管理器等外部程序。
"""

import ctypes
import sys

# Job Object 相关常量
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JobObjectExtendedLimitInformation = 9

# 打开子进程所需的访问权限
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001

# 全局持有 Job 句柄，防止被 GC；主进程存活期间必须一直持有。
_job_handle = None


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _get_kill_job():
    """惰性创建并缓存 kill-on-close Job Object（失败返回 None）。"""
    global _job_handle
    if _job_handle is not None:
        return _job_handle
    if sys.platform != "win32":
        return None
    try:
        kernel32 = ctypes.windll.kernel32
        h_job = kernel32.CreateJobObjectW(None, None)
        if not h_job:
            return None
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            h_job,
            _JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            kernel32.CloseHandle(h_job)
            return None
        _job_handle = h_job
        return _job_handle
    except Exception:
        return None


def assign_pid_to_kill_job(pid: int) -> bool:
    """把指定 PID 的子进程加入 kill-on-close Job（失败静默，不影响主流程）。"""
    h_job = _get_kill_job()
    if not h_job:
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        h_proc = kernel32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, int(pid)
        )
        if not h_proc:
            return False
        try:
            ok = kernel32.AssignProcessToJobObject(h_job, h_proc)
            return bool(ok)
        finally:
            kernel32.CloseHandle(h_proc)
    except Exception:
        return False