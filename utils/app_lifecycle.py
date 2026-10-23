"""Bounded shutdown of the desktop backend and its owned child processes."""

import os
import threading

import psutil

_SHUTDOWN_LOCK = threading.Lock()
_SHUTDOWN_STARTED = False
_OWNED_PROCESSES = []


def stop_owned_processes():
    try:
        current = psutil.Process().children(recursive=True)
    except psutil.Error:
        current = []
    children = list({process: process for process in [*_OWNED_PROCESSES, *current]}.values())
    for process in reversed(children):
        try:
            process.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(children, timeout=3)
    for process in alive:
        try:
            process.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(alive, timeout=1)


def request_shutdown(server, timeout=15):
    global _SHUTDOWN_STARTED, _OWNED_PROCESSES
    with _SHUTDOWN_LOCK:
        if _SHUTDOWN_STARTED:
            return
        _SHUTDOWN_STARTED = True
        try:
            _OWNED_PROCESSES = psutil.Process().children(recursive=True)
        except psutil.Error:
            _OWNED_PROCESSES = []

    def shutdown():
        server.shutdown()

    def force_exit():
        stop_owned_processes()
        os._exit(0)

    watchdog = threading.Timer(timeout, force_exit)
    watchdog.daemon = True
    watchdog.start()
    threading.Thread(target=shutdown, name="lianxin-http-shutdown", daemon=True).start()
