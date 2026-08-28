# -*- coding: utf-8 -*-
"""ESP32-CAM 局域网中继服务（替代云端中继，供同网段本地使用）。

用法:
    python tools/esp32_relay_server.py              # 默认 0.0.0.0:8765
    python tools/esp32_relay_server.py --port 9000

配合莲心（同局域网三步）:
    1. 在这台电脑上运行本脚本（作为“服务器”）。
    2. 修改 config.py 里 camera.shoulder_relay_url 为 ws://127.0.0.1:8765
       （或 user_config.json 的 camera.shoulder_relay_url）。
    3. 把 ESP32 固件的中继 URL 改成 ws://<本机局域网IP>:8765，
       并让 ESP32 连接到家里的同一个 WiFi。
    （ESP32 与莲心不需要改协议，只改 URL 即可。）

协议与云端中继一致:
    - 客户端连接后第一条消息声明角色: "PC" 或 "ESP32"
    - 双方配对后，双向原样转发（文本命令 / 二进制 JPEG 帧）
    - 任一方断开，另一方会被关闭，等待重新配对
"""

from __future__ import annotations

import argparse
import asyncio
import socket

import websockets


class Esp32LocalRelay:
    """极简配对中继：一个 PC 客户端 + 一个 ESP32 客户端，双向转发。"""

    def __init__(self) -> None:
        self.pc = None
        self.esp32 = None

    def _peer_of(self, ws) -> "object | None":
        # 显式按身份判断，避免被新连接覆盖后的旧连接误转发
        if self.pc is not None and ws is self.pc:
            return self.esp32
        if self.esp32 is not None and ws is self.esp32:
            return self.pc
        return None

    async def handler(self, ws) -> None:
        try:
            first = await asyncio.wait_for(ws.recv(), timeout=15)
        except Exception:
            return
        text = first.decode("utf-8", "ignore") if isinstance(first, bytes) else str(first)
        role = text.strip().lower()

        # 欢迎消息带 WELCOME: 前缀，ESP32 固件会跳过这类文本。
        # 同角色重复连接直接覆盖（处理 ESP32 断线重连 / PC 逐命令新建连接），
        # 不关闭旧对端，保证 PC 断开后 ESP32 仍驻留，下次命令秒配对。
        if role == "pc":
            self.pc = ws
            await ws.send("WELCOME:LAN-RELAY")
        elif role in ("esp32", "esp"):
            self.esp32 = ws
            await ws.send("WELCOME:LAN-RELAY")
        else:
            await ws.send("WELCOME:unknown role: " + text[:64])
            return

        try:
            async for message in ws:
                peer = self._peer_of(ws)
                if peer is None:
                    continue
                try:
                    await peer.send(message)
                except websockets.exceptions.ConnectionClosed:
                    break
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            # 只清空自己的槽位，不主动关闭对端（对端可驻留等待下次配对）
            if self.pc is ws:
                self.pc = None
            elif self.esp32 is ws:
                self.esp32 = None


def _lan_ips() -> list[str]:
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            addr = info[4][0]
            if addr and not addr.startswith("127."):
                ips.add(addr)
    except Exception:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        ips.add(probe.getsockname()[0])
        probe.close()
    except Exception:
        pass
    return sorted(ips)


async def main(host: str, port: int) -> None:
    relay = Esp32LocalRelay()
    print("=" * 60)
    print("ESP32-CAM 局域网中继已启动")
    print(f"  监听地址 : {host}:{port}")
    print(f"  莲心电脑端: ws://127.0.0.1:{port}   (config 里 shoulder_relay_url)")
    for ip in _lan_ips():
        print(f"  ESP32端  : ws://{ip}:{port}   (ESP32 固件中继 URL)")
    print("  配对方式 : 两端各自连接后自动配对，顺序不限")
    print("  退出     : Ctrl+C")
    print("=" * 60)
    async with websockets.serve(relay.handler, host, port):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ESP32-CAM 局域网中继")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.host, args.port))
    except KeyboardInterrupt:
        print("\n已退出")