"""Isolated startup model loader.

This process deliberately owns native Torch/FunASR imports.  A crash here
cannot take down the Qt process.  The current phase is a safety preflight;
the following IPC phase will keep the loaded model alive for transcription.
"""

from __future__ import annotations

import os
import sys
import base64
import json
import socket
import threading


def report(message: str) -> None:
    print(message, flush=True)


def main() -> int:
    try:
        report("TORCH_START")
        import torch  # noqa: F401
        report("TORCH_READY")
        report("FUNASR_START")
        from brain.stt_funasr import _load_model
        model = _load_model()
        if model is None:
            report("FUNASR_FAILED: model returned None")
            return 2
        report("FUNASR_READY")
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(4)
        report(f"SERVICE_READY:{server.getsockname()[1]}")

        def handle(conn):
            try:
                payload = b""
                while not payload.endswith(b"\n"):
                    block = conn.recv(1024 * 1024)
                    if not block:
                        return
                    payload += block
                request = json.loads(payload.decode("utf-8"))
                wav = base64.b64decode(request["wav"])
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp.write(wav)
                    path = tmp.name
                try:
                    result = model.generate(input=path, language=request.get("language", "zh"), use_itn=True, ban_emo_unk=True)
                    text = result[0].get("text", "").strip() if result else ""
                finally:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
                conn.sendall((json.dumps({"ok": True, "text": text}, ensure_ascii=False) + "\n").encode("utf-8"))
            except Exception as exc:
                conn.sendall((json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False) + "\n").encode("utf-8"))
            finally:
                conn.close()

        while True:
            conn, _ = server.accept()
            threading.Thread(target=handle, args=(conn,), daemon=True).start()
    except KeyboardInterrupt:
        return 0
    except BaseException as exc:
        report(f"MODEL_FAILED: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
