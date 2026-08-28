"""肩载设备局域网联调诊断脚本（在家运行）。

用法:
    python tools/esp32_relay_diag.py             # 只检查本地中继是否在跑
    python tools/esp32_relay_diag.py 192.168.43.123   # 传入 ESP32 的 IP 一并检查

依次检查:
    1) 本地中继 127.0.0.1:8765 是否在运行
    2) ESP32 是否可达 (TCP 80)
    3) ESP32 HTTP 首页 / 是否返回 200
    4) ESP32 视频流 /stream 是否在推流
"""

from __future__ import annotations

import os
import socket
import sys
import urllib.request

if sys.platform.startswith("win"):
    # 把控制台切到 UTF-8，避免中文提示乱码（对用户终端无副作用）
    os.system("chcp 65001 >nul")


def check(name: str, ok: bool, hint: str = "") -> None:
    mark = "OK  " if ok else "FAIL"
    line = f"[{mark}] {name}"
    if hint and not ok:
        line += f"   -> {hint}"
    print(line)


def tcp_ok(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def http_get(host: str, port: int, path: str, timeout: float = 4.0):
    try:
        with urllib.request.urlopen(f"http://{host}:{port}{path}", timeout=timeout) as resp:
            return resp.status, None
    except Exception as exc:
        return None, str(exc)


def main() -> None:
    relay_up = tcp_ok("127.0.0.1", 8765)
    check("本地中继 127.0.0.1:8765 是否在跑", relay_up,
          "用你平时跑莲心的 python 运行: python tools/esp32_relay_server.py")

    if len(sys.argv) > 1:
        ip = sys.argv[1]
        print()
        reachable = tcp_ok(ip, 80)
        check(f"ESP32 可达 {ip}:80", reachable,
              "确认电脑和 ESP32 连同一个热点/网段，IP 是否填对")
        if reachable:
            st, err = http_get(ip, 80, "/")
            check(f"ESP32 首页 /  (HTTP {st})", st == 200,
                  f"浏览器打开 http://{ip}/ 看看是否能出画面")
            st2, err2 = http_get(ip, 80, "/stream")
            check(f"ESP32 视频流 /stream (HTTP {st2})", st2 == 200,
                  f"推流异常: {err2}；确认相机 OK、没有别的程序占用")
    else:
        print("\n提示: 传入 ESP32 的 IP 可进一步检查设备，例如:")
        print("    python tools/esp32_relay_diag.py 192.168.43.123")

    print("\n联调提示: 浏览器打开 http://<esp32_ip>/relay")
    print("  host = 电脑局域网IP（中继脚本打印的那个）, port = 8765, ssl = ws://(局域网)")


if __name__ == "__main__":
    main()
