"""Lianxin UI compatibility API.

This is an additive bridge for the React/Tauri interface.  It deliberately
reuses the existing AgentCore, HistoryManager, note manager and task store;
the legacy PyQt application remains unchanged and can continue to use them.
Run with: ``python api_server.py``.
"""

from __future__ import annotations

import json
import atexit
import hashlib
import base64
import mimetypes
import random
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from config import get_tts_config, save_tts_config


_IMAGE_FLOW_LOG = Path(__file__).resolve().parent / "logs" / "image_flow_diagnostic.log"
_IMAGE_FLOW_LOG_LOCK = threading.Lock()


_IMAGE_FLOW_LOG_READY = False
_IMAGE_FLOW_POLL_TICK = 0


def _image_flow_log(message: str) -> None:
    """Write bounded image-flow diagnostics without recording image bytes or full text."""
    global _IMAGE_FLOW_LOG_READY
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [IMAGE_FLOW] {message}\n"
    try:
        _IMAGE_FLOW_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _IMAGE_FLOW_LOG_LOCK:
            if not _IMAGE_FLOW_LOG_READY:
                # 与 debug.log 一致：每次启动轮转并只保留最近几份，
                # 避免这个文件无界增长（曾累积到 22MB / 22 万行）。
                from utils.bridge_log import rotate_log
                rotate_log(_IMAGE_FLOW_LOG, keep=5)
                _IMAGE_FLOW_LOG_READY = True
            with _IMAGE_FLOW_LOG.open("a", encoding="utf-8") as handle:
                handle.write(line)
    except OSError:
        pass


from brain.observation import OBSERVATION_TOOLS  # noqa: E402  (观察工具白名单，单一来源)


def _latest_assistant_message_id(agent) -> int:
    """当前会话里最新一条 assistant 消息的 id（用于判断本轮是否真的产生了回复）。"""
    try:
        history = agent.get_history_manager()
        session_id = getattr(agent, "_session_id", None)
        if history is None or session_id is None:
            return 0
        return int(history.latest_message_id(session_id, "assistant"))
    except Exception:
        return 0


def _attach_observation_to_reply(agent, observation, assistant_id_before: int) -> None:
    """把"莲心这次看到的画面"挂到她自己那条回复消息上。

    新版界面把 assistant 消息的附件渲染成莲心侧图片气泡；挂在回复上
    （而不是另存一条"莲心刚才观察到：…"旁白），就实现了
    "她的话 + 她看到的图"合成同一条消息，历史里也不再有多余的机械旁白。
    """
    if not observation or not observation.get("path"):
        return
    try:
        history = agent.get_history_manager()
        session_id = getattr(agent, "_session_id", None)
        if history is None or session_id is None:
            return
        message_id = int(history.latest_message_id(session_id, "assistant"))
        if message_id <= int(assistant_id_before or 0):
            _image_flow_log(
                f"observe_attach_skipped session={session_id} reason=no_new_assistant_message"
            )
            return
        from pathlib import Path as _Path

        from brain.observation import adopt_observation_image
        stored = adopt_observation_image(str(observation.get("path")))
        if not stored:
            _image_flow_log(f"observe_attach_skipped session={session_id} reason=adopt_failed")
            return
        history.attach_metadata_to_message(message_id, {"attachments": [{
            "kind": "image",
            "path": stored,
            "fileName": _Path(stored).name,
            "description": str(observation.get("desc") or ""),
        }]})
        _image_flow_log(
            f"observe_attached session={session_id} message={message_id} path={stored}"
        )
    except Exception as exc:
        _image_flow_log(f"observe_attach_failed error={exc}")


def _clean_vision_description(text: str) -> str:
    """把视觉模型返回的原始描述整理成中性素材。

    去 Markdown 排版（避免模型模仿其格式）、去客套开头（"以下是…详细描述"）、
    折叠多余空白。按用户要求不设长度上限，只做去噪。
    """
    import re
    value = str(text or "").replace("\r\n", "\n")
    if not value.strip():
        return ""
    value = re.sub(r"```[\s\S]*?```", "", value)
    value = re.sub(r"^\s*#{1,6}\s*", "", value, flags=re.MULTILINE)
    value = re.sub(r"^\s*[-*+]\s+", "", value, flags=re.MULTILINE)
    value = re.sub(r"^\s*\d+[.、)]\s+", "", value, flags=re.MULTILINE)
    value = value.replace("**", "").replace("__", "")
    value = re.sub(r"(?<!\w)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\w)", r"\1", value)
    value = value.replace("`", "")
    # 去掉独立的客套/说明行（如"以下是关于图片内容的详细描述："）
    value = re.sub(
        r"(?m)^[ \t]*(?:以下是[^\n]{0,40}?描述[：:]?|图片描述[：:]?|"
        r"这张图片[^\n]{0,20}[：:]|下面[^\n]{0,20}描述[：:]?)[ \t]*$",
        "",
        value,
    )
    # 去掉开头多余的"好的，"
    value = re.sub(r"^[ \t]*好的[，,]?[ \t]*", "", value.lstrip())
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _image_flow_log_poll(message: str) -> None:
    """空轮询降噪：默认每 60 次才记录一条，避免前端轮询把日志刷爆。"""
    global _IMAGE_FLOW_POLL_TICK
    _IMAGE_FLOW_POLL_TICK += 1
    if _IMAGE_FLOW_POLL_TICK % 60 == 1:
        _image_flow_log(f"{message} [sampled 1/60]")


LEGACY_FEATURES = {
    "ripple", "persona", "memory-constellation", "prism-memory",
    "history", "note", "workflow", "duty", "proactive", "alarm", "reminder",
    "settings", "api", "network", "capability", "vision", "voice-stt", "sound",
    "qq", "wechat", "study-room", "time-capsule", "data-tide", "camera", "galgame",
    "video-call",
}
WEBENGINE_FEATURES = {"ripple", "memory-constellation", "study-room", "time-capsule", "data-tide"}
_CONSTELLATION_BRIDGE_SHIM = """<script>
(function () {
  var base = '/api/memory-constellation';
  function showMessages(text) {
    var old = document.getElementById('lianxin-constellation-modal');
    if (old) old.remove();
    var box = document.createElement('div');
    box.id = 'lianxin-constellation-modal';
    box.style.cssText = 'position:fixed;top:20px;left:50%;transform:translateX(-50%);z-index:99999;width:min(720px,92vw);max-height:72vh;overflow:auto;padding:18px 20px;background:#0a1220;color:#cfe3e0;border:1px solid rgba(157,214,205,.35);border-radius:12px;font:13px/1.7 "Segoe UI",sans-serif;white-space:pre-wrap;box-shadow:0 18px 50px rgba(0,0,0,.5);';
    var close = document.createElement('button');
    close.textContent = '\u5173\u95ed';
    close.style.cssText = 'position:absolute;top:8px;right:10px;border:0;background:transparent;color:#9dd6cd;cursor:pointer;font-size:13px;';
    close.onclick = function () { box.remove(); };
    box.appendChild(close);
    box.appendChild(document.createTextNode(text));
    document.body.appendChild(box);
  }
  window.lianxinBridge = {
    refreshSnapshot: function (cb) {
      fetch(base + '/snapshot').then(function (r) { return r.json(); }).then(function (d) { cb(JSON.stringify(d)); }).catch(function (e) { cb(JSON.stringify({ error: String(e) })); });
    },
    openOriginalMessages: function (idsStr) {
      fetch(base + '/messages?ids=' + encodeURIComponent(idsStr)).then(function (r) { return r.json(); }).then(function (d) { showMessages(d.text || ''); }).catch(function (e) { showMessages('\u8bfb\u53d6\u5931\u8d25\uff1a' + e); });
    },
    queueMemoryReview: function (id, cb) {
      fetch(base + '/review', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: Number(id) }) }).then(function (r) { return r.json(); }).then(function (d) { cb(Boolean(d.ok)); }).catch(function () { cb(false); });
    },
    correctMemory: function (id, content, cb) {
      fetch(base + '/correct', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: Number(id), content: String(content || '') }) }).then(function (r) { return r.json(); }).then(function (d) { cb(d); }).catch(function (e) { cb({ ok: false, error: String(e) }); });
    },
    deleteMemory: function (id, cb) {
      fetch(base + '/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: Number(id) }) }).then(function (r) { return r.json(); }).then(function (d) { cb(d); }).catch(function (e) { cb({ ok: false, error: String(e) }); });
    },
    toggleFullscreen: function () {
      window.parent.postMessage({ source: 'lianxin-embedded-window', action: 'maximize' }, '*');
      return true;
    }
  };
})();
</script>"""

_RIPPLE_BRIDGE_SHIM = """<script>
(function () {
  var base = '/api/ripple';
  window.lianxinBridge = {
    refreshSnapshot: function (cb) {
      fetch(base + '/snapshot').then(function (r) { return r.json(); }).then(function (d) { cb(JSON.stringify(d)); }).catch(function (e) { cb(JSON.stringify({ error: String(e) })); });
    },
    simulateEmotion: function (name, cb) {
      fetch(base + '/simulate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scenario: name }) }).then(function (r) { return r.json(); }).then(function (d) { cb(JSON.stringify(d)); }).catch(function (e) { cb(JSON.stringify({ ok: false, reason: String(e) })); });
    },
    restoreEmotionSimulation: function (cb) {
      fetch(base + '/restore', { method: 'POST' }).then(function (r) { return r.json(); }).then(function (d) { cb(JSON.stringify(d)); }).catch(function (e) { cb(JSON.stringify({ ok: false, reason: String(e) })); });
    },
    configureEmotion: function (raw, cb) {
      fetch(base + '/configure', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ config: raw }) }).then(function (r) { return r.json(); }).then(function (d) { cb(Boolean(d.ok)); }).catch(function () { cb(false); });
    },
    clearEmotionSimulation: function (cb) {
      fetch(base + '/clear', { method: 'POST' }).then(function (r) { return r.json(); }).then(function (d) { cb(Boolean(d.ok)); }).catch(function () { cb(false); });
    },
    toggleFullscreen: function (cb) {
      window.parent.postMessage({ source: 'lianxin-embedded-window', action: 'maximize' }, '*');
      if (cb) cb(true);
    }
  };
})();
</script>"""

_DATA_TIDE_BRIDGE_SHIM = """<script>
(function () {
  function request(path, options) {
    return fetch('/api/data-tide/' + path, options || {}).then(function (response) {
      if (!response.ok) throw new Error('HTTP ' + response.status);
      return response.json();
    });
  }
  var bridge = {
    get_initial_state: function (cb) { request('state').then(function (data) { cb(JSON.stringify(data)); }); },
    refresh: function (cb) { request('state').then(function (data) { cb(JSON.stringify(data)); }); },
    get_journey_page: function (offset, limit, categories, day, cb) {
      var query = new URLSearchParams({ offset: offset || 0, limit: limit || 20, categories: categories || '[]', day: day || '' });
      request('journey?' + query.toString()).then(function (data) { cb(JSON.stringify(data)); });
    },
    export_metrics: function (format, cb) {
      request('export', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ format: format || 'json' }) }).then(function (data) { cb(JSON.stringify(data)); });
    },
    mark_unlocks_read: function (ids, cb) {
      request('mark-read', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids: ids || '[]' }) }).then(function (data) { cb(JSON.stringify(data)); });
    },
    request_close: function () { window.parent.postMessage({ source: 'lianxin-embedded-window', action: 'close' }, '*'); },
    request_minimize: function () { window.parent.postMessage({ source: 'lianxin-embedded-window', action: 'minimize' }, '*'); },
    request_fullscreen: function () { window.parent.postMessage({ source: 'lianxin-embedded-window', action: 'maximize' }, '*'); }
  };
  window.qt = { webChannelTransport: {} };
  window.QWebChannel = function (transport, callback) { callback({ objects: { achievementBridge: bridge } }); };
})();
</script>"""

_STUDY_ROOM_BRIDGE_SHIM = """<script>
(function () {
  var listeners = {};
  var signal = function (name) {
    return { connect: function (callback) { (listeners[name] = listeners[name] || []).push(callback); } };
  };
  var call = function (method, args, callback) {
    var parentActions = { minimize_window: 'minimize', toggle_fullscreen: 'maximize', close_window: 'close', set_focus_fullscreen: 'maximize' };
    if (parentActions[method]) {
      window.parent.postMessage({ source: 'lianxin-embedded-window', action: parentActions[method] }, '*');
      if (callback) callback(null);
      return;
    }
    fetch('/api/study-room/rpc', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ method: method, args: args || [] }) })
      .then(function (response) { if (!response.ok) throw new Error('HTTP ' + response.status); return response.json(); })
      .then(function (data) { if (callback) callback(data.result); })
      .catch(function (error) { console.warn('[StudyRoom HTTP]', error); if (callback) callback(null); });
  };
  var bridge = new Proxy({
    timer_tick: signal('timer_tick'), phase_changed: signal('phase_changed'), tasks_changed: signal('tasks_changed'),
    statistics_changed: signal('statistics_changed'), companion_message: signal('companion_message'),
    focus_completed: signal('focus_completed'), clock_changed: signal('clock_changed')
  }, { get: function (target, name) { return target[name] || function () {
    var args = Array.prototype.slice.call(arguments), callback = typeof args[args.length - 1] === 'function' ? args.pop() : null;
    call(name, args, callback);
  }; } });
  var last = '', lastPhase = '';
  function poll() {
    call('get_initial_state', [], function (raw) {
      if (!raw) return;
      try {
        var state = typeof raw === 'string' ? JSON.parse(raw) : raw;
        var snapshot = JSON.stringify(state);
        if (snapshot === last) return;
        last = snapshot;
        var phase = (state.timer || {}).phase || 'idle';
        if (phase !== lastPhase) {
          lastPhase = phase;
          (listeners.phase_changed || []).forEach(function (fn) { fn(phase); });
        }
        (listeners.clock_changed || []).forEach(function (fn) { fn(JSON.stringify(state.clock || {})); });
        (listeners.timer_tick || []).forEach(function (fn) { fn(JSON.stringify(state.timer || {})); });
        (listeners.tasks_changed || []).forEach(function (fn) { fn(JSON.stringify(state.tasks || [])); });
        (listeners.statistics_changed || []).forEach(function (fn) { fn(JSON.stringify(state.stats || {})); });
      } catch (error) { console.warn('[StudyRoom state]', error); }
    });
  }
  window.qt = { webChannelTransport: {} };
  window.QWebChannel = function (transport, callback) { callback({ objects: { studyBridge: bridge } }); };
  document.addEventListener('mousedown', function (event) {
    if (event.button !== 0 || event.target.closest('button, input, textarea, select, a')) return;
    if (event.target.closest('.topbar, .focus-window-actions')) window.parent.postMessage({ source: 'lianxin-embedded-window', action: 'drag' }, '*');
  });
  setInterval(poll, 1000);
})();
</script>"""

_TIME_CAPSULE_BRIDGE_SHIM = """<script>
(function () {
  var listeners = {};
  var signal = function (name) { return { connect: function (callback) { (listeners[name] = listeners[name] || []).push(callback); } }; };
  var call = function (method, args, callback) {
    var parentActions = { request_minimize: 'minimize', request_fullscreen: 'maximize', request_close: 'close' };
    if (parentActions[method]) {
      window.parent.postMessage({ source: 'lianxin-embedded-window', action: parentActions[method] }, '*');
      if (callback) callback(null);
      return;
    }
    fetch('/api/time-capsule/rpc', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ method: method, args: args || [] }) })
      .then(function (response) { if (!response.ok) throw new Error('HTTP ' + response.status); return response.json(); })
      .then(function (data) { if (callback) callback(data.result); })
      .catch(function (error) { console.warn('[TimeCapsule HTTP]', error); if (callback) callback(null); });
  };
  var bridge = new Proxy({
    state_changed: signal('state_changed'), page_state_changed: signal('page_state_changed'), page_invalidated: signal('page_invalidated'),
    tree_reply_ready: signal('tree_reply_ready'), generation_completed: signal('generation_completed'), companion_ready: signal('companion_ready')
  }, { get: function (target, name) { return target[name] || function () {
    var args = Array.prototype.slice.call(arguments), callback = typeof args[args.length - 1] === 'function' ? args.pop() : null;
    call(name, args, callback);
  }; } });
  function poll() { call('get_initial_state', [], function (raw) { if (!raw) return; (listeners.state_changed || []).forEach(function (fn) { fn(raw); }); }); }
  window.qt = { webChannelTransport: {} };
  window.QWebChannel = function (transport, callback) { callback({ objects: { capsuleBridge: bridge } }); };
  document.addEventListener('mousedown', function (event) {
    if (event.button !== 0 || event.target.closest('button, input, textarea, select, a')) return;
    if (event.target.closest('.topbar')) window.parent.postMessage({ source: 'lianxin-embedded-window', action: 'drag' }, '*');
  });
  setInterval(poll, 1500);
})();
</script>"""

_EMBEDDED_WINDOW_SCRIPT = """<script>
(function () {
  document.addEventListener('mousedown', function (event) {
    if (event.button !== 0 || event.target.closest('button, input, textarea, select, a')) return;
    if (event.target.closest('.topbar')) window.parent.postMessage({ source: 'lianxin-embedded-window', action: 'drag' }, '*');
  });
})();
</script>"""

_STUDY_ROOM_EMBEDDED_STYLE = """<style>
html { font-size: 13px; }
body { font-size: 13px; }
.sidebar { flex-basis: 208px; min-width: 208px; padding: 22px 16px 14px; }
.brand { font-size: 18px; }.brand-mark { width: 32px; height: 32px; flex-basis: 32px; }
.brand-note { margin: 10px 4px 22px; font-size: 12px; }.nav { gap: 6px; }
.nav-item { height: 46px; padding: 0 13px; gap: 10px; font-size: 13px; }.nav-item span { font-size: 18px; }
.topbar { min-height: 52px; font-size: 12px; }.main-content { font-size: 13px; }
.welcome h1, .section-heading h2 { font-size: 24px; }.welcome p, .section-heading p { font-size: 13px; }
.space-card-head strong { font-size: 16px; }.space-card-head small { font-size: 12px; }
.space-wallpaper-card, .space-note-board, .space-event-board { padding: 16px; }
.wallpaper-option { width: 128px; min-width: 128px; }.wallpaper-option img { height: 68px; }
</style>"""

_TIME_CAPSULE_EMBEDDED_STYLE = """<style>
:root { --font-size-h1: 26px; --font-size-h2: 21px; --font-size-h3: 17px; --font-size-body: 13px; --font-size-small: 12px; --font-size-caption: 11px; --font-size-label: 12px; }
.app-shell { grid-template: 60px 1fr 36px / 190px 1fr; }
.topbar { padding: 0 18px 0 24px; }.sidebar { padding: 22px 12px; }
.brand { padding: 0 6px 22px; text-align: left; }.brand h1, .brand p, .c-sidebar-item span, .version { display: block; }
.c-sidebar-item { justify-content: flex-start; padding: 0 14px; gap: 10px; }
.tree-compose textarea, .tree-note { font-size: 15px; }.tree-compose textarea::placeholder { font-size: 14px; }
.paper-editor, .paper-reading { font-size: 16px; padding: 24px; }.detail-content { font-size: 15px; }
</style>"""


def _study_room_wallpaper_url(path: Path) -> str:
    return "/api/study-room/wallpaper?path=" + quote(str(path.resolve()), safe="")


# ── 音乐空间壁纸：id 与磁盘路径解耦 ──────────────────────────────────
# 旧版直接把绝对路径当 id 存进设置，项目一换目录/换机器壁纸就失效，也会把本机
# 路径透给前端。现在统一用可移植 id：内置壁纸 asset:<文件名>，自定义 file:<绝对路径>，
# 磁盘路径一律由后端按 id 解析（并校验不越界）。
_MUSIC_WALLPAPER_ASSET_PREFIX = "asset:"
_MUSIC_WALLPAPER_FILE_PREFIX = "file:"


def _music_wallpaper_asset_dir():
    try:
        from utils.resource_path import get_asset_path
        return get_asset_path("主界面背景图")
    except Exception:
        return None


def _music_asset_wallpaper_id(name) -> str:
    return _MUSIC_WALLPAPER_ASSET_PREFIX + str(name)


def _music_file_wallpaper_id(path) -> str:
    return _MUSIC_WALLPAPER_FILE_PREFIX + str(Path(path).resolve())


def _music_wallpaper_url(wallpaper_id) -> str:
    value = str(wallpaper_id or "")
    if not value or value == "default":
        return ""
    return "/api/music/wallpaper?id=" + quote(value, safe="")


def _music_wallpaper_path(wallpaper_id):
    """把壁纸 id 解析成磁盘路径；无法解析或越界时返回 None。"""
    value = str(wallpaper_id or "").strip()
    if not value or value == "default":
        return None
    if value.startswith(_MUSIC_WALLPAPER_ASSET_PREFIX):
        directory = _music_wallpaper_asset_dir()
        if directory is None:
            return None
        try:
            root = directory.resolve()
            candidate = (root / value[len(_MUSIC_WALLPAPER_ASSET_PREFIX):]).resolve()
        except Exception:
            return None
        return candidate if candidate.is_file() and candidate.parent == root else None
    if value.startswith(_MUSIC_WALLPAPER_FILE_PREFIX):
        value = value[len(_MUSIC_WALLPAPER_FILE_PREFIX):]
    try:
        candidate = Path(value).expanduser().resolve()
    except Exception:
        return None
    return candidate if candidate.is_file() else None


def _music_portable_wallpaper_id(stored, wallpapers) -> str:
    """旧版设置里存的是绝对路径：按文件名匹配回可移植的内置壁纸 id。"""
    try:
        target = Path(str(stored or "")).name.lower()
    except Exception:
        return ""
    if not target:
        return ""
    for item in wallpapers or []:
        item_id = str(item.get("id") or "")
        if item_id.startswith(_MUSIC_WALLPAPER_ASSET_PREFIX) and \
                item_id[len(_MUSIC_WALLPAPER_ASSET_PREFIX):].lower() == target:
            return item_id
    return ""





# ---------- ????????? Tauri CSP ??????? ----------
_COVER_CACHE_DIR: Path | None = None


def _cover_cache_dir() -> Path:
    global _COVER_CACHE_DIR
    if _COVER_CACHE_DIR is None:
        try:
            from utils.paths import get_user_data_dir
            _COVER_CACHE_DIR = get_user_data_dir() / "netease_cover_cache"
        except Exception:
            _COVER_CACHE_DIR = Path.home() / ".lianxin" / "netease_cover_cache"
    _COVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _COVER_CACHE_DIR


def _download_cover(remote_url: str) -> Path:
    """???????????????????????"""
    import urllib.request
    digest = hashlib.md5(remote_url.encode("utf-8")).hexdigest()[:16]
    target = _cover_cache_dir() / (digest + ".jpg")
    if not target.exists():
        req = urllib.request.Request(remote_url, headers={"User-Agent": "Mozilla/5.0 (Lianxin)"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = resp.read()
        if data:
            target.write_bytes(data)
    return target


def _cleanup_cover_cache() -> None:
    """?? mtime ? 7 ???? 100 ???????"""
    try:
        cache_dir = _cover_cache_dir()
        entries = []
        for child in cache_dir.iterdir():
            try:
                if child.is_file():
                    entries.append((child.stat().st_mtime, child))
            except Exception:
                continue
        if not entries:
            return
        entries.sort(key=lambda item: item[0], reverse=True)  # ????
        now = time.time()
        keep = []
        for mtime, child in entries:
            if mtime < now - 7 * 86400 or len(keep) >= 100:
                try:
                    child.unlink()
                except Exception:
                    pass
            else:
                keep.append(child)
    except Exception as exc:
        bridge_log.log("清理", f"封面缓存清理失败: {exc}")


def _cover_cleanup_loop() -> None:
    _cleanup_cover_cache()
    while True:
        time.sleep(60 * 60)
        _cleanup_cover_cache()

from utils import bridge_log
from utils.note_manager import read_note, write_note
from brain.task_store import get_task_store


def _memory_heartbeat_loop() -> None:
    """Periodic RSS/system-memory heartbeat matching the legacy [内存] log."""
    while True:
        try:
            from utils.memory_guard import get_memory_status
            status = get_memory_status() or {}
            rss = status.get("rss_mb")
            available = status.get("available_mb")
            percent = status.get("percent")
            rss_text = f"RSS={rss:.0f}MB" if rss else "RSS=n/a"
            avail_text = f"可用={available:.0f}MB" if available else "可用=n/a"
            percent_text = f"占用={percent:.0f}%" if percent is not None else "占用=n/a"
            bridge_log.log("内存", f"心跳 {rss_text} {avail_text} {percent_text}")
        except Exception:
            pass
        time.sleep(30)


def _watchdog_loop() -> None:
    """Periodic health check while the HTTP server is running."""
    while True:
        time.sleep(60)
        try:
            bridge_log.log(
                "看门狗",
                f"健康检查 运行={bridge_log.uptime_seconds():.0f}s 线程数={threading.active_count()}",
            )
        except Exception:
            pass


class QQBridgeRuntime:
    """Headless owner for the QQ bridge used by the React/legacy boundary."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._worker = None
        self._connected = False
        self._stopping = False
        self._last_error = ""
        self._last_disconnect = ""
        self._fast_reply_enabled = False

    @staticmethod
    def _register_worker(worker) -> None:
        from brain.tools import _register_qq_bridge
        _register_qq_bridge(worker)

    @staticmethod
    def _worker_is_running(worker) -> bool:
        if worker is None:
            return False
        try:
            return bool(worker.isRunning())
        except RuntimeError:
            return False

    def _on_connected(self) -> None:
        with self._lock:
            self._connected = True
            self._last_error = ""
            self._last_disconnect = ""
        bridge_log.log("QQ", "QQ bridge connected")
        try:
            from brain.runtime_status import update_status
            update_status("qq", running=True, connected=True,
                          health="healthy", last_activity_summary="QQ bridge connected")
        except Exception:
            pass

    def _on_disconnected(self, reason: str) -> None:
        with self._lock:
            stopping = self._stopping
            self._connected = False
            if not stopping:
                self._last_disconnect = str(reason or "connection closed")
        bridge_log.log("QQ", f"QQ bridge disconnected: {reason}")
        if stopping:
            return
        try:
            from brain.runtime_status import update_status
            update_status("qq", running=True, connected=False,
                          last_activity_summary=f"QQ bridge disconnected: {reason}")
        except Exception:
            pass

    def _on_error(self, error: str) -> None:
        with self._lock:
            self._connected = False
            self._last_error = str(error or "QQ bridge error")
        bridge_log.log("QQ", f"QQ bridge error: {error}")
        try:
            from brain.runtime_status import update_status
            update_status("qq", running=True, connected=False, health="error",
                          last_activity_summary=f"QQ bridge error: {error}")
        except Exception:
            pass

    def _on_debug_log(self, message: str) -> None:
        bridge_log.log("QQ", str(message))

    def status(self) -> dict:
        with self._lock:
            worker = self._worker
            running = self._worker_is_running(worker)
            socket_connected = False
            if running and worker is not None:
                try:
                    socket_connected = bool(worker._ws and worker._ws.sock and worker._ws.sock.connected)
                except Exception:
                    socket_connected = False
            connected = bool(running and (self._connected or socket_connected))
            if connected:
                state = "connected"
            elif running and self._last_error:
                state = "error"
            elif running:
                state = "connecting"
            else:
                state = "stopped"
            try:
                from config import get_qq_bridge_config
                url = str(get_qq_bridge_config().get("ws_url") or "")
            except Exception:
                url = ""
            return {
                "running": running,
                "connected": connected,
                "state": state,
                "url": url,
                "error": self._last_error if running else "",
                "disconnectReason": self._last_disconnect if running else "",
                "fastReplyEnabled": self._fast_reply_enabled,
            }

    def start(self) -> dict:
        with self._lock:
            if self._worker_is_running(self._worker):
                return self.status()
            from config import get_qq_bridge_config
            config = get_qq_bridge_config()
            if not str(config.get("qq_account") or "").strip():
                raise ValueError("QQ account is not configured")
            if not str(config.get("ws_url") or "").strip():
                raise ValueError("QQ WebSocket URL is not configured")

            # api_server normally creates QCoreApplication before HTTP starts.
            # Keep the runtime usable in focused tests and direct imports too.
            try:
                from PyQt5.QtCore import QCoreApplication
                if QCoreApplication.instance() is None:
                    self._qt_app_hold = QCoreApplication([])
            except Exception as exc:
                raise RuntimeError(f"Qt runtime is unavailable: {exc}") from exc

            from workers.qq_bridge_worker import QQBridgeWorker
            worker = QQBridgeWorker()
            worker.set_fast_reply_enabled(self._fast_reply_enabled)
            from PyQt5.QtCore import Qt
            worker.connected.connect(self._on_connected, Qt.DirectConnection)
            worker.disconnected.connect(self._on_disconnected, Qt.DirectConnection)
            worker.error_occurred.connect(self._on_error, Qt.DirectConnection)
            worker.debug_log.connect(self._on_debug_log, Qt.DirectConnection)
            self._worker = worker
            self._connected = False
            self._stopping = False
            self._last_error = ""
            self._last_disconnect = ""
            self._register_worker(worker)
            worker.start()
            bridge_log.log("QQ", "QQ bridge worker started")
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            worker = self._worker
            self._stopping = True
            if worker is not None:
                try:
                    worker.stop()
                except Exception as exc:
                    bridge_log.log("QQ", f"QQ bridge stop failed: {exc}")
                try:
                    worker.wait(7000)
                except Exception:
                    pass
            self._worker = None
            self._connected = False
            self._last_error = ""
            self._last_disconnect = ""
            self._register_worker(None)
            self._stopping = False
            try:
                from brain.runtime_status import update_status
                update_status("qq", running=False, connected=False,
                              health="stopped", last_activity_summary="QQ bridge stopped")
            except Exception:
                pass
            return self.status()

    def reload(self) -> dict:
        with self._lock:
            if self._worker_is_running(self._worker):
                self._worker.reload_bridge_config()
            return self.status()

    def set_fast_reply(self, enabled: bool) -> dict:
        with self._lock:
            self._fast_reply_enabled = bool(enabled)
            if self._worker is not None:
                self._worker.set_fast_reply_enabled(self._fast_reply_enabled)
            return self.status()


class LianxinBridge:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._chat_lock = threading.Lock()
        self._agent = None
        self._busy = False
        self._voice = None
        self._voice_lock = threading.RLock()
        self._voice_events = deque(maxlen=100)
        self._music_events = deque(maxlen=200)
        self._music_watcher = None
        self._music_watcher_lock = threading.RLock()
        self._voice_input = None
        self._proactive = None
        self._proactive_runtime = None
        self._avatar_lock = threading.RLock()
        self._avatar_last_trigger = 0.0
        self._avatar_busy = False
        self._avatar_streak = 0
        self._avatar_last_tap = 0.0
        self._avatar_stats = None
        self._tts_lock = threading.RLock()
        self._tts_worker = None
        self._tts_generation = 0
        self._prepared_tts = {}
        self._call_sound = None
        self._call_channel = None
        self._netease_spawn_lock = threading.Lock()
        self._music_control_lock = threading.Lock()
        self._netease_proc = None
        self._netease_fail_streak = 0
        self._netease_cooldown_until = 0.0
        self._netease_last_spawn_error = ""
        self._netease_cooldown_logged_at = 0.0
        self._watched_song = None
        from brain.music_service import MusicService
        self.music_service = MusicService(self)
        from brain.music_state import NetEaseStateProvider
        self._music_state_provider = NetEaseStateProvider(poll_interval=1.0, timeout=1.2)
        self._study_room_bridge = None
        self._time_capsule_bridge = None
        self._study_room_lock = threading.RLock()
        self._time_capsule_lock = threading.RLock()
        self._diary_workers = set()
        self._qq_runtime = QQBridgeRuntime()
        # Reuse the legacy companion counter so the standalone React/Tauri
        # runtime contributes to the same data-tide statistics.
        from utils.accompany_stats import AccompanyStats
        self._accompany_stats = AccompanyStats()
        self._accompany_stats.start_session()
        atexit.register(self._end_accompany_session)

    def _end_accompany_session(self) -> None:
        try:
            self._accompany_stats.end_session()
        except Exception:
            pass

    def agent(self):
        with self._lock:
            if self._agent is None:
                from brain.agent import AgentCore
                self._agent = AgentCore()
            self._agent._execution_status_enabled = True
            from utils.execution_status import get_execution_status_bus
            get_execution_status_bus().enabled = True
            return self._agent

    def qq_status(self) -> dict:
        return self._qq_runtime.status()

    def start_qq(self) -> dict:
        return self._qq_runtime.start()

    def stop_qq(self) -> dict:
        return self._qq_runtime.stop()

    def reload_qq(self) -> dict:
        return self._qq_runtime.reload()

    def set_qq_fast_reply(self, enabled: bool) -> dict:
        return self._qq_runtime.set_fast_reply(enabled)

    def status(self) -> dict:
        agent = self.agent()
        return {
            "online": True,
            "sessionId": getattr(agent, "_session_id", None),
            "busy": self._busy,
            "source": "python-core",
        }

    def cancel_chat(self) -> dict:
        agent = self.agent()
        cancelled = bool(agent.cancel_active_request("用户在新版界面中停止了当前回复"))
        return {"cancelled": cancelled}

    def sessions(self) -> list[dict]:
        return self.agent().get_history_manager().get_sessions()

    def messages(self, session_id: int, after: int = 0) -> list[dict]:
        items = self.agent().get_history_manager().get_messages_with_ids(session_id)
        if int(after or 0) > 0:
            items = [item for item in items if int(item.get("id") or 0) > int(after)]
        # The attachment bridge uses these markers internally so the model can
        # distinguish an image caption from the user's accompanying text. They
        # are transport syntax, never visible chat content.
        for item in items:
            if item.get("role") != "user":
                continue
            content = str(item.get("content") or "")
            prefix = "[用户附加文字]"
            suffix = "[用户附加文字结束]"
            if content.startswith(prefix) and content.endswith(suffix):
                item["content"] = content[len(prefix):-len(suffix)].strip()

        # Internal proactive source labels (proactive/slack/observe) belong to model
        # context, never to the chat bubbles. Strip them for display.
        import re as _re
        _proactive_label = _re.compile(r"^\s*(?:\[(?:\u4e3b\u52a8|\u6478\u9c7c|\u89c2\u5bdf)\]\s*)+")
        for item in items:
            if item.get("role") != "assistant":
                continue
            content = str(item.get("content") or "")
            stripped = _proactive_label.sub("", content)
            if stripped != content:
                item["content"] = stripped.strip()
        attachment_count = sum(len(item.get("attachments", []) or []) for item in items)
        description_count = sum(
            1 for item in items for attachment in (item.get("attachments", []) or [])
            if str(attachment.get("description") or "").strip()
        )
        _poll_message = (
            f"messages_response session={session_id} items={len(items)} "
            f"attachments={attachment_count} descriptions={description_count}"
        )
        if items or attachment_count or description_count:
            _image_flow_log(_poll_message)
        else:
            _image_flow_log_poll(_poll_message)
        for item in items:
            for attachment in item.get("attachments", []) or []:
                path = str(attachment.get("path") or "")
                if path:
                    attachment["url"] = f"http://127.0.0.1:8766/api/attachments?path={__import__('urllib.parse', fromlist=['quote']).quote(path)}"
        return items

    def new_session(self) -> dict:
        agent = self.agent()
        with self._chat_lock:
            agent.new_session()
        return {"id": agent._session_id}

    def select_session(self, session_id: int) -> dict:
        with self._chat_lock:
            from brain.agent import AgentCore
            self._agent = AgentCore(session_id=int(session_id))
            self._agent._execution_status_enabled = True
            return {"id": self._agent._session_id}

    def chat(self, text: str) -> dict:
        if not text.strip():
            raise ValueError("消息不能为空")
        self._proactive_user_activity()
        with self._chat_lock:
            agent = self.agent()
            self._busy = True
            try:
                response = agent.chat(text)
            finally:
                self._busy = False
        return {
            "sessionId": agent._session_id,
            "message": {"role": "assistant", "content": response or ""},
        }

    @staticmethod
    def _store_image_attachment(attachment: dict) -> tuple[Path, str]:
        """Persist a WebView image using the same managed directory as legacy UI."""
        data_url = str(attachment.get("dataUrl") or "")
        if not data_url.startswith("data:image/") or ";base64," not in data_url:
            raise ValueError("仅支持 base64 图片附件")
        header, encoded = data_url.split(";base64,", 1)
        mime = header[5:].lower()
        extension = mimetypes.guess_extension(mime) or ".png"
        if extension == ".jpe":
            extension = ".jpg"
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > 12 * 1024 * 1024:
            raise ValueError("图片为空或超过 12 MB")
        root = Path.home() / ".lianxin" / "images"
        root.mkdir(parents=True, exist_ok=True)
        filename = Path(str(attachment.get("fileName") or "image")).stem[:80]
        target = root / f"{filename}-{uuid.uuid4().hex[:10]}{extension}"
        target.write_bytes(raw)
        return target, mime

    @staticmethod
    def _store_file_attachment(attachment: dict) -> Path:
        data_url = str(attachment.get("dataUrl") or "")
        if ";base64," not in data_url:
            raise ValueError("文件附件格式无效")
        _, encoded = data_url.split(";base64,", 1)
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > 20 * 1024 * 1024:
            raise ValueError("文件为空或超过 20 MB")
        root = Path.home() / ".lianxin" / "files"
        root.mkdir(parents=True, exist_ok=True)
        filename = Path(str(attachment.get("fileName") or "attachment")).name[:100]
        target = root / f"{uuid.uuid4().hex[:10]}-{filename}"
        target.write_bytes(raw)
        return target

    def chat_stream(self, text: str, attachments: list[dict] | None = None,
                    forced_tool: str | None = None, preferred_tool: str | None = None,
                    quote: dict | None = None):
        """Run AgentCore in a worker and forward tool lifecycle events as SSE data."""
        attachments = list(attachments or [])
        if not text.strip() and not attachments:
            raise ValueError("message or image attachment is required")
        self._proactive_user_activity()
        from queue import Queue

        events = Queue()
        agent = self.agent()
        trace_id = uuid.uuid4().hex[:12]
        _image_flow_log(
            f"request trace={trace_id} session={getattr(agent, '_session_id', None)} "
            f"text_chars={len(str(text or '').strip())} attachments={len(attachments)} "
            f"kinds={[str(item.get('kind') or 'image') for item in attachments]}"
        )
        pending_tool_round = [None]
        # 本轮"莲心看到的画面"（截图/摄像头），收尾时挂到她自己的回复消息上。
        pending_observation = [None]

        def on_round_start(round_num):
            # AgentCore enters a model round even for ordinary text replies.
            # The legacy UI creates its debug group only after a real tool call.
            pending_tool_round[0] = int(round_num)

        def on_tool_call(name, args):
            round_num = pending_tool_round[0]
            if round_num is not None:
                events.put({"type": "tool_round_start", "round": round_num})
                pending_tool_round[0] = None
            safe_args = args or {}
            if str(name or "").startswith("browser_"):
                try:
                    from brain.browser_security import redact_browser_args
                    safe_args = redact_browser_args(name, safe_args)
                except Exception:
                    safe_args = {"redacted": True}
            events.put({
                "type": "tool_call", "name": str(name), "args": safe_args,
            })

        def on_tool_result(name, result, is_error=False, elapsed_ms=0):
            preview = str(result or "")[:240]
            if len(str(result or "")) > 240:
                preview += "..."
            events.put({
                "type": "tool_result", "name": str(name), "preview": preview,
                "isError": bool(is_error), "elapsedMs": float(elapsed_ms or 0),
            })
            if str(name) in OBSERVATION_TOOLS:
                try:
                    from brain.tools import get_observation_image
                    observation = get_observation_image()
                    if observation.get("path"):
                        pending_observation[0] = observation
                except Exception as exc:
                    _image_flow_log(f"observe_capture_failed tool={name} error={exc}")

        def run():
            with self._chat_lock:
                self._busy = True
                try:
                    context_parts = []
                    stored_paths = []
                    descriptions = []
                    for index, attachment in enumerate(attachments):
                        if str(attachment.get("kind") or "image") == "file":
                            try:
                                file_path = self._store_file_attachment(attachment)
                                stored_paths.append(file_path)
                                descriptions.append("")
                                context_parts.append(
                                    f"[用户发送了文件]\n文件名：{attachment.get('fileName', '附件')}\n已保存路径：{file_path}\n"
                                    "如果需要读取文件内容，请使用可用的文件读取工具。"
                                )
                            except Exception as exc:
                                stored_paths.append("")
                                descriptions.append("")
                                context_parts.append(f"[文件附件保存失败] {exc}")
                            events.put({"type": "file_attachment_saved", "index": index,
                                        "fileName": str(attachment.get("fileName") or "attachment")})
                            continue
                        events.put({"type": "image_analysis_started", "index": index,
                                    "fileName": str(attachment.get("fileName") or "image")})
                        try:
                            image_path, _ = self._store_image_attachment(attachment)
                            stored_paths.append(image_path)
                            from brain.vision import describe_image
                            description = describe_image(
                                str(image_path),
                            )
                            descriptions.append(description)
                            _image_flow_log(
                                f"vision trace={trace_id} index={index} path={image_path} "
                                f"description_chars={len(description)}"
                            )
                            context_parts.append(
                                "[系统观察：以下是图片的原始观察记录]\n"
                                f"{_clean_vision_description(description)}\n"
                                "[观察记录结束]"
                            )
                            events.put({"type": "image_analysis_result", "index": index,
                                        "fileName": str(attachment.get("fileName") or "image"),
                                        "description": description})
                        except Exception as exc:
                            stored_paths.append("")
                            error_text = str(exc)
                            descriptions.append(error_text)
                            context_parts.append(f"[图片分析失败] {error_text}")
                            events.put({"type": "image_analysis_result", "index": index,
                                        "fileName": str(attachment.get("fileName") or "image"),
                                        "error": error_text, "description": error_text})
                    user_text = text.strip()
                    if attachments and user_text:
                        user_text = f"[用户附加文字]\n{user_text}\n[用户附加文字结束]"
                    quote_text = ""
                    if isinstance(quote, dict) and str(quote.get("content") or "").strip():
                        quote_role = "你" if quote.get("role") == "user" else "莲心"
                        quote_text = f"[用户正在回复此前的{quote_role}消息]\n{str(quote.get('content'))[:12000]}\n[引用消息结束]"
                    agent_text = "\n\n".join(context_parts + ([quote_text] if quote_text else []) + ([user_text] if user_text else []))
                    if not agent_text.strip():
                        agent_text = "请根据你看到的图片自然地回应。"
                    from brain.request_context import parse_request_context
                    request_context = parse_request_context(agent_text)
                    _image_flow_log(
                        f"agent_input trace={trace_id} session={getattr(agent, '_session_id', None)} "
                        f"agent_chars={len(agent_text)} image_blocks={len(request_context.image_descriptions)} "
                        f"routing_chars={len(request_context.routing_text)} "
                        f"original_text_present={bool(text.strip())} "
                        f"routing_preview={request_context.routing_text[:80]!r}"
                    )
                    assistant_id_before = _latest_assistant_message_id(agent)
                    response = agent.chat(
                        agent_text,
                        forced_tool=forced_tool or None,
                        preferred_tool=preferred_tool or None,
                        on_round_start=on_round_start,
                        on_tool_call=on_tool_call,
                        on_tool_result=on_tool_result,
                    )
                    _attach_observation_to_reply(
                        agent, pending_observation[0], assistant_id_before
                    )
                    pending_observation[0] = None
                    if quote_text:
                        agent.get_history_manager().update_latest_message_content(
                            agent._session_id, "user", text.strip() or "看看这张图片"
                        )
                    if attachments:
                        metadata = {"attachments": [
                            {"kind": str(item.get("kind") or "image"),
                             "fileName": str(item.get("fileName") or "attachment"),
                             "path": str(path),
                             "description": descriptions[index] if index < len(descriptions) else ""}
                            for index, (item, path) in enumerate(zip(attachments, stored_paths))
                        ]}
                        agent.get_history_manager().update_latest_message_metadata(
                            agent._session_id, "user", metadata
                        )
                        visible_text = text.strip() or "看看这张图片"
                        agent.get_history_manager().update_latest_message_content(
                            agent._session_id, "user", visible_text
                        )
                        for history_item in reversed(getattr(agent, "history", [])):
                            if history_item.get("role") == "user":
                                history_item["content"] = visible_text
                                break
                        _image_flow_log(
                            f"persist trace={trace_id} session={getattr(agent, '_session_id', None)} "
                            f"paths={len([path for path in stored_paths if path])} "
                            f"descriptions={len([value for value in descriptions if value])} "
                            f"visible_text_chars={len(visible_text)}"
                        )
                    events.put({
                        "type": "completed", "sessionId": agent._session_id,
                        "message": {"role": "assistant", "content": response or ""},
                    })
                except Exception as exc:
                    events.put({"type": "error", "error": str(exc)})
                finally:
                    self._busy = False
                    events.put(None)

        threading.Thread(target=run, name="lianxin-api-chat", daemon=True).start()
        yield {"type": "started"}
        while True:
            event = events.get()
            if event is None:
                break
            yield event

    def capabilities(self) -> dict:
        from brain.capability_catalog import list_capabilities
        return {"items": [item.__dict__ for item in list_capabilities(include_disabled=True)]}

    def speak(self, text: str, voice: str = "", generation: int | None = None) -> dict:
        if not str(text or "").strip():
            raise ValueError("朗读内容不能为空")
        from utils.settings import SettingsManager
        if SettingsManager().silent_mode:
            return {"speaking": False, "muted": True}
        with self._tts_lock:
            if generation is not None and generation != self._tts_generation:
                return {"speaking": False, "cancelled": True}
            self.stop_speaking()
            generation = self._tts_generation
        from voice.speaker import VoiceSpeaker
        if not voice:
            try:
                from config import get_tts_config
                voice = str(get_tts_config().get("edge_tts_voice") or "zh-CN-XiaoxiaoNeural")
            except Exception:
                voice = "zh-CN-XiaoxiaoNeural"
        from brain import tts_health
        breaker = tts_health.get_breaker()
        remaining = breaker.open_remaining()
        # 单引擎语义：没有兜底引擎可切换。Edge 熔断期内直接如实上报失败，
        # 前端会显示一行提示；GPT-SoVITS 模式与 Edge 熔断器无关，不受影响。
        edge_in_use = True
        try:
            from config import get_tts_config
            from brain.tts_engine import resolve_tts_mode, MODE_GPT_ONLY
            edge_in_use = resolve_tts_mode(get_tts_config().get("engine")) != MODE_GPT_ONLY
        except Exception:
            edge_in_use = True
        if edge_in_use and remaining > 0:
            reason = breaker.reason()
            tts_health.note_engine_failure("edge_tts", reason)
            notice = tts_health.engine_failure_text("edge_tts")
            bridge_log.log("语音", f"TTS 熔断中（剩余 {remaining:.0f}s），跳过朗读：{reason}")
            with self._voice_lock:
                self._voice_events.append({
                    "id": time.time_ns(), "type": "voice.tts_unavailable",
                    "reason": reason, "notice": notice,
                })
            return {"speaking": False, "unavailable": True, "reason": reason, "notice": notice}
        speaker = VoiceSpeaker(voice=voice)
        def run():
            voice_manager = self._voice
            with self._tts_lock:
                if generation != self._tts_generation:
                    return
                if voice_manager is not None:
                    voice_manager.pause_vad()
                    with self._voice_lock:
                        self._voice_events.append({"id": time.time_ns(), "type": "voice.tts", "state": "speaking"})
            try:
                speaker.speak(str(text), reset_stop=False)
            finally:
                with self._tts_lock:
                    if self._tts_worker is speaker:
                        self._tts_worker = None
                        if voice_manager is not None:
                            voice_manager.resume_vad()
                            with self._voice_lock:
                                self._voice_events.append({"id": time.time_ns(), "type": "voice.tts", "state": "idle"})
        with self._tts_lock:
            if generation != self._tts_generation:
                return {"speaking": False, "cancelled": True}
            self._tts_worker = speaker
        threading.Thread(target=run, name="lianxin-api-tts", daemon=True).start()
        return {"speaking": True}

    def prepare_speech(self, text: str, generation: int | None = None) -> dict:
        if not str(text or "").strip():
            raise ValueError("Speech text must not be empty")
        from utils.settings import SettingsManager
        if SettingsManager().silent_mode:
            return {"prepared": False, "muted": True}
        import uuid
        from voice.speaker import VoiceSpeaker
        with self._tts_lock:
            if generation is not None and generation != self._tts_generation:
                return {"prepared": False, "cancelled": True}
            generation = self._tts_generation
        from config import get_tts_config
        voice = str(get_tts_config().get("edge_tts_voice") or "zh-CN-XiaoxiaoNeural")
        speaker = VoiceSpeaker(voice=voice)
        path = speaker.prepare(str(text))
        if not path:
            return {"prepared": False, "unavailable": True}
        with self._tts_lock:
            if generation != self._tts_generation:
                speaker._remove_temp_file(path)
                return {"prepared": False, "cancelled": True}
            prepared_id = uuid.uuid4().hex
            while len(self._prepared_tts) >= 8:
                expired_id = next(iter(self._prepared_tts))
                expired_speaker, expired_path, _ = self._prepared_tts.pop(expired_id)
                expired_speaker._remove_temp_file(expired_path)
            self._prepared_tts[prepared_id] = (speaker, path, generation)
        return {"prepared": True, "id": prepared_id}

    def play_prepared_speech(self, prepared_id: str) -> dict:
        with self._tts_lock:
            prepared = self._prepared_tts.pop(str(prepared_id or ""), None)
            if prepared is None:
                return {"played": False, "cancelled": True}
            speaker, path, generation = prepared
            if generation != self._tts_generation:
                speaker._remove_temp_file(path)
                return {"played": False, "cancelled": True}
            from utils.settings import SettingsManager
            if SettingsManager().silent_mode:
                speaker._remove_temp_file(path)
                return {"played": False, "muted": True}
            previous = self._tts_worker
            if previous is not None and previous is not speaker:
                speaker._remove_temp_file(path)
                return {"played": False, "busy": True}
            self._tts_worker = speaker
            voice_manager = self._voice
            if voice_manager is not None:
                voice_manager.pause_vad()
                with self._voice_lock:
                    self._voice_events.append({"id": time.time_ns(), "type": "voice.tts", "state": "speaking"})
        try:
            played = speaker.play_prepared(path)
        finally:
            with self._tts_lock:
                if self._tts_worker is speaker:
                    self._tts_worker = None
                    if voice_manager is not None:
                        voice_manager.resume_vad()
                        with self._voice_lock:
                            self._voice_events.append({"id": time.time_ns(), "type": "voice.tts", "state": "idle"})
        return {"played": bool(played) and generation == self._tts_generation,
                "cancelled": generation != self._tts_generation}

    def stop_speaking(self) -> dict:
        from skills.语音合成.tools import stop_voice_playback
        with self._tts_lock:
            self._tts_generation += 1
            stop_voice_playback()
            speaker = self._tts_worker
            self._tts_worker = None
            if speaker is not None:
                try:
                    speaker.stop()
                except Exception:
                    pass
                if self._voice is not None:
                    self._voice.resume_vad()
                    with self._voice_lock:
                        self._voice_events.append({"id": time.time_ns(), "type": "voice.tts", "state": "idle"})
            for prepared_speaker, path, _ in self._prepared_tts.values():
                prepared_speaker._remove_temp_file(path)
            self._prepared_tts.clear()
            return {"speaking": False, "generation": self._tts_generation}

    def tts_state(self) -> dict:
        from brain import tts_health
        with self._tts_lock:
            speaking = self._tts_worker is not None
        return {
            "speaking": speaking,
            "stage": tts_health.current_stage(),
            # 合成失败时给界面一行提示（非破坏性读取，前端按 id 去重）。
            "notice": tts_health.engine_failure_state(),
        }

    def play_sound(self, name: str) -> dict:
        allowed = {
            "ButtonAll.mp3", "ButtonMusic.mp3", "lianxinSend.mp3", "Send message.mp3",
            "ToolBox1.mp3", "MemoBook.mp3", "OpenDiary.mp3", "DaiJiMoShi.mp3",
            "StartSpeak.mp3", "拍一拍.mp3", "FinishedClock.mp3", "write.mp3",
            "page1.mp3", "page2.mp3",
        }
        if name not in allowed:
            raise ValueError("不允许播放的音效文件")
        from utils.sound import play_sound
        play_sound(name)
        return {"played": True, "name": name}

    def start_call_sound(self) -> dict:
        try:
            import pygame
            from utils.resource_path import get_asset_path
            from utils.settings import get_settings
            self.stop_call_sound()
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            path = get_asset_path("sound", "等待接通电话.mp3")
            if not path.exists():
                return {"playing": False}
            self._call_sound = pygame.mixer.Sound(str(path))
            self._call_sound.set_volume(get_settings().sfx_volume)
            self._call_channel = self._call_sound.play(loops=-1)
            return {"playing": True}
        except Exception:
            self._call_sound = None
            self._call_channel = None
            return {"playing": False}

    def stop_call_sound(self) -> dict:
        if self._call_channel is not None:
            try:
                self._call_channel.stop()
            except Exception:
                pass
        self._call_channel = None
        self._call_sound = None
        return {"playing": False}

    def background_state(self, include_data: bool = True) -> dict:
        """Expose the legacy wallpaper settings to the WebView as a data URL."""
        # The settings dialog runs in a separate legacy process.  Constructing
        # a fresh manager here avoids serving the API process's stale singleton.
        from utils.settings import SettingsManager

        settings = SettingsManager()
        was_silent = bool(settings.silent_mode)
        source = str(settings.background_source or "")
        source_type = settings.background_source_type
        path = Path(source).expanduser()
        if source_type in {"folder_first", "folder_random"} and path.is_dir():
            images = sorted(
                item for item in path.iterdir()
                if item.is_file() and item.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
            )
            path = images[0] if images else Path()
        fingerprint = ""
        data_url = ""
        try:
            stat = path.stat()
            fingerprint = f"{path}:{stat.st_mtime_ns}:{stat.st_size}"
            if include_data and stat.st_size <= 12 * 1024 * 1024:
                mime = mimetypes.guess_type(path.name)[0] or "image/png"
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                data_url = f"data:{mime};base64,{encoded}"
        except (OSError, ValueError):
            pass
        return {
            "enabled": bool(settings.background_enabled),
            "opacity": float(settings.background_opacity),
            "chatOpacity": float(settings.chat_background_opacity),
            "fitMode": settings.background_fit_mode,
            "fingerprint": fingerprint,
            "dataUrl": data_url,
        }

    def settings_panel_state(self) -> dict:
        from utils.settings import SettingsManager
        from config import get_avatar_config, get_chat_avatar_config, get_device_preference, get_quick_launch_apps
        from utils.autostart import is_autostart_enabled
        from utils.accompany_stats import AccompanyStats
        settings = SettingsManager()
        tts = get_tts_config()
        return {
            "global": {
                "silentMode": bool(settings.silent_mode),
                "userName": settings.user_name,
                "emotionProbability": float(settings.emotion_probability),
                "showExitConfirmation": bool(settings.show_exit_confirmation),
                "startupCheckEnabled": bool(settings.startup_check_enabled),
                "startupMode": settings.startup_mode,
                "trayEnabled": bool(settings.tray_enabled),
                "closeBehavior": settings.close_behavior,
                "minimizeToTray": bool(settings.minimize_to_tray),
                "restoreWindowState": bool(settings.restore_window_state),
                "alwaysOnTop": bool(settings.always_on_top),
                "reducedMotion": bool(settings.reduced_motion),
                "desktopNotifications": bool(settings.desktop_notifications),
                "fontSize": int(settings.font_size),
                "noteFilePath": settings.note_file_path,
                "backgroundEnabled": bool(settings.background_enabled),
                "backgroundSource": str(settings.background_source or ""),
                "backgroundSourceType": settings.background_source_type,
                "backgroundOpacity": float(settings.background_opacity),
                "chatBackgroundOpacity": float(settings.chat_background_opacity),
                "backgroundFitMode": settings.background_fit_mode,
                "avatarMode": get_avatar_config().get("mode", "animated"),
                "avatarPath": get_avatar_config().get("static_image_path", ""),
                "firstMeetDate": AccompanyStats().get_first_meet_date(),
                "autostart": bool(is_autostart_enabled()),
                "segmentPauseChatMin": float(settings.segment_pause_chat_min),
                "segmentPauseChatMax": float(settings.segment_pause_chat_max),
                "segmentPauseSemanticMin": float(settings.segment_pause_semantic_min),
                "segmentPauseSemanticMax": float(settings.segment_pause_semantic_max),
            },
            "avatar": get_chat_avatar_config(),
            "quickLaunch": get_quick_launch_apps(),
            "performance": {key: get_device_preference(key) for key in ("whisper", "funasr", "rag")},
            "sound": {
                "ttsVolume": float(settings.tts_volume),
                "sfxVolume": float(settings.sfx_volume),
                "silentMode": bool(settings.silent_mode),
                "engine": tts.get("engine", "edge_tts"),
                "speed": float(tts.get("speed", 1.0)),
                "defaultMood": tts.get("default_mood", "auto"),
                "gptSovitsVersion": tts.get("gpt_sovits_version", "v2Pro"),
                "gptSovitsPath": tts.get("gpt_sovits_path", ""),
                "refWavOverride": tts.get("ref_wav_override", ""),
            },
        }

    def save_settings_panel(self, payload: dict) -> dict:
        from utils.settings import SettingsManager
        from config import get_avatar_config, save_avatar_config, save_chat_avatar_config, save_device_preference, save_quick_launch_apps
        from utils.autostart import disable_autostart, enable_autostart, is_autostart_enabled
        from utils.accompany_stats import AccompanyStats
        settings = SettingsManager()
        global_values = payload.get("global") or {}
        avatar_values = payload.get("avatar") or {}
        performance_values = payload.get("performance") or {}
        sound_values = payload.get("sound") or {}
        for key, value in global_values.items():
            if key == "silentMode": settings.silent_mode = bool(value)
            elif key == "userName": settings.user_name = str(value)[:20]
            elif key == "emotionProbability": settings.emotion_probability = max(0.0, min(1.0, float(value)))
            elif key == "showExitConfirmation": settings.show_exit_confirmation = bool(value)
            elif key == "startupCheckEnabled": settings.startup_check_enabled = bool(value)
            elif key == "startupMode": settings.startup_mode = str(value)
            elif key == "trayEnabled": settings.tray_enabled = bool(value)
            elif key == "closeBehavior": settings.close_behavior = str(value)
            elif key == "minimizeToTray": settings.minimize_to_tray = bool(value)
            elif key == "restoreWindowState": settings.restore_window_state = bool(value)
            elif key == "alwaysOnTop": settings.always_on_top = bool(value)
            elif key == "reducedMotion": settings.reduced_motion = bool(value)
            elif key == "desktopNotifications": settings.desktop_notifications = bool(value)
            elif key == "fontSize": settings.font_size = max(10, min(20, int(value)))
            elif key == "noteFilePath": settings.note_file_path = str(value)
            elif key == "segmentPauseChatMin": settings.segment_pause_chat_min = max(0.1, float(value))
            elif key == "segmentPauseChatMax": settings.segment_pause_chat_max = max(0.1, float(value))
            elif key == "segmentPauseSemanticMin": settings.segment_pause_semantic_min = max(0.1, float(value))
            elif key == "segmentPauseSemanticMax": settings.segment_pause_semantic_max = max(0.1, float(value))
            elif key == "backgroundEnabled": settings.background_enabled = bool(value)
            elif key == "backgroundSource": settings.background_source = str(value)
            elif key == "backgroundSourceType": settings.background_source_type = str(value)
            elif key == "backgroundOpacity": settings.background_opacity = max(0.0, min(1.0, float(value)))
            elif key == "chatBackgroundOpacity": settings.chat_background_opacity = max(0.0, min(1.0, float(value)))
            elif key == "backgroundFitMode": settings.background_fit_mode = str(value)
        for key, value in sound_values.items():
            if key == "ttsVolume": settings.tts_volume = max(0.0, min(1.0, float(value)))
            elif key == "sfxVolume": settings.sfx_volume = max(0.0, min(1.0, float(value)))
            elif key == "silentMode": settings.silent_mode = bool(value)
        if sound_values:
            tts = get_tts_config()
            mapping = {"engine": "engine", "speed": "speed", "defaultMood": "default_mood", "gptSovitsVersion": "gpt_sovits_version", "gptSovitsPath": "gpt_sovits_path", "refWavOverride": "ref_wav_override"}
            for source, target in mapping.items():
                if source in sound_values:
                    tts[target] = sound_values[source]
            save_tts_config(tts)
        if avatar_values:
            save_chat_avatar_config(avatar_values)
        for key, value in performance_values.items():
            if key in {"whisper", "funasr", "rag"} and value in {"auto", "cpu", "cuda"}:
                save_device_preference(key, value)
        if "quickLaunch" in payload and isinstance(payload["quickLaunch"], list):
            save_quick_launch_apps(payload["quickLaunch"])
        if "avatarMode" in global_values:
            avatar = get_avatar_config()
            avatar["mode"] = str(global_values["avatarMode"])
            if "avatarPath" in global_values:
                avatar["static_image_path"] = str(global_values["avatarPath"])
            save_avatar_config(avatar)
        if "firstMeetDate" in global_values and global_values["firstMeetDate"]:
            AccompanyStats().set_first_meet_date(str(global_values["firstMeetDate"]))
        if "autostart" in global_values and bool(global_values["autostart"]) != bool(is_autostart_enabled()):
            (enable_autostart if global_values["autostart"] else disable_autostart)()
        if settings.silent_mode and not was_silent:
            self.stop_speaking()
        return self.settings_panel_state()

    @staticmethod
    def _image_payload(path_value: str, include_data: bool) -> dict:
        path = Path(path_value).expanduser() if path_value else Path()
        fingerprint = ""
        data_url = ""
        try:
            if not path.is_file():
                return {"fingerprint": "", "dataUrl": ""}
            stat = path.stat()
            fingerprint = f"{path}:{stat.st_mtime_ns}:{stat.st_size}"
            if include_data and stat.st_size <= 12 * 1024 * 1024:
                mime = mimetypes.guess_type(path.name)[0] or "image/png"
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                data_url = f"data:{mime};base64,{encoded}"
        except (OSError, ValueError):
            pass
        return {"fingerprint": fingerprint, "dataUrl": data_url}

    def avatar_state(self, include_data: bool = True) -> dict:
        """Expose the legacy chat/avatar settings without changing their owner."""
        from config import get_avatar_config, get_chat_avatar_config

        chat_config = get_chat_avatar_config()
        character_config = get_avatar_config()
        chat_assistant_path = str(chat_config.get("assistant_path") or "")
        character_path = ""
        if character_config.get("mode") == "static":
            character_path = str(character_config.get("static_image_path") or "")
        if not chat_assistant_path:
            chat_assistant_path = character_path
        if not chat_assistant_path:
            chat_assistant_path = str(Path(__file__).resolve().parent / "assets" / "莲心形象透明背景.png")

        assistant = self._image_payload(chat_assistant_path, include_data)
        user = self._image_payload(str(chat_config.get("user_path") or ""), include_data)
        character_path = character_path or str(Path(__file__).resolve().parent / "assets" / "GIF" / "normal.gif")
        character = self._image_payload(character_path, include_data)
        fingerprint = "|".join((assistant["fingerprint"], user["fingerprint"], character["fingerprint"], str(chat_config.get("enabled", True)), str(chat_config.get("size", 60)), str(chat_config.get("gap", 10)), str(chat_config.get("border", True))))
        return {
            "enabled": bool(chat_config.get("enabled", True)),
            "size": int(chat_config.get("size", 60) or 60),
            "gap": int(chat_config.get("gap", 10) or 10),
            "border": bool(chat_config.get("border", True)),
            "assistantDataUrl": assistant["dataUrl"],
            "userDataUrl": user["dataUrl"],
            "characterDataUrl": character["dataUrl"],
            "characterFingerprint": character["fingerprint"],
            "fingerprint": fingerprint,
        }

    def avatar_action(self, action: str) -> dict:
        """Run the legacy chat-avatar interaction chain for the new shell."""
        sounds = {"tap": "拍一拍.mp3", "headpat": "ButtonAll.mp3"}
        filename = sounds.get(action)
        if not filename:
            raise ValueError(f"unsupported avatar action: {action}")
        from config import get_chat_avatar_config, get_user_name
        cfg = get_chat_avatar_config()
        if not cfg.get("interactions_enabled", True):
            return {"action": action, "accepted": False, "message": "头像互动已在设置中关闭"}
        with self._avatar_lock:
            now = time.monotonic()
            cooldown = float(cfg.get("tap_cooldown_seconds", 1.5) or 1.5)
            if self._avatar_busy or now - self._avatar_last_trigger < cooldown:
                return {"action": action, "accepted": False, "message": "互动冷却中，请稍等一下"}
            self._avatar_busy = True
            self._avatar_last_trigger = now
            if now - self._avatar_last_tap > 8:
                self._avatar_streak = 0
            self._avatar_streak += 1
            self._avatar_last_tap = now
        try:
            from utils.sound import play_sound
            play_sound(filename)
        except Exception as exc:
            sound_ok = False
            sound_error = str(exc)
        else:
            sound_ok = True
            sound_error = ""

        counter_action = ""
        if bool(cfg.get("counter_tap", True)) and random.random() < 0.28:
            counter_action = action

        fallback = {
            "tap": ["你拍到我了，我记住啦。", "拍完就不许跑远，我还在这里。"],
            "headpat": ["被你摸到了，今天也稍微陪你久一点。", "好啦好啦，摸到了，继续陪你聊天。"],
        }
        counter_fallback = {
            "tap": ["看到了吧，我也会反手拍回来。别以为只有你会逗我。", "这一下算我的回礼，接住了就不许装作没感觉。"],
            "headpat": ["我也摸回来一下，礼尚往来。现在轮到你乖乖感受了。", "刚才那一下我收到了，所以也轻轻摸回来，不许只占我的便宜。"],
        }
        response = random.choice(counter_fallback[action] if counter_action else fallback[action])
        used_fallback = True
        if cfg.get("dynamic_response", True):
            try:
                from brain.agent import AgentCore
                isolated = AgentCore(disable_tools=True, track_emotion=False, owner_scope=False, source_channel="avatar_interaction")
                recent = getattr(self.agent(), "history", [])[-6:]
                recent_text = "\n".join(f"{m.get('role')}: {str(m.get('content', ''))[:200]}" for m in recent if isinstance(m, dict))
                actor = str(get_user_name() or "主人")
                action_name = "拍一拍" if action == "tap" else "摸一摸"
                if counter_action:
                    prompt = (
                        f"事实不可改变：{actor}刚刚对莲心的头像进行了{action_name}，随后莲心已经反手对{actor}进行了同样的{action_name}。"
                        "请以莲心第一人称，用1到2句自然、口语化的话回应这次反击。"
                        "语气要像轻微调侃、撒娇或亲近的回应，明确体现这是莲心反手回应，而不是普通地被用户拍/摸。"
                        "不要写成‘你拍我’‘被你摸’等把莲心写成再次被动接受动作的表达，不要反转动作方向。"
                    )
                else:
                    prompt = (
                        f"{actor}刚刚对莲心的头像进行了{action_name}。"
                        "请以莲心第一人称，用1到2句自然、口语化的话回应被互动的感受。"
                        "不要提模型、系统、工具或提示词，不要否认这次互动，不要反转动作方向。"
                    )
                prompt += f"最近对话上下文：{recent_text}"
                generated = (isolated.chat(prompt, disable_tools=True) or "").strip()
                invalid = ("api", "调用失败", "请求失败", "系统错误", "不要提")
                if generated and not any(marker in generated.lower() for marker in invalid):
                    response = generated[:180]
                    used_fallback = False
            except Exception:
                pass
        try:
            from utils.accompany_stats import AccompanyStats
            if self._avatar_stats is None:
                self._avatar_stats = AccompanyStats()
            self._avatar_stats.record_avatar_detail(
                "user_tap" if action == "tap" else "user_headpat",
                actor="user", target="assistant", source="user", reaction=action,
                sound=sound_ok, llm=not used_fallback, fallback=used_fallback,
                streak=self._avatar_streak, context={"source": "react-chat-avatar"},
            )
            if counter_action:
                self._avatar_stats.record_avatar_detail(
                    "counter_tap" if action == "tap" else "counter_headpat",
                    actor="assistant", target="user", source="counter", reaction=action,
                    streak=self._avatar_streak, context={"source": "react-chat-avatar"},
                )
        except Exception:
            pass
        with self._avatar_lock:
            self._avatar_busy = False
        result = {"action": action, "accepted": True, "sound": sound_ok, "response": response, "counterAction": counter_action}
        if sound_error:
            result["soundError"] = sound_error
        return result

    # ---------- ?????????/??????? QSettings ??? ----------
    def _music_space_wallpapers(self) -> list:
        try:
            from utils.resource_path import get_asset_path
            directory = get_asset_path("\u4e3b\u754c\u9762\u80cc\u666f\u56fe")
        except Exception:
            directory = None
        items = [{"id": "default", "name": "\u9ed8\u8ba4\u58c1\u7eb8", "url": ""}]
        if directory is not None and directory.is_dir():
            allowed = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
            try:
                files = sorted(directory.iterdir(), key=lambda p: p.name.lower())
            except Exception:
                files = []
            for image in files:
                try:
                    if image.is_file() and image.suffix.lower() in allowed:
                        resolved = image.resolve()
                        portable_id = _music_asset_wallpaper_id(image.name)
                        items.append({"id": portable_id, "name": image.stem,
                                      "url": _music_wallpaper_url(portable_id)})
                except Exception:
                    continue
        return items

    def _music_space_settings(self) -> dict:
        try:
            from PyQt5.QtCore import QSettings
            settings = QSettings("Lianxin", "MusicBox")
        except Exception:
            settings = None
        current = str(settings.value("space_wallpaper", "default")) if settings is not None else "default"
        wallpapers = self._music_space_wallpapers()
        known = {item["id"] for item in wallpapers}
        if current != "default" and current not in known:
            portable = _music_portable_wallpaper_id(current, wallpapers)
            if portable:
                # 旧版存的是绝对路径：迁移成可移植 id，换目录/换机器后不再丢壁纸
                current = portable
            else:
                custom = Path(current)
                if custom.is_file():
                    current = _music_file_wallpaper_id(custom)
                    wallpapers.append({"id": current, "name": f"自定义：{custom.stem}",
                                       "url": _music_wallpaper_url(current)})
        try:
            opacity = float(settings.value("space_wallpaper_opacity", 0.7)) if settings is not None else 0.7
        except (TypeError, ValueError):
            opacity = 0.75
        try:
            mask = float(settings.value("space_content_mask_opacity", 0.5)) if settings is not None else 0.5
        except (TypeError, ValueError):
            mask = 0.82
        return {
            "wallpapers": wallpapers,
            "settings": {
                "wallpaper": current,
                "wallpaper_opacity": opacity,
                "content_mask_opacity": mask,
                "fit": str(settings.value("space_wallpaper_fit", "cover")) if settings is not None else "cover",
            },
        }

    def save_music_space_settings(self, wallpaper, wallpaper_opacity, content_mask_opacity, fit) -> dict:
        try:
            from PyQt5.QtCore import QSettings
            settings = QSettings("Lianxin", "MusicBox")
        except Exception:
            return self._music_space_settings()
        raw_id = str(wallpaper or "default")
        if raw_id == "default":
            path = "default"
        elif raw_id.startswith((_MUSIC_WALLPAPER_ASSET_PREFIX, _MUSIC_WALLPAPER_FILE_PREFIX)):
            path = raw_id if _music_wallpaper_path(raw_id) else "default"
        else:
            # 旧版前端可能直接传绝对路径：优先映射回内置壁纸 id
            portable = _music_portable_wallpaper_id(raw_id, self._music_space_wallpapers())
            if portable:
                path = portable
            else:
                candidate = _music_wallpaper_path(raw_id)
                path = _music_file_wallpaper_id(candidate) if candidate else "default"
        settings.setValue("space_wallpaper", path)
        settings.setValue("space_wallpaper_opacity", max(0.0, min(1.0, float(wallpaper_opacity))))
        settings.setValue("space_content_mask_opacity", max(0.0, min(1.0, float(content_mask_opacity))))
        settings.setValue("space_wallpaper_fit", "contain" if str(fit) == "contain" else "cover")
        settings.sync()
        return self._music_space_settings()

    def _proxy_cover_url(self, url: str) -> str:
        """?? URL ??????? URL ?? 8766 ?????Tauri CSP ??????"""
        if not url:
            return ""
        if url.startswith(("file://", "data:", "http://127.0.0.1", "http://localhost")):
            return url
        return "http://127.0.0.1:8766/api/music/cover?url=" + quote(url, safe="")

    @staticmethod
    def _track_cover_url(track: dict) -> str:
        """Extract the cover from the different shapes returned by NetEase APIs."""
        if not isinstance(track, dict):
            return ""
        album = track.get("album") if isinstance(track.get("album"), dict) else {}
        return str(
            track.get("coverUrl")
            or track.get("cover_url")
            or track.get("picUrl")
            or track.get("albumPicUrl")
            or track.get("coverImgUrl")
            or album.get("picUrl")
            or album.get("coverUrl")
            or ""
        )

    def music_state(self) -> dict:
        # Prefer the real 8765 player API; the state file remains a safe fallback.
        provider = getattr(self, "_music_state_provider", None)
        if provider is not None:
            try:
                unified = provider.build(
                    cover_resolver=self._proxy_cover_url,
                    playlist_cover_resolver=self._proxy_cover_url,
                )
            except Exception:
                unified = None
            if unified is not None:
                self._log_current_song(unified)
                return unified
        try:
            import urllib.request
            with urllib.request.urlopen("http://127.0.0.1:8765/api/status", timeout=1.5) as response:
                status = json.loads(response.read().decode("utf-8"))
            st = status.get("status") or {}
            playback = status.get("playback") or {}
            listening = status.get("listening") or {}
            lyrics_raw = listening.get("lyrics") if isinstance(listening, dict) else None
            lyrics = []
            if isinstance(lyrics_raw, list):
                for ln in lyrics_raw:
                    if not isinstance(ln, dict):
                        continue
                    txt = str(ln.get("text") or "").strip()
                    if not txt:
                        continue
                    try:
                        t_sec = float(ln.get("time"))
                    except (TypeError, ValueError):
                        t_sec = 0.0
                    lyrics.append({"time": t_sec, "text": txt})
                lyrics.sort(key=lambda x: x["time"])
            style = str(listening.get("style") or "") if isinstance(listening, dict) else ""
            queue_info = {"queue": [], "index": -1, "mode": "sequence"}
            try:
                with urllib.request.urlopen("http://127.0.0.1:8765/api/queue", timeout=1.5) as qr:
                    queue_data = json.loads(qr.read().decode("utf-8"))
                queue_info = queue_data or queue_info
            except Exception:
                pass
            q = queue_info.get("queue") or []
            index = int(queue_info.get("index", -1) or -1)
            mode = str(queue_info.get("mode") or "sequence")
            playlist = []
            for i, t in enumerate(q):
                try:
                    dur = float(t.get("durationMs") or 0) / 1000.0
                except (TypeError, ValueError):
                    dur = 0.0
                playlist.append({
                    "id": t.get("id") or "",
                    "title": t.get("name") or "未知曲目",
                    "artist": t.get("artist") or "",
                    "duration": dur,
                    "index": i,
                    "coverUrl": self._proxy_cover_url(self._track_cover_url(t)),
                })
            duration = float(st.get("duration") or 0)
            if not duration and playback.get("durationMs"):
                duration = float(playback.get("durationMs")) / 1000.0
            placeholder_set = {"纯音乐，请欣赏", "暂无歌词", "纯音乐", "（暂无歌词）"}
            has_lyric = any(x["text"] not in placeholder_set for x in lyrics)
            try:
                _title = str(playback.get("name") or "").strip()
                _song_id = str(playback.get("id") or playback.get("songId") or "").strip()
                _current = (_title, _song_id)
                if _current != getattr(self, "_watched_song", None) and _title:
                    _state_text = "播放中" if st.get("playing") else "已暂停"
                    bridge_log.log("MusicWatcher", f"当前歌曲: {_title} id={_song_id} [{_state_text}]")
                    self._watched_song = _current
            except Exception:
                pass
            current_track = {
                "id": playback.get("id") or playback.get("songId") or "",
                "title": playback.get("name") or "",
                "artist": playback.get("artist") or "",
                "album": playback.get("album") or "",
                "coverUrl": self._proxy_cover_url(
                    self._track_cover_url(playback)
                    or (playlist[index].get("coverUrl") if 0 <= index < len(playlist) else "")
                ),
            }
            if not (0 <= index < len(playlist)):
                playback_id = str(playback.get("id") or playback.get("songId") or "")
                index = next(
                    (i for i, item in enumerate(playlist) if str(item.get("id") or "") == playback_id),
                    -1,
                )
            cover_url = self._track_cover_url(playback)
            if not cover_url and 0 <= index < len(playlist):
                cover_url = playlist[index].get("coverUrl") or ""
            return {
                "serviceOnline": True, "loggedIn": status.get("loggedIn"),
                "stateVersion": status.get("stateVersion", 0),
                "queueAvailable": bool(playlist), "currentTrack": current_track,
                "error": "", "updatedAt": time.time(),
                "active": bool(st.get("playing")) and not bool(st.get("paused")),
                "playing": bool(st.get("playing")), "paused": bool(st.get("paused")),
                "name": playback.get("name") or "", "title": playback.get("name") or "",
                "artist": playback.get("artist") or "", "album": playback.get("album") or "",
                "coverUrl": self._proxy_cover_url(cover_url),
                "progress": float(st.get("position") or 0), "duration": duration,
                "volume": float(st.get("volume") or 0), "source": "netease-8765",
                "id": current_track.get("id") or "",
                "songId": current_track.get("id") or "",
                "startedAt": playback.get("startedAt") or "",
                "playlist": playlist, "current_index": index, "mode": mode,
                "lyrics": lyrics, "style": style, "instrumental": not has_lyric,
            }
        except Exception:
            pass
        from brain.music_state import build_state, legacy_payloads, offline_state
        try:
            from utils.paths import get_netease_state_file_candidates
            candidates = get_netease_state_file_candidates()
        except Exception:
            candidates = []
        for path in candidates:
            try:
                if path.exists():
                    status, queue = legacy_payloads(json.loads(path.read_text(encoding="utf-8")))
                    state = build_state(
                        status, queue,
                        cover_resolver=self._proxy_cover_url,
                        playlist_cover_resolver=self._proxy_cover_url,
                    )
                    state["serviceOnline"] = False
                    state["source"] = "netease-state-file"
                    return state
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        return offline_state("播放器服务离线")

    def _log_current_song(self, state: dict) -> None:
        try:
            title = str(state.get("name") or "").strip()
            song_id = str(state.get("id") or "").strip()
            current = (title, song_id)
            if current != getattr(self, "_watched_song", None) and title:
                text = "播放中" if state.get("active") else "已暂停"
                bridge_log.log("MusicWatcher", f"当前歌曲: {title} id={song_id} [{text}]")
                self._watched_song = current
        except Exception:
            pass

    @staticmethod
    def _normalize_music_state(state: dict) -> dict:
        result = dict(state or {})
        result.setdefault("serviceOnline", bool(result.get("source")))
        result.setdefault("loggedIn", None)
        result.setdefault("playlist", [])
        result.setdefault("queueAvailable", bool(result["playlist"]))
        result.setdefault("error", "")
        result.setdefault("updatedAt", time.time())
        result.setdefault("name", result.get("title") or "")
        result.setdefault("title", result.get("name") or "")
        result.setdefault("currentTrack", {
            "id": result.get("id") or result.get("songId") or "",
            "title": result.get("name") or result.get("title") or "",
            "artist": result.get("artist") or "",
            "album": result.get("album") or "",
        })
        return result

    # ── 网易云 Web 播放器（8765）：判活与拉起 ─────────────────────────────
    NETEASE_PORT = 8765
    NETEASE_SPAWN_TIMEOUT = 8.0
    NETEASE_FAIL_STREAK_LIMIT = 3
    NETEASE_COOLDOWN_SECONDS = 300.0

    def _netease_port_listening(self, timeout: float = 0.5) -> bool:
        """端口级判活：只看 127.0.0.1:8765 上有没有监听者，不依赖对端是否及时应答。"""
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                return sock.connect_ex(("127.0.0.1", self.NETEASE_PORT)) == 0
        except Exception:
            return False

    def _netease_probe(self, path: str, timeout: float) -> str:
        """探测一次 8765 的接口。

        返回三态，便于区分"路由不存在"和"对端压根不应答"：
        - "ok"           对端给了成功响应
        - "missing"      对端应答了但路由不可用（例如旧版 server.js 没有 /api/ping）
        - "unreachable"  超时/连接失败（对端卡住或不是 HTTP 服务）
        """
        import urllib.error
        import urllib.request
        url = f"http://127.0.0.1:{self.NETEASE_PORT}{path}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError:
            return "missing"
        except Exception:
            return "unreachable"
        if path == "/api/ping":
            try:
                return "ok" if bool(json.loads(body.decode("utf-8") or "{}").get("ok", True)) else "missing"
            except Exception:
                # 内容不是 JSON 但确实应答了，按旧版/异常实现处理
                return "ok"
        return "ok"

    def _netease_online(self) -> bool:
        """两层判活：先看端口，再打不碰 mpv 的 /api/ping。

        /api/status 会去问 mpv IPC（最坏 ~3s），超时太短会把健康服务误判成离线，
        所以只在"确认是旧版服务"时才退回它。
        """
        if not self._netease_port_listening():
            return False
        ping = self._netease_probe("/api/ping", 1.2)
        if ping == "ok":
            return True
        if ping == "missing":
            return self._netease_probe("/api/status", 2.0) == "ok"
        # unreachable：对端占用端口却不应答，没有必要再去问 /api/status
        return False

    def _netease_web_player_log(self):
        """把播放器子进程的 stdout/stderr 落盘，避免再出现“失败原因不可考”。"""
        try:
            log_dir = Path(__file__).resolve().parent / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            return open(log_dir / "netease_web_player.log", "ab", buffering=0)
        except Exception:
            return subprocess.DEVNULL

    def _reap_netease_proc(self) -> None:
        """回收上一次未就绪的播放器进程，避免每次失败都留下孤儿 node。"""
        proc = self._netease_proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                proc.kill()
                bridge_log.log("网易云", f"回收上一次未就绪的播放器进程 (PID {proc.pid})")
            except Exception:
                pass
        self._netease_proc = None

    def _spawn_netease_web_player(self) -> bool:
        """仅在端口无人监听时拉起独立 Web 播放器进程。"""
        self._reap_netease_proc()
        try:
            server_js = (
                Path(__file__).resolve().parent
                / "参考项目" / "netease-music-mcp-main" / "src" / "server.js"
            )
            cwd = server_js.parent.parent
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            sink = self._netease_web_player_log()
            proc = subprocess.Popen(
                ["node", str(server_js), "--web-player", "--port", str(self.NETEASE_PORT)],
                cwd=str(cwd),
                stdout=sink,
                stderr=sink,
                creationflags=flags,
            )
            self._netease_proc = proc
            try:
                from utils.child_kill_job import assign_pid_to_kill_job
                assign_pid_to_kill_job(proc.pid)
            except Exception:
                pass
            self._netease_last_spawn_error = ""
            bridge_log.log("网易云", f"8765 不在线，自动拉起 netease-music-mcp (PID {proc.pid})")
            return True
        except Exception as exc:
            self._netease_last_spawn_error = str(exc)
            bridge_log.log("网易云", f"拉起 8765 失败: {exc}")
            return False

    def _netease_spawn_diagnosis(self) -> str:
        """给日志和报错提供可执行的定位信息。"""
        parts = []
        proc = self._netease_proc
        if proc is None:
            parts.append("未新增子进程")
        else:
            rc = proc.poll()
            parts.append(
                f"子进程仍在启动中 (PID {proc.pid})" if rc is None
                else f"子进程已退出 rc={rc} (PID {proc.pid})"
            )
        parts.append("端口已被占用" if self._netease_port_listening() else "端口无人监听")
        if self._netease_last_spawn_error:
            parts.append(f"spawn 异常: {self._netease_last_spawn_error}")
        return "；".join(parts)

    def _netease_unavailable_message(self) -> str:
        now = time.time()
        if now < self._netease_cooldown_until:
            remaining = int(self._netease_cooldown_until - now)
            return f"网易云播放器暂时不可用：自动拉起连续失败，已进入冷却（约 {remaining}s 后可重试）"
        return f"网易云播放器暂时不可用：{self._netease_spawn_diagnosis()}（详见 logs/netease_web_player.log）"

    def ensure_netease_online(self, timeout: float = NETEASE_SPAWN_TIMEOUT) -> bool:
        """确保 8765 在线；不在线时自动拉起（幂等 + 失败熔断）。

        只有“端口确实没人监听”才会 spawn，避免反复拉起、每次超时、还留下孤儿 node。
        timeout 是本次调用允许消耗的总预算（包含判活），调用方因此不会被拖住。
        """
        now = time.time()
        # 冷却期内若端口仍是空的，直接快速失败：不再浪费 8s 去探测/拉起；
        # 日志最多每 30s 一条，避免前端轮询把日志刷爆。
        if now < self._netease_cooldown_until and not self._netease_port_listening():
            if now - self._netease_cooldown_logged_at > 30:
                self._netease_cooldown_logged_at = now
                bridge_log.log(
                    "网易云",
                    f"拉起处于冷却中（剩余 {int(self._netease_cooldown_until - now)}s），跳过自动拉起",
                )
            return False
        deadline = time.time() + timeout
        if self._netease_online():
            return True
        with self._netease_spawn_lock:
            if time.time() < deadline and self._netease_online():
                return True
            now = time.time()
            if now < self._netease_cooldown_until:
                bridge_log.log(
                    "网易云",
                    f"拉起处于冷却中（剩余 {int(self._netease_cooldown_until - now)}s），跳过自动拉起",
                )
                return False
            if self._netease_port_listening():
                bridge_log.log(
                    "网易云",
                    "8765 端口已被监听但未就绪，等待其完成启动（不重复拉起）",
                )
            elif not self._spawn_netease_web_player():
                return False
            while time.time() < deadline:
                if self._netease_online():
                    self._netease_fail_streak = 0
                    self._netease_cooldown_until = 0.0
                    bridge_log.log("网易云", f"8765 已就绪（端口 {self.NETEASE_PORT}）")
                    return True
                time.sleep(0.3)
            self._netease_fail_streak += 1
            bridge_log.log(
                "网易云",
                f"8765 拉起超时（连续第 {self._netease_fail_streak} 次）｜{self._netease_spawn_diagnosis()}",
            )
            if self._netease_fail_streak >= self.NETEASE_FAIL_STREAK_LIMIT:
                self._netease_cooldown_until = time.time() + self.NETEASE_COOLDOWN_SECONDS
                bridge_log.log(
                    "网易云",
                    f"连续 {self._netease_fail_streak} 次拉起失败，进入 "
                    f"{int(self.NETEASE_COOLDOWN_SECONDS)}s 冷却；音乐操作将直接返回可读提示"
                    "（子进程日志见 logs/netease_web_player.log）",
                )
            return False

    def music_ensure(self) -> dict:
        return {"online": self.ensure_netease_online(), "url": "http://127.0.0.1:8765/"}

    def music_stats(self) -> dict:
        return self.music_service.stats()

    def music_playlists(self, offset: int = 0, limit: int = 24) -> dict:
        import urllib.request
        query = f"?offset={max(0, int(offset))}&limit={max(1, min(50, int(limit)))}"
        with urllib.request.urlopen("http://127.0.0.1:8765/api/playlists" + query, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def music_playlist(self, playlist_id: str) -> dict:
        import urllib.request
        if not str(playlist_id).strip():
            raise ValueError("歌单 ID 不能为空")
        url = "http://127.0.0.1:8765/api/playlist?id=" + quote(str(playlist_id), safe="")
        with urllib.request.urlopen(url, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def music_play_playlist(self, playlist_id: str) -> dict:
        import urllib.request
        detail = self.music_playlist(playlist_id)
        raw_tracks = detail.get("tracks") or (detail.get("playlist") or {}).get("tracks") or []
        tracks = []
        for track in raw_tracks:
            if not isinstance(track, dict) or not track.get("id"):
                continue
            tracks.append({
                "id": str(track.get("id")),
                "name": track.get("name") or track.get("title") or "未知歌曲",
                "artist": track.get("artist") or "",
                "album": track.get("album") or "",
                "durationMs": track.get("durationMs") or track.get("duration") or 0,
                "coverUrl": self._track_cover_url(track),
            })
        if not tracks:
            raise ValueError("歌单中没有可播放歌曲")
        base = "http://127.0.0.1:8765"
        headers = {"Content-Type": "application/json", "X-Lianxin-Bridge": "1"}
        queue_request = urllib.request.Request(
            base + "/api/queue",
            data=json.dumps({"tracks": tracks, "index": 0}).encode("utf-8"),
            headers=headers, method="POST",
        )
        with urllib.request.urlopen(queue_request, timeout=8) as response:
            response.read()
        play_request = urllib.request.Request(
            base + "/api/play-track",
            data=json.dumps({"id": tracks[0]["id"]}).encode("utf-8"),
            headers=headers, method="POST",
        )
        with urllib.request.urlopen(play_request, timeout=8) as response:
            response.read()
        deadline = time.time() + 8.0
        state = self.music_state()
        while time.time() < deadline:
            queue = state.get("playlist") or []
            expected_ids = [str(track["id"]) for track in tracks]
            actual_ids = [str(item.get("id") or "") for item in queue]
            if actual_ids == expected_ids:
                return state
            time.sleep(0.25)
            state = self.music_state()
        actual_ids = [str(item.get("id") or "") for item in (state.get("playlist") or [])]
        raise TimeoutError(
            f"播放队列同步超时：期望 {len(expected_ids)} 首，实际 {len(actual_ids)} 首，歌曲顺序未确认"
        )

    def _music_user_recently_active(self) -> bool:
        """最近 5 分钟内用户是否发过消息（听歌反馈据此决定能不能提问）。"""
        try:
            return bool(self.get_proactive_scheduler().user_active_within(300))
        except Exception:
            return False

    def start_music_watcher(self) -> dict:
        with self._music_watcher_lock:
            if self._music_watcher is not None:
                return {"running": True}
            from brain.music_watcher import MusicWatcher
            watcher = MusicWatcher(
                state_provider=self.music_service.state,
                on_feedback_meta=self._on_music_feedback,
                on_status=self._on_music_status,
                enabled_check=lambda: bool(self.get_proactive_scheduler().music_feedback_enabled),
                feedback_delay=float(self.get_proactive_scheduler()._settings.get("music_feedback_delay_seconds", 15)),
                listen_min_seconds=float(self.get_proactive_scheduler()._settings.get("music_feedback_min_seconds", 10)),
                min_interval_seconds=float(self.get_proactive_scheduler()._settings.get("music_feedback_cooldown_seconds", 120)),
                tracker=self.music_service.playback_tracker,
                stats=self.music_service.stats_store,
                song_cooldown_seconds=float(self.get_proactive_scheduler()._settings.get("music_feedback_song_cooldown_seconds", 600)),
                user_active_check=self._music_user_recently_active,
            )
            watcher.start()
            self._music_watcher = watcher
            return {"running": True}

    def stop_music_watcher(self) -> dict:
        with self._music_watcher_lock:
            watcher = self._music_watcher
            self._music_watcher = None
        if watcher is not None:
            watcher.stop()
        try:
            self.music_service.playback_tracker.stop()
        except Exception:
            pass
        return {"running": False}

    def start_music_state(self) -> dict:
        provider = getattr(self, "_music_state_provider", None)
        if provider is None:
            return {"running": False}
        provider.start()
        return {"running": True}

    def stop_music_state(self) -> dict:
        provider = getattr(self, "_music_state_provider", None)
        if provider is not None:
            provider.stop()
        return {"running": False}

    def _speak_feedback(self, text: str) -> None:
        """后台朗读听歌反馈；失败只记日志，不影响反馈主流程。"""
        try:
            import pygame
            if not pygame.mixer.get_init():
                pygame.mixer.init()
        except Exception:
            pass
        try:
            self.speak(str(text))
        except Exception as exc:
            bridge_log.log("音乐", f"听歌反馈朗读失败: {exc}")

    def _on_music_feedback(self, text: str, meta: dict | None = None) -> None:
        content = str(text or "").strip()
        if not content:
            return
        scheduler = self.get_proactive_scheduler()
        settings = scheduler._settings
        message_id = None
        if bool(settings.get("music_feedback_save_to_chat", True)):
            try:
                agent = self.agent()
                message_id = agent.get_history_manager().save_message(
                    agent._session_id, "assistant", content,
                )
            except Exception as exc:
                bridge_log.log("音乐", f"听歌反馈写入会话失败: {exc}")
        if bool(settings.get("music_feedback_auto_speak", False)):
            threading.Thread(target=self._speak_feedback, args=(content,), daemon=True).start()
        event = {
            "id": time.time_ns(),
            "type": "music.feedback_ready",
            "content": content,
            "messageId": message_id,
            "trackId": (meta or {}).get("track_id") or "",
            "track": (meta or {}).get("name") or "",
        }
        with self._music_watcher_lock:
            self._music_events.append(event)

    def _on_music_status(self, status: str, state: dict) -> None:
        event = {
            "id": time.time_ns(),
            "type": f"music.feedback_{status}",
            "status": status,
            "track": state.get("name") or state.get("title") or "",
        }
        with self._music_watcher_lock:
            self._music_events.append(event)

    def tool_events(self, after: int = 0) -> dict:
        """莲心工具活动事件流（内存态，重启后清空）。"""
        from brain import tool_activity
        return tool_activity.events_after(after)

    # ── 执行运行时（阶段 1：只读执行引擎）──────────────────────
    def task_runtime(self):
        from brain.runtime import get_task_runtime
        return get_task_runtime()

    def runtime_state(self, probe: bool = False) -> dict:
        """运行时快照；probe=True 时顺带探测执行引擎可用性。"""
        runtime = self.task_runtime()
        return runtime.probe(force=True) if probe else runtime.state()

    def runtime_events(self, after: int = 0) -> dict:
        """运行时事件流（任务开始/结束/审批/熔断/工具调用）。"""
        return self.task_runtime().server_events(after)

    def runtime_start(self, goal: str, session_id=None, turn: int = 0,
                      sandbox: str = "", cwd: str = "", wait: bool = False,
                      resume_thread_id: str = "", network_access: bool = False,
                      goal_contract=None) -> dict:
        if resume_thread_id:
            return {"ok": False, "error": "继续历史任务请使用最新任务卡片，不能绕过任务身份校验"}
        return self.task_runtime().start(
            goal, session_id=session_id, turn=turn, sandbox=sandbox, cwd=cwd, wait=wait,
            resume_thread_id=resume_thread_id,
            network_access=network_access,
            goal_contract=goal_contract,
        )

    @staticmethod
    def runtime_scope(body: dict) -> dict:
        try:
            session_id = int(body.get("sessionId") or 0)
        except (TypeError, ValueError):
            session_id = 0
        return {"session_id": session_id, "task_id": str(body.get("taskId") or ""),
                "thread_id": str(body.get("threadId") or ""),
                "turn_id": str(body.get("turnId") or "")}

    def runtime_steer(self, text: str, scope: dict) -> dict:
        return self.task_runtime().steer(text, scope=scope)

    def runtime_continue(self, body: dict) -> dict:
        return self.task_runtime().continue_task(
            **self.runtime_scope(body), mode=str(body.get("mode") or ""),
            text=str(body.get("text") or ""))

    def runtime_stop(self, reason: str = "user", scope=None) -> dict:
        return self.task_runtime().stop(reason, scope=scope)

    def runtime_approve(self, request_id, decision: str = "accept",
                        remember: bool = False, scope=None) -> dict:
        """回执一次挂起的审批；remember=True 表示"本轮同类都允许"。"""
        return self.task_runtime().approve(request_id, decision, remember=remember, scope=scope)

    # ── 执行引擎终态回灌（阶段 3 · 步骤 2）────────────────────
    # 设计见 docs/莲心执行引擎体验对齐方案.md §3.3：
    # 引擎任务受理即返回，终态（完成/失败/被停）由这里补一条莲心的文字消息回会话。
    # 只落库、不开 TTS、不碰正在进行的对话轮，避免在后台卡住用户下一条消息。
    def install_execution_terminal_sink(self) -> bool:
        try:
            from brain.runtime import get_task_runtime

            return get_task_runtime().on_terminal(self._execution_terminal_sink)
        except Exception as exc:
            bridge_log.log("执行引擎", f"终态回灌注册失败: {exc}")
            return False

    def _execution_terminal_session(self, result: dict) -> int:
        """仅向任务的来源会话回灌，不把来源缺失的旧任务投到当前聊天。"""
        try:
            session_id = int(result.get("sessionId") or 0)
        except (TypeError, ValueError):
            session_id = 0
        return session_id

    @staticmethod
    def _execution_terminal_fallback(result: dict) -> str:
        """转述失败时的兜底文案：不漂亮，但结果不会丢。"""
        goal = str(result.get("goal") or "刚才那件长活")
        status = str(result.get("status") or "")
        if result.get("recovered"):
            return (
                "上次我关掉之前，这件事（%s）还没跑完。"
                "要接着做的话，点一下下面的「接着做」，我会从原来的进度继续。"
                % goal[:60]
            )
        label = {
            "completed": "已经做完了",
            "failed": "没能做成功",
            "interrupted": "被停下来了",
            "stopped": "停下来了",
        }.get(status, "收尾了")
        text = "刚才那件事（%s）%s。" % (goal[:60], label)
        reason = str(result.get("reason") or "").strip()
        if status != "completed" and reason:
            text += "原因：%s。" % reason
        return text

    def _execution_terminal_reply(self, material: str) -> str:
        """用莲心自己的口吻把终态素材改成一句话（单次纯文本调用，不带工具）。"""
        import re as _re

        agent = self.agent()
        persona = str(getattr(agent, "_system_prompt", "") or "")
        scene = (
            "\n\n【本轮场景：执行引擎回执】\n"
            "你之前交给后台执行引擎的那件长活刚刚收尾了，现在要把结果告诉主人。\n"
            "要求：用你平时的口吻，一到三句话；说清做了什么、结果放在哪里、还需不需要他动手；\n"
            "素材里写着没有正常完成时，就如实说没做成，不要假装目标已经达成；\n"
            "不要出现工具名、命令行、任务号、日志、报错原文或字段名，不要照抄素材；\n"
            "本轮只输出这段要对主人说的话本身，不要输出任何标签。"
        )
        messages = [{"role": "system", "content": (persona + scene) if persona else scene.strip()}]
        messages.append({"role": "user", "content": material})
        response = agent._call_api_with_retry(messages=messages, max_retries=2)
        try:
            content = response.choices[0].message.content
        except Exception:
            content = ""
        text = str(content or "").strip()
        # 人格 prompt 要求结尾带【表情：…】，这条回灌消息只要正文。
        text = _re.sub(r"[\s]*【(?:表情|情绪)：[^】]*】[\s]*$", "", text).strip()
        # 回灌只是一句告知，别让模型写成长报告。
        return text[:600]

    # 回灌通道的宽限期：工具可能刚好把同一个终态同步带回本轮对话
    # （探测窗口内就收尾的秒级任务），等它一句"我拿走了"再决定要不要播报。
    EXECUTION_SINK_GRACE_SECONDS = 2.0

    @staticmethod
    def _execution_card_status_text(result: dict) -> str:
        """同步路径的卡片正文：只陈述事实，不重复莲心刚在对话里说过的话。"""
        label = {
            "completed": "做完了",
            "failed": "没做成",
            "interrupted": "被停下来了",
            "stopped": "停下来了",
        }.get(str(result.get("status") or ""), "收尾了")
        return "执行引擎这边%s：%s 轮动作，共 %s 秒。" % (
            label,
            int(result.get("rounds") or 0),
            result.get("elapsedSeconds") or 0,
        )

    def _save_execution_card(self, session_id: int, result: dict, text: str,
                             narrated: bool) -> int:
        """把一次执行任务落成会话里的一条卡片消息（复用 messages + kind 元数据）。

        metadata 会被 ``HistoryManager._decode_message`` 平铺成消息字段，
        所以统一加 execution 前缀，避免与既有字段撞名（如 source / goal）。
        """
        from brain.tools import clean_execution_summary

        metadata = {
            "kind": "execution_task",
            "executionTaskId": str(result.get("taskId") or ""),
            "executionThreadId": str(result.get("threadId") or ""),
            "executionTurnId": str(result.get("turnId") or ""),
            "executionStatus": str(result.get("status") or ""),
            "executionGoal": str(result.get("goal") or ""),
            "executionRounds": int(result.get("rounds") or 0),
            "executionElapsed": result.get("elapsedSeconds") or 0,
            "executionReason": str(result.get("reason") or ""),
            "executionApprovalReason": str((result.get("lastApproval") or {}).get("reason") or ""),
            "executionSummary": clean_execution_summary(result.get("summary") or "", limit=1200),
            "executionNarrated": bool(narrated),
        }
        history = self.agent().get_history_manager()
        message_id = history.save_message(
            session_id, "assistant", str(text or "").strip(), metadata=metadata,
        )
        bridge_log.log(
            "执行引擎",
            "执行卡片已落库 会话 %s 消息 #%s（%s）" % (
                session_id, message_id, str(result.get("status") or ""),
            ),
        )
        thread_id = str(result.get("threadId") or "")
        if thread_id:
            try:
                from brain.runtime import session_store

                session_store.update_meta(thread_id, {
                    "cardSaved": True, "cardMessageId": int(message_id or 0),
                })
            except Exception:
                pass
        return message_id

    def _execution_terminal_sink(self, result: dict) -> None:
        """在后台线程里执行：先落执行卡片；本轮没有同步播报过时再补一句。"""
        try:
            time.sleep(self.EXECUTION_SINK_GRACE_SECONDS)
            from brain.runtime import get_task_runtime

            delivered = get_task_runtime().was_delivered(str(result.get("taskId") or ""))
            session_id = self._execution_terminal_session(result)
            if not session_id:
                bridge_log.log("执行引擎", "终态落库跳过：没有可用会话")
                return
            text = ""
            if not delivered:
                if result.get("skipNarration"):
                    # 启动恢复出来的「非预期中断」只陈述事实：不在启动时额外调用模型。
                    text = self._execution_terminal_fallback(result)
                else:
                    # 这张卡就是这次任务的"在场证明"，所以正文用莲心的口吻说。
                    from brain.tools import format_execution_terminal

                    try:
                        text = self._execution_terminal_reply(format_execution_terminal(result))
                    except Exception as exc:
                        bridge_log.log("执行引擎", f"终态转述失败，改用兜底文案: {exc}")
                    text = text or self._execution_terminal_fallback(result)
            if not text:
                text = self._execution_card_status_text(result)
            narrated = (not delivered) and not bool(result.get("skipNarration"))
            self._save_execution_card(session_id, result, text, narrated=narrated)
        except Exception as exc:
            bridge_log.log("执行引擎", f"执行卡片落库失败: {exc}")

    def music_events(self, after: int = 0) -> dict:
        with self._music_watcher_lock:
            events = [event for event in self._music_events if int(event["id"]) > int(after)]
        return {"items": events, "latest": events[-1]["id"] if events else int(after)}

    def music_feedback_settings(self) -> dict:
        scheduler = self.get_proactive_scheduler()
        return {
            "enabled": bool(scheduler.music_feedback_enabled),
            "delaySeconds": int(scheduler._settings.get("music_feedback_delay_seconds", 15)),
            "minimumListenSeconds": int(scheduler._settings.get("music_feedback_min_seconds", 10)),
            "cooldownSeconds": int(scheduler._settings.get("music_feedback_cooldown_seconds", 120)),
            "songCooldownSeconds": int(scheduler._settings.get("music_feedback_song_cooldown_seconds", 600)),
            "autoSpeak": bool(scheduler._settings.get("music_feedback_auto_speak", False)),
            "saveToChat": bool(scheduler._settings.get("music_feedback_save_to_chat", True)),
        }

    def save_music_feedback_settings(self, payload: dict) -> dict:
        scheduler = self.get_proactive_scheduler()
        if "enabled" in payload:
            scheduler.music_feedback_enabled = bool(payload["enabled"])
        mapping = {
            "delaySeconds": ("music_feedback_delay_seconds", 3, 300),
            "minimumListenSeconds": ("music_feedback_min_seconds", 1, 600),
            "cooldownSeconds": ("music_feedback_cooldown_seconds", 10, 3600),
            "songCooldownSeconds": ("music_feedback_song_cooldown_seconds", 10, 86400),
        }
        for key, (target, lower, upper) in mapping.items():
            if key in payload:
                scheduler._settings[target] = max(lower, min(upper, int(payload[key])))
        for key, target in (("autoSpeak", "music_feedback_auto_speak"), ("saveToChat", "music_feedback_save_to_chat")):
            if key in payload:
                scheduler._settings[target] = bool(payload[key])
        scheduler.save_settings()
        with self._music_watcher_lock:
            watcher = self._music_watcher
        if watcher is not None:
            self.stop_music_watcher()
            self.start_music_watcher()
        return self.music_feedback_settings()

    def open_web_player(self) -> dict:
        """确保网易云 Web 播放器在线，并在系统默认浏览器中打开 8765 页面。"""
        import webbrowser
        url = "http://127.0.0.1:8765/"
        online = False
        try:
            online = self.ensure_netease_online(timeout=8)
        except Exception as exc:
            bridge_log.log("网易云", f"确保 8765 在线时异常: {exc}")
        try:
            webbrowser.open(url)
        except Exception as exc:
            bridge_log.log("网易云", f"打开 Web 播放器失败: {exc}")
            return {"ok": False, "online": online, "url": url, "error": str(exc)}
        bridge_log.log("网易云", f"已在系统浏览器打开 Web 播放器 {url} (8765 online={online})")
        return {"ok": True, "online": online, "url": url}

    def music_control(self, action: str, payload: dict | None = None) -> dict:
        import urllib.request
        base = "http://127.0.0.1:8765"
        payload = payload or {}
        if action != "kill-mpv" and not self.ensure_netease_online():
            raise ValueError(self._netease_unavailable_message())
        if action in {"next", "previous"}:
            return self._music_skip_unplayable(action, payload)
        if action in {"queue-append", "queue-insert-next"}:
            return self._music_queue_operation(action, payload)
        if action == "select":
            try:
                index = int(payload.get("index", -1) or -1)
            except (TypeError, ValueError):
                index = -1
            track_id = None
            try:
                with urllib.request.urlopen(base + "/api/queue", timeout=1.5) as qr:
                    queue_data = json.loads(qr.read().decode("utf-8"))
                q = queue_data.get("queue") or []
                if 0 <= index < len(q):
                    track_id = q[index].get("id")
            except Exception:
                track_id = None
            if not track_id:
                raise ValueError("无法定位要播放的曲目")
            route, body = "/api/play-track", {"id": track_id}
        elif action == "seek":
            try:
                seconds = max(0, int(float(payload.get("position") or 0)))
            except (TypeError, ValueError):
                seconds = 0
            route, body = "/api/seek", {"seconds": seconds}
        elif action == "volume":
            try:
                vol = max(0.0, min(1.0, float(payload.get("volume") or 0)))
            except (TypeError, ValueError):
                vol = 0.8
            route, body = "/api/volume", {"volume": int(round(vol * 100))}
        elif action == "mode":
            m = str(payload.get("mode") or "")
            mode = "single" if m == "single" else ("shuffle" if m == "shuffle" else "sequence")
            route, body = "/api/play-mode", {"mode": mode}
        elif action == "kill-mpv":
            try:
                from utils.net_ease_cleanup import stop_netease_mpv
                stop_netease_mpv()
            except Exception as exc:
                bridge_log.log("网易云", f"kill mpv 失败: {exc}")
            return {"active": False, "playing": False, "paused": False, "source": "netease-8765"}
        else:
            routes = {"toggle": "/api/pause", "play": "/api/pause", "pause": "/api/pause"}
            route = routes.get(action)
            if not route:
                raise ValueError(f"不支持的音乐操作: {action}")
            body = payload
        bridge_log.log("网易云", f"动作: {action} -> {route}")
        request = urllib.request.Request(
            base + route,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Lianxin-Bridge": "1"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            response.read()
        result = self.music_state()
        if action == "mode":
            target = mode
            deadline = time.time() + 2.0
            while result.get("mode") != target and time.time() < deadline:
                time.sleep(0.15)
                result = self.music_state()
            result["modeSyncPending"] = result.get("mode") != target
            result["modeRequested"] = target
        return result

    def _music_queue_operation(self, action: str, payload: dict) -> dict:
        """Append or insert one normalized track without starting playback."""
        import urllib.request
        track = payload.get("track") if isinstance(payload, dict) else None
        if not isinstance(track, dict) or not track.get("id"):
            raise ValueError("缺少要加入队列的歌曲")
        base = "http://127.0.0.1:8765"
        with urllib.request.urlopen(base + "/api/queue", timeout=1.5) as response:
            current = json.loads(response.read().decode("utf-8"))
        queue = list(current.get("queue") or [])
        item = {
            "id": str(track.get("id")),
            "name": track.get("name") or track.get("title") or "未知歌曲",
            "artist": track.get("artist") or "",
            "album": track.get("album") or "",
            "durationMs": track.get("durationMs") or track.get("duration") or 0,
            "coverUrl": self._track_cover_url(track),
        }
        if action == "queue-insert-next":
            index = int(current.get("index", -1) or -1)
            insert_at = max(0, min(len(queue), index + 1))
            queue.insert(insert_at, item)
        else:
            queue.append(item)
        request = urllib.request.Request(
            base + "/api/queue",
            data=json.dumps({"tracks": queue, "index": current.get("index", -1)}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Lianxin-Bridge": "1"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read()
        return self.music_state()

    def _music_skip_unplayable(self, action: str, payload: dict) -> dict:
        """Try directional queue candidates instead of failing on one VIP track."""
        with self._music_control_lock:
            return self._music_skip_unplayable_locked(action, payload)

    def _music_skip_unplayable_locked(self, action: str, payload: dict) -> dict:
        """Run one directional skip while excluding overlapping bridge requests."""
        import urllib.request
        base = "http://127.0.0.1:8765"
        if action == "previous":
            with urllib.request.urlopen(base + "/api/status", timeout=1.5) as response:
                status = json.loads(response.read().decode("utf-8"))
            player_status = status.get("status") or {}
            playback = status.get("playback") or {}
            try:
                position = float(player_status.get("position") or 0)
            except (TypeError, ValueError):
                position = 0.0
            current_id = str(playback.get("id") or playback.get("songId") or "")
            if position > 3 and current_id:
                request = urllib.request.Request(
                    base + "/api/replay-track",
                    data=json.dumps({"id": current_id}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-Lianxin-Bridge": "1"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=8) as response:
                    response.read()
                return self.music_state()
        with urllib.request.urlopen(base + "/api/queue", timeout=1.5) as response:
            queue_data = json.loads(response.read().decode("utf-8"))
        queue = queue_data.get("queue") or []
        if not queue:
            raise ValueError("当前播放队列为空")
        try:
            current = int(queue_data.get("index", -1))
        except (TypeError, ValueError):
            current = -1
        step = 1 if action == "next" else -1
        tried = []
        skipped = []
        for offset in range(1, len(queue) + 1):
            index = (current + step * offset) % len(queue)
            track_id = queue[index].get("id")
            if not track_id:
                continue
            tried.append(track_id)
            request = urllib.request.Request(
                base + "/api/play-track",
                data=json.dumps({"id": track_id}).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-Lianxin-Bridge": "1"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=3) as response:
                    response.read()
                deadline = time.time() + 2.0
                while time.time() < deadline:
                    state = self.music_state()
                    current_id = str((state.get("currentTrack") or {}).get("id") or "")
                    if state.get("active") and (current_id == str(track_id) or state.get("name") == queue[index].get("name")):
                        state["skippedTracks"] = max(0, len(tried) - 1)
                        state["skippedTrackIds"] = skipped
                        return state
                    time.sleep(0.2)
            except Exception as exc:
                skipped.append({
                    "id": str(track_id),
                    "title": queue[index].get("name") or "未知歌曲",
                    "reason": "unavailable",
                })
                bridge_log.log("网易云", f"跳过不可播放歌曲 {track_id}: {exc}")
        raise ValueError(f"未找到可播放的歌曲，已尝试 {len(tried)} 首")

    def voice_state(self) -> dict:
        with self._voice_lock:
            return {"active": bool(self._voice), "state": getattr(self._voice, "state", "STOPPED")}

    def voice_events(self, after: int = 0) -> dict:
        with self._voice_lock:
            events = [event for event in self._voice_events if int(event["id"]) > int(after)]
        return {"items": events, "latest": events[-1]["id"] if events else int(after)}

    def start_voice(self) -> dict:
        with self._voice_lock:
            if self._voice is not None:
                return self.voice_state()
            from brain.voice_duplex import VoiceDuplexManager
            def on_state(state):
                self._voice_events.append({"id": time.time_ns(), "type": "voice.state", "state": state})
            def on_transcript(text):
                self._voice_events.append({"id": time.time_ns(), "type": "voice.transcript", "content": text})
                threading.Thread(target=self._voice_chat, args=(text,), name="lianxin-voice-chat", daemon=True).start()
            def on_stt_ready(ready):
                if ready:
                    self.stop_call_sound()
                self._voice_events.append({"id": time.time_ns(), "type": "voice.stt", "state": "ready" if ready else "error"})
            manager = VoiceDuplexManager(on_state_change=on_state, on_transcript=on_transcript, on_stt_ready=on_stt_ready)
            self.start_call_sound()
            if not manager.start():
                self.stop_call_sound()
                raise RuntimeError("语音输入启动失败：WebRTC VAD 或麦克风不可用")
            self._voice = manager
            return self.voice_state()

    def _voice_chat(self, text: str):
        try:
            self._voice_events.append({"id": time.time_ns(), "type": "voice.reply.started"})
            result = self.chat(text)
            content = str(result.get("message", {}).get("content", "") or "")
            self._voice_events.append({"id": time.time_ns(), "type": "voice.reply", "content": content})
            if content:
                self.speak(content)
        except Exception as exc:
            self._voice_events.append({"id": time.time_ns(), "type": "voice.error", "error": str(exc)})

    def stop_voice(self) -> dict:
        with self._voice_lock:
            if self._voice is not None:
                self._voice.stop()
                self._voice = None
            self.stop_call_sound()
            return self.voice_state()

    def set_voice_mic(self, muted: bool) -> dict:
        with self._voice_lock:
            if self._voice is None:
                return {"active": False, "muted": bool(muted)}
            self._voice.set_muted(bool(muted))
            return {"active": True, "muted": bool(muted)}

    def start_voice_input(self) -> dict:
        with self._voice_lock:
            if self._voice_input is not None:
                return {"active": True}
            from voice.listener import VoiceListener
            listener = VoiceListener()
            self._voice_input = listener

        def _run():
            try:
                audio = listener.record()
                text = listener.transcribe(audio) if len(audio) else ""
                with self._voice_lock:
                    self._voice_events.append({"id": time.time_ns(), "type": "voice.input", "content": text or ""})
            except Exception as exc:
                with self._voice_lock:
                    self._voice_events.append({"id": time.time_ns(), "type": "voice.input", "error": str(exc)})
            finally:
                with self._voice_lock:
                    self._voice_input = None

        threading.Thread(target=_run, daemon=True).start()
        return {"active": True}

    def stop_voice_input(self) -> dict:
        with self._voice_lock:
            if self._voice_input is not None:
                try:
                    self._voice_input.stop()
                except Exception:
                    pass
            return {"active": self._voice_input is not None}

    def proactive(self) -> dict:
        scheduler = self.get_proactive_scheduler()
        return {"desktopEnabled": scheduler.desktop_enabled, "qqEnabled": scheduler.qq_enabled,
                "musicFeedbackEnabled": scheduler.music_feedback_enabled,
                "minIntervalMinutes": scheduler.min_interval_minutes,
                "frequency": scheduler.frequency}

    def get_proactive_scheduler(self):
        with self._lock:
            if self._proactive is None:
                from utils.proactive_chat import ProactiveChatScheduler
                self._proactive = ProactiveChatScheduler()
            return self._proactive

    def set_proactive(self, enabled: bool) -> dict:
        scheduler = self.get_proactive_scheduler()
        scheduler.desktop_enabled = bool(enabled)
        scheduler.save_settings()
        return self.proactive()

    def proactive_runtime(self):
        with self._lock:
            if self._proactive_runtime is None:
                self._proactive_runtime = LianxinProactiveRuntime(self)
            return self._proactive_runtime

    def start_proactive_runtime(self):
        self.proactive_runtime().start()

    def stop_proactive_runtime(self):
        runtime = getattr(self, "_proactive_runtime", None)
        if runtime is not None:
            runtime.stop()

    def proactive_trigger(self, mode: str = "normal", action: str = "") -> dict:
        return self.proactive_runtime().trigger(str(mode), str(action))

    def proactive_status(self) -> dict:
        runtime = getattr(self, "_proactive_runtime", None)
        if runtime is None:
            return {"ready": False, "running": False}
        return runtime.status()

    def bilibili_interaction_state(self) -> dict:
        from utils.bilibili_interaction import get_bilibili_interaction_service
        return get_bilibili_interaction_service().get_state()

    def bilibili_surf_state(self) -> dict:
        from utils.bilibili_history import get_bilibili_history
        from utils.bilibili_surf_policy import SurfDiagnostics
        return {"ok": True, "tags": get_bilibili_history().get_search_tags(), "latest": SurfDiagnostics().latest()}

    def open_bilibili_video(self, bvid: str) -> dict:
        import re
        import webbrowser
        if not isinstance(bvid, str) or not re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid):
            return {"ok": False, "error": "视频编号无效，没有打开浏览器。"}
        url = f"https://www.bilibili.com/video/{bvid}"
        try:
            if webbrowser.open(url, new=2):
                return {"ok": True, "url": url}
        except Exception as error:
            bridge_log.log("B站冲浪", f"打开系统浏览器失败: {type(error).__name__}")
        return {"ok": False, "error": "系统浏览器未能打开，请复制视频链接后手动打开。"}

    def save_bilibili_surf_tag(self, payload: dict) -> dict:
        from utils.bilibili_history import get_bilibili_history
        try:
            get_bilibili_history().update_search_tag(payload)
        except ValueError as error:
            return {"ok": False, "error": str(error)}
        except OSError:
            return {"ok": False, "error": "兴趣标签未能保存，请检查本地数据目录是否可写。"}
        return self.bilibili_surf_state()

    def save_bilibili_interaction_settings(self, payload: dict) -> dict:
        from utils.bilibili_interaction import get_bilibili_interaction_service
        return get_bilibili_interaction_service().save_settings(payload)

    def verify_bilibili_user_uid(self, uid: str) -> dict:
        from utils.bilibili_interaction import get_bilibili_interaction_service
        return get_bilibili_interaction_service().verify_user_uid(uid)

    def confirm_bilibili_user_uid(self, uid: str) -> dict:
        from utils.bilibili_interaction import get_bilibili_interaction_service
        return get_bilibili_interaction_service().confirm_user_uid(uid)

    def inspect_bilibili_test_video(self, target: str) -> dict:
        from utils.bilibili_interaction import get_bilibili_interaction_service
        return get_bilibili_interaction_service().inspect_test_video(target)

    def request_bilibili_test_comment(self, target: str = "", draft_id: str = "", confirmed=False) -> dict:
        from utils.bilibili_interaction import get_bilibili_interaction_service
        return get_bilibili_interaction_service().request_test_comment(target, draft_id, confirmed)

    def bilibili_login_start(self) -> dict:
        from utils.bilibili_interaction import get_bilibili_interaction_service
        return get_bilibili_interaction_service().start_login()

    def bilibili_login_poll(self, key: str) -> dict:
        from utils.bilibili_interaction import get_bilibili_interaction_service
        return get_bilibili_interaction_service().poll_login(key)

    def bilibili_logout(self) -> dict:
        from utils.bilibili_interaction import get_bilibili_interaction_service
        return get_bilibili_interaction_service().logout()

    def create_bilibili_comment_draft(self, target: str, text: str = "") -> dict:
        from utils.bilibili_interaction import get_bilibili_interaction_service

        service = get_bilibili_interaction_service()
        candidate = str(text or "").strip()
        if not candidate:
            inspected = service.inspect_test_video(target)
            if not inspected.get("ok"):
                return inspected
            video = inspected["video"]
            state = service.get_state()
            mention_name = state["settings"].get("userName", "")
            if not mention_name:
                return {
                    "ok": False,
                    "status": "blocked",
                    "error": "请先确认用户 UID 对应的公开昵称，再生成草稿。",
                    "state": state,
                }
            candidate = f"这个视频看起来挺有意思的，@{mention_name} 你也可以看看～"
            try:
                agent = self.agent()
                generated = agent._stream_summary_text(
                    [
                        {
                            "role": "system",
                            "content": (
                                "请为莲心生成一条待人工确认的 B 站评论草稿。只输出评论正文，"
                                "不加引号、不解释过程、不使用 Markdown；内容简短自然，不能假装已经看完视频。"
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"视频标题：{video.get('title', '')}\n"
                                f"UP 主：{video.get('author', '')}\n"
                                f"必须在正文中自然包含：@{mention_name}"
                            ),
                        },
                    ],
                    max_tokens=120,
                )
                if generated and len(generated.strip()) <= 500:
                    candidate = generated.strip()
            except Exception as exc:
                bridge_log.log("B站互动", f"评论草稿生成失败，使用安全兜底: {exc}")
        return service.create_comment_draft(target, candidate)

    def clear_bilibili_comment_draft(self) -> dict:
        from utils.bilibili_interaction import get_bilibili_interaction_service
        return get_bilibili_interaction_service().clear_comment_draft()

    def append_agent_context(self, content: str):
        agent = self.agent()
        if agent is not None:
            agent.history.append({"role": "assistant", "content": content})

    def _proactive_user_activity(self):
        runtime = getattr(self, "_proactive_runtime", None)
        if runtime is not None:
            try:
                runtime.notify_user_message()
            except Exception:
                pass

    def management(self) -> dict:
        return {"modules": [
            {"id": "ripple", "label": "涟漪情感系统", "available": True},
            {"id": "persona", "label": "人格枢控", "available": True},
            {"id": "memory-constellation", "label": "星图系统", "available": True},
            {"id": "prism-memory", "label": "棱镜记忆系统", "available": True},
            {"id": "history", "label": "历史记录", "available": True},
            {"id": "note", "label": "备忘本", "available": True},
            {"id": "alarm", "label": "闹钟与提醒", "available": True},
            {"id": "workflow", "label": "任务运行中心", "available": True},
            {"id": "duty", "label": "后台职责中心", "available": True},
            {"id": "proactive", "label": "主动聊天", "available": True},
            {"id": "study-room", "label": "莲心自习室", "available": True},
            {"id": "time-capsule", "label": "时间胶囊", "available": True},
            {"id": "data-tide", "label": "数据潮汐", "available": True},
            {"id": "capability", "label": "能力中枢", "available": True},
            {"id": "vision", "label": "视觉理解", "available": True},
            {"id": "voice-stt", "label": "语音转录", "available": True},
            {"id": "voice", "label": "语音聊天", "available": True, "state": self.voice_state()},
            {"id": "sound", "label": "声音设置", "available": True},
            {"id": "settings", "label": "全局设置", "available": True},
            {"id": "api", "label": "API Key", "available": True},
            {"id": "network", "label": "网络设置", "available": True},
            {"id": "qq", "label": "QQ 聊天", "available": True},
            {"id": "wechat", "label": "微信聊天", "available": True},
            {"id": "music", "label": "网易云音乐", "available": bool(self.music_state().get("source"))},
            {"id": "python-api", "label": "Python 核心 API", "available": True},
        ]}


    def persona_state(self) -> dict:
        from brain.persona import PersonaPromptComposer, get_persona_manager
        from brain.persona.growth import get_persona_growth_service
        from config import get_core_system_policy, get_user_name

        manager = get_persona_manager()
        snapshot = manager.get_snapshot()
        growth = get_persona_growth_service()
        try:
            compiled = PersonaPromptComposer.compose(
                snapshot,
                user_name=get_user_name(),
                core_policy=get_core_system_policy(),
                scene_policy="不同渠道会在运行时追加各自的场景规则。",
            )
            preview = {"text": compiled.text, "estimated_tokens": compiled.estimated_tokens, "layers": [layer.name for layer in compiled.layers]}
        except Exception as exc:
            preview = {"text": "", "estimated_tokens": 0, "layers": [], "error": str(exc)}
        return {
            "snapshot": {"profile": snapshot.profile.to_dict(), "revision": snapshot.revision, "enabled": snapshot.enabled, "activated_at": snapshot.activated_at},
            "profiles": [profile.to_dict() for profile in manager.list_profiles()],
            "preview": preview,
            "growth": {"summary": growth.summary(snapshot.profile.id), "events": [event.__dict__ for event in growth.store.list(snapshot.profile.id)[:100]], "settings": growth.settings()},
        }

    def persona_save(self, payload: dict) -> dict:
        from brain.persona import get_persona_manager
        manager = get_persona_manager()
        profile = manager.load_profile(str(payload.get("id", "")))
        fields = ("profile_name", "assistant_name", "summary", "identity", "appearance", "personality", "speaking_style", "habits", "relationship", "user_address", "boundaries", "custom_instructions")
        manager.save_profile(profile.updated(**{field: str(payload[field] or "") for field in fields if field in payload}))
        return self.persona_state()

    def persona_create(self, name: str) -> dict:
        from brain.persona import get_persona_manager
        get_persona_manager().create_profile(name)
        return self.persona_state()

    def persona_activate(self, profile_id: str, enabled: bool = True) -> dict:
        from brain.persona import get_persona_manager
        get_persona_manager().activate(profile_id, enable=enabled)
        return self.persona_state()

    def persona_toggle(self, enabled: bool) -> dict:
        from brain.persona import get_persona_manager
        get_persona_manager().set_enabled(enabled)
        return self.persona_state()

    def persona_delete(self, profile_id: str) -> dict:
        from brain.persona import get_persona_manager
        get_persona_manager().delete_profile(profile_id)
        return self.persona_state()

    def memory_constellation_snapshot(self) -> dict:
        from brain.memory_narrative import list_entity_profiles, list_episodes, list_sagas, list_narrative_events, get_last_narrative_run
        from config import get_user_name
        entities = list_entity_profiles(300)
        episodes = list_episodes(300)
        sagas = list_sagas(100)
        try:
            from brain.graph_memory import list_all_facts
            facts_by_category = list_all_facts() or {}
        except Exception:
            facts_by_category = {}
        facts = []
        for category, rows in facts_by_category.items():
            for row in rows or []:
                item = dict(row)
                item["category"] = category
                item["kind"] = "fact"
                item["label"] = item.get("content") or "\u672a\u547d\u540d\u8bb0\u5fc6"
                facts.append(item)
        source_ids = {}
        try:
            from brain.graph_memory import get_fact_fragments
            def collect_fact_ids(item):
                fact_ids = []
                if item.get("kind") == "fact" and str(item.get("id", "")).isdigit():
                    fact_ids.append(int(item["id"]))
                for key in ("source_fact_ids", "fragment_ids"):
                    try:
                        values = json.loads(item.get(key, "[]") or "[]")
                        if key == "source_fact_ids":
                            fact_ids.extend(int(v) for v in values)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        pass
                return fact_ids
            def collect_sources(item):
                ids = []
                for fact_id in collect_fact_ids(item):
                    for fragment in get_fact_fragments(fact_id, include_inactive=True):
                        ids.extend(int(v) for v in fragment.get("source_message_ids", []) if str(v).isdigit())
                return sorted(set(ids))
            for item in entities:
                source_ids["entity:" + str(item.get('id'))] = collect_sources(item)
                source_ids.setdefault(str(item.get("id")), source_ids["entity:" + str(item.get('id'))])
            for item in episodes:
                source_ids["episode:" + str(item.get('id'))] = collect_sources(item)
                source_ids.setdefault(str(item.get("id")), source_ids["episode:" + str(item.get('id'))])
            for item in facts:
                source_ids["fact:" + str(item.get('id'))] = collect_sources(item)
                source_ids.setdefault(str(item.get("id")), source_ids["fact:" + str(item.get('id'))])
            for saga in sagas:
                try:
                    episode_ids = [int(v) for v in json.loads(saga.get("episode_ids", "[]") or "[]")]
                except (TypeError, ValueError, json.JSONDecodeError):
                    episode_ids = []
                source_ids["saga:" + str(saga.get('id'))] = sorted({sid for eid in episode_ids for sid in source_ids.get("episode:" + str(eid), source_ids.get(str(eid), []))})
                source_ids.setdefault(str(saga.get("id")), source_ids["saga:" + str(saga.get('id'))])
        except Exception:
            pass
        try:
            from brain.persona.manager import PersonaManager
            assistant_name = PersonaManager().get_snapshot().profile.assistant_name or "\u83b2\u5fc3"
        except Exception:
            assistant_name = "\u83b2\u5fc3"
        try:
            from brain.memory_maintenance import get_last_maintenance_run
            maintenance = get_last_maintenance_run()
        except Exception:
            maintenance = None
        try:
            from brain.memory_quality import get_memory_statistics
            health = get_memory_statistics()
        except Exception:
            health = {}
        try:
            from brain.memory_diagnostics import get_memory_diagnostic_stats
            diagnostics = get_memory_diagnostic_stats()
        except Exception:
            diagnostics = {}
        return {
            "entities": entities, "episodes": episodes, "sagas": sagas, "facts": facts,
            "events": list_narrative_events(80), "source_ids": source_ids,
            "core": {"user": get_user_name() or "\u4e3b\u4eba", "assistant": assistant_name},
            "model": get_last_narrative_run() or {"status": "\u672a\u8fd0\u884c"},
            "maintenance": maintenance or {"status": "\u672a\u8fd0\u884c", "stats": {}},
            "health": health,
            "diagnostics": diagnostics,
        }

    def memory_constellation_messages(self, raw_ids: str) -> dict:
        try:
            ids = sorted({int(value) for value in json.loads(raw_ids or "[]") if str(value).isdigit()})
        except (TypeError, ValueError, json.JSONDecodeError):
            ids = []
        try:
            from brain.graph_memory import _get_conn
            if not ids:
                return {"text": "\u8be5\u8bb0\u5fc6\u6ca1\u6709\u53ef\u5b9a\u4f4d\u7684\u539f\u59cb\u6d88\u606f\u7f16\u53f7\u3002"}
            placeholders = ",".join("?" for _ in ids)
            rows = _get_conn().execute(
                "SELECT id,session_id,role,content,timestamp FROM messages WHERE id IN (" + placeholders + ") ORDER BY timestamp,id",
                tuple(ids),
            ).fetchall()
            text = "\n\n".join(
                "[" + str(row['timestamp'] or '') + "] #" + str(row['id']) + " " + str(row['role'] or '') + "\n" + str(row['content'] or '')
                for row in rows
            ) or "\u539f\u59cb\u6d88\u606f\u5df2\u4e0d\u5b58\u5728\u3002"
            return {"text": text}
        except Exception as exc:
            return {"text": "\u8bfb\u53d6\u539f\u59cb\u6d88\u606f\u5931\u8d25\uff1a" + str(exc)}

    def memory_constellation_review(self, raw_id: str) -> dict:
        try:
            fact_id = int(raw_id)
            if fact_id <= 0:
                return {"ok": False}
            from brain.graph_memory import _get_conn
            conn = _get_conn()
            cur = conn.execute(
                "UPDATE memory_facts SET review_status='needs_confirmation', quality_updated_at=datetime('now','localtime') WHERE id=? AND status='active'",
                (fact_id,),
            )
            conn.commit()
            return {"ok": cur.rowcount > 0}
        except Exception:
            return {"ok": False}

    def memory_constellation_correct(self, raw_id: str, content: str) -> dict:
        try:
            fact_id = int(raw_id)
            if fact_id <= 0 or not str(content or "").strip():
                return {"ok": False, "error": "记忆内容不能为空"}
            from brain.graph_memory import correct_fact_by_id
            fact = correct_fact_by_id(fact_id, content)
            return {"ok": bool(fact), "fact": fact, "error": "记忆不存在" if not fact else ""}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def memory_constellation_delete(self, raw_id: str) -> dict:
        try:
            fact_id = int(raw_id)
            if fact_id <= 0:
                return {"ok": False, "error": "无效的记忆编号"}
            from brain.graph_memory import delete_fact_by_id
            ok = delete_fact_by_id(fact_id)
            return {"ok": ok, "error": "记忆不存在" if not ok else ""}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def ripple_snapshot(self) -> dict:
        payload = self.memory_constellation_snapshot()
        try:
            from brain.emotional import get_manager
            manager = get_manager()
            emotion = manager.get_debug_info()
            emotion["motive"] = manager.get_proactive_motive()
            emotion["tone_guidance"] = manager.build_prompt_snippet()
            payload["emotion"] = emotion
        except Exception as exc:
            payload["emotion"] = {"error": str(exc), "version": 3}
        payload["ripple"] = {
            "schema": 1,
            "title": "涟漪星图",
            "description": "情绪状态、关系变化与长期记忆的可追溯视图",
        }
        return payload

    def ripple_simulate(self, scenario: str) -> dict:
        try:
            from brain.emotional import get_manager
            result = get_manager().simulate_scenario(str(scenario or ""))
            return result
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}

    def ripple_restore(self) -> dict:
        try:
            from brain.emotional import get_manager
            return get_manager().restore_simulation()
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}

    def ripple_configure(self, raw_config: str) -> dict:
        try:
            payload = json.loads(raw_config or "{}")
            from brain.emotional import get_manager
            get_manager().configure_settings(
                semantic_analysis=payload.get("semantic_analysis"),
                significant_memory_threshold=payload.get("significant_memory_threshold"),
                proactive_motive_enabled=payload.get("proactive_motive_enabled"),
                saga_bias_scale=payload.get("saga_bias_scale"),
                dynamics=payload.get("dynamics"),
            )
            return {"ok": True}
        except Exception:
            return {"ok": False}

    def ripple_clear(self) -> dict:
        try:
            from brain.emotional import get_manager
            get_manager().clear_simulation_events()
            return {"ok": True}
        except Exception:
            return {"ok": False}

    def data_tide_state(self) -> dict:
        from config import get_user_name
        from gui.achievement.service import AchievementService
        state = AchievementService().state()
        # AchievementService projects closed presence segments. Include the
        # current API session immediately so the card does not wait for exit.
        live_seconds = self._accompany_stats.get_current_session_seconds()
        metrics = state.setdefault("metrics", {})
        metrics["today_seconds"] = int(metrics.get("today_seconds", 0) or 0) + live_seconds
        metrics["seconds"] = int(metrics.get("seconds", 0) or 0) + live_seconds
        state["user_name"] = get_user_name()
        return state

    def data_tide_journey(self, offset: int, limit: int, categories: str, day: str) -> dict:
        try:
            parsed = json.loads(str(categories or "[]"))
            if not isinstance(parsed, list):
                parsed = []
            from gui.achievement.service import AchievementService
            return AchievementService().journey_page(offset, limit, parsed, str(day or ""))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return {"items": [], "total": 0, "error": str(exc)}

    def data_tide_export(self, export_format: str) -> dict:
        try:
            from gui.achievement.service import AchievementService
            return {"ok": True, "path": AchievementService().export_metrics(str(export_format or "json"))}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def data_tide_mark_read(self, raw_ids: str) -> dict:
        try:
            ids = json.loads(str(raw_ids or "[]"))
            if not isinstance(ids, list):
                raise ValueError("achievement ids must be a list")
            from gui.achievement.service import AchievementService
            AchievementService().mark_unlocks_read(ids)
            return {"ok": True}
        except (ValueError, TypeError, json.JSONDecodeError):
            return {"ok": False}

    def study_room_rpc(self, method: str, args: list) -> dict:
        allowed = {
            "get_initial_state", "get_space", "save_space_settings", "update_note", "get_statistics", "get_report",
            "start_focus", "toggle_pause", "stop_focus", "add_task", "toggle_task", "complete_task", "delete_task",
            "update_task", "refresh_statistics", "set_statistics_active", "save_settings", "minimize_window",
            "toggle_fullscreen", "set_focus_fullscreen", "close_window",
        }
        return self._qt_bridge_rpc("study", method, args, allowed)

    def time_capsule_rpc(self, method: str, args: list) -> dict:
        allowed = {
            "get_initial_state", "get_day", "get_page_state", "get_corridor_page", "get_museum_page", "get_tree_page",
            "save_user_content", "seal_day", "request_diary_generation", "toggle_day_favorite", "add_trace", "add_collection",
            "import_photos", "import_collection_file", "import_collection_path", "import_collection_data", "open_collection",
            "toggle_collection_favorite", "add_tree_note", "request_tree_reply", "mark_tree_notifications_read", "mark_tree_thread_read",
            "toggle_tree_favorite", "toggle_tree_archive", "search", "get_settings", "open_media_directory", "get_default_media_directory",
            "save_settings", "save_settings_payload", "visit_diary", "invite_lianxin", "request_close", "request_minimize", "request_fullscreen",
        }
        return self._qt_bridge_rpc("time-capsule", method, args, allowed)

    def _qt_bridge_rpc(self, kind: str, method: str, args: list, allowed: set[str]) -> dict:
        if method not in allowed:
            return {"ok": False, "error": "bridge method is not allowed"}
        if not isinstance(args, list):
            return {"ok": False, "error": "bridge args must be a list"}
        if kind == "study":
            with self._study_room_lock:
                if self._study_room_bridge is None:
                    from gui.study_room.bridge import StudyRoomBridge
                    self._study_room_bridge = StudyRoomBridge()
                target = self._study_room_bridge
        else:
            with self._time_capsule_lock:
                if self._time_capsule_bridge is None:
                    from gui.time_capsule.bridge import TimeCapsuleBridge
                    self._time_capsule_bridge = TimeCapsuleBridge()
                    self._time_capsule_bridge.generation_requested.connect(
                        self._start_time_capsule_diary_generation
                    )
                target = self._time_capsule_bridge
        try:
            if kind == "study" and method == "get_initial_state":
                timer = getattr(target, "timer", None)
                qt_timer = getattr(timer, "_timer", None)
                if timer is not None and qt_timer is not None and timer.active and qt_timer.isActive():
                    timer._on_tick()
            result = getattr(target, method)(*args)
            if kind == "study" and method in {"get_initial_state", "get_space", "save_space_settings"}:
                result = self._study_room_http_payload(result)
            return {"ok": True, "result": result}
        except TypeError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def _study_room_http_payload(raw):
        """Replace legacy file URLs so an HTTP iframe can load study wallpapers."""
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError):
            return raw

        def replace_space_urls(space):
            if not isinstance(space, dict):
                return
            for item in space.get("wallpapers", []):
                if not isinstance(item, dict):
                    continue
                candidate = str(item.get("id") or "")
                if candidate and candidate != "default":
                    item["url"] = _study_room_wallpaper_url(Path(candidate))

        if isinstance(payload, dict):
            replace_space_urls(payload.get("space"))
            replace_space_urls(payload)
        return json.dumps(payload, ensure_ascii=False) if isinstance(raw, str) else payload

    def shutdown_web_bridges(self) -> None:
        try:
            from brain.runtime import get_task_runtime
            get_task_runtime().shutdown()
        except Exception:
            pass
        with self._study_room_lock:
            if self._study_room_bridge is not None:
                self._study_room_bridge.shutdown()
                self._study_room_bridge = None
        with self._time_capsule_lock:
            self._time_capsule_bridge = None
            self._diary_workers.clear()

    def _start_time_capsule_diary_generation(self, date_str: str) -> None:
        """Reuse the existing DiaryWorker chain for iframe-triggered generation."""
        try:
            from brain.interaction_events import InteractionEventStore
            from config import get_diary_config
            from utils.diary import DiaryWorker

            history = self.agent().get_history_manager()
            messages = [
                {"role": item["role"], "content": item["content"]}
                for item in history.get_messages_by_date(str(date_str), owner_only=True)
            ]
            config = get_diary_config()
            enabled_features = {
                "desktop": config.get("reference_chat", True),
                "tree_hole": config.get("reference_tree_hole", True),
                "study_room": config.get("reference_study_room", True),
                "time_capsule": config.get("reference_time_capsule", True),
            }
            events = InteractionEventStore().list_for_date(str(date_str), limit=80)
            events = [event for event in events if enabled_features.get(event.get("feature", ""), True)]
            events = [event for event in events if event.get("importance") != "noise"]
            events.sort(key=lambda event: (event.get("importance") != "important", event.get("occurred_at", "")))
            messages.extend(
                {"role": "system", "source_event_id": event["id"],
                 "content": f"[{event['importance']}][{event['feature']}] {event.get('summary') or event.get('content')}"}
                for event in events if event.get("event_type") != "diary_saved"
            )
            if not messages:
                self._emit_time_capsule_generation(str(date_str), False, "当天没有可用于生成日记的内容")
                return
            maximum = int(config.get("max_messages", 30))
            selected = messages[:maximum] if config.get("direction") == "earliest" else messages[-maximum:]
            worker = DiaryWorker(str(date_str), selected)
            self._diary_workers.add(worker)
            worker.finished.connect(
                lambda success, result, current=worker: self._on_time_capsule_diary_finished(
                    current, str(date_str), bool(success), str(result)
                )
            )
            worker.start()
        except Exception as exc:
            self._emit_time_capsule_generation(str(date_str), False, str(exc))

    def _on_time_capsule_diary_finished(self, worker, date_str: str, success: bool, result: str) -> None:
        self._diary_workers.discard(worker)
        self._emit_time_capsule_generation(date_str, success, "" if success else result)

    def _emit_time_capsule_generation(self, date_str: str, success: bool, error: str) -> None:
        with self._time_capsule_lock:
            capsule = self._time_capsule_bridge
        if capsule is not None:
            capsule.generation_completed.emit(str(date_str), bool(success), str(error or ""))
            capsule.emit_state(str(date_str))
            capsule.emit_page_state("today")

    def open_legacy_window(self, feature: str) -> dict:
        if feature not in LEGACY_FEATURES:
            raise ValueError(f"不支持的原版界面: {feature}")
        if feature in WEBENGINE_FEATURES:
            try:
                import PyQt5.QtWebEngineWidgets  # noqa: F401
            except ImportError as exc:
                raise RuntimeError(
                    "原版 WebEngine 窗口需要 PyQtWebEngine，请在莲心运行环境执行: "
                    "python -m pip install PyQtWebEngine==5.15.7"
                ) from exc
        launcher = Path(__file__).with_name("legacy_ui_launcher.py")
        log_dir = Path(__file__).with_name("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"legacy_ui_{feature}.log"
        # 每次拉起都轮转，避免子进程日志无界增长（曾累积到 1MB / 1.8 万行，
        # 且同一条异常被重复打印上万次）。
        bridge_log.rotate_log(log_path, keep=3)
        log_file = log_path.open("a", encoding="utf-8")
        process = subprocess.Popen(
            [sys.executable, str(launcher), feature],
            cwd=str(launcher.parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        # The child owns its process lifetime, but the parent must release its
        # inherited file handle immediately after spawning it.
        log_file.close()
        return {"feature": feature, "pid": process.pid, "mode": "legacy-popup"}

    def five_axis(self) -> dict:
        try:
            from brain.emotional import get_manager
            manager = get_manager()
            info = manager.get_debug_info() or {}
            axes = info.get("axes") or {}
            return {
                "axes": {
                    "connection": float(axes.get("connection", 0.0)),
                    "pride": float(axes.get("pride", 0.0)),
                    "valence": float(axes.get("valence", 0.0)),
                    "arousal": float(axes.get("arousal", 0.0)),
                    "immersion": float(axes.get("immersion", 0.0)),
                },
                "mood": str(info.get("middle_layer") or "中性"),
            }
        except Exception as exc:
            return {"axes": {}, "mood": "状态暂不可用", "error": str(exc)}

    def task_snapshot(self) -> dict:
        data = self.tasks()
        data["workflowRuns"] = __import__("brain.workflow", fromlist=["get_workflow_store"]).get_workflow_store().list_runs(limit=30)
        return data

    def task_events(self, after: int = 0) -> dict:
        """Return incremental workflow/task events for the task center."""
        runs = self.task_snapshot().get("workflowRuns", [])
        items = []
        for run in runs:
            try:
                run_id = int(run.get("id", 0))
            except (TypeError, ValueError):
                continue
            if run_id > int(after):
                items.append({"id": run_id, "type": "workflow", "data": run})
        return {"items": sorted(items, key=lambda item: item["id"]), "latest": max([int(after)] + [item["id"] for item in items])}

    def time_capsule_state(self, day: str = "") -> dict:
        from datetime import date
        from gui.time_capsule.database import TimeCapsuleDatabase
        db = TimeCapsuleDatabase()
        selected = str(day or date.today().isoformat())
        return {"day": db.read_day(selected), "timeline": db.timeline(limit=60), "today": selected}

    def time_capsule_save(self, day: str, content: str) -> dict:
        from gui.time_capsule.database import TimeCapsuleDatabase
        return TimeCapsuleDatabase().save_user_content(str(day), str(content))

    def time_capsule_seal(self, day: str, content: str) -> dict:
        from gui.time_capsule.database import TimeCapsuleDatabase
        db = TimeCapsuleDatabase()
        db.save_user_content(str(day), str(content))
        return db.seal_day(str(day))

    def tasks(self) -> dict:
        store = get_task_store()
        try:
            from brain.task_tracker import get_task_tracker
            tracker = get_task_tracker()
            live_todos = tracker.get_todos()
            completed, total, active = tracker.get_progress()
        except Exception:
            live_todos, completed, total, active = [], 0, 0, ""
        return {
            "todos": store.list_todos(),
            "liveTodos": live_todos,
            "autoTasks": store.list_auto_tasks(),
            "logs": store.list_auto_task_logs(limit=50),
            "progress": {"completed": completed, "total": total, "active": active},
        }


# 方案 B：复用原有 DutyScheduler + ProactiveDuty 链路，挂到 api_server 常驻运行时
# 复用 DutyScheduler + ProactiveDuty + ProactivePresentationController 完整后台链路
# 呈现层改为「写会话历史 + TTS」，前端轮询 /api/conversations 显示

from PyQt5.QtCore import QObject, QThread, pyqtSignal


class _ProactiveTriggerRelay(QObject):
    """跨线程触发中继：HTTP handler 线程 emit，槽在 Qt 线程执行"""
    trigger_requested = pyqtSignal(str, str)   # mode, action
    user_message_requested = pyqtSignal()


class _HeadlessProactiveChatWidget:
    """无 GUI 聊天控件替身，供呈现控制器调用（headless）"""

    def __init__(self, bridge):
        self._bridge = bridge

    def add_ai_message(self, text):
        # 呈现由 controller 经 history_context_func 写入会话历史
        pass

    def add_system_tip(self, text):
        from utils.execution_status import get_execution_status_bus
        bus = get_execution_status_bus()
        if bus.enabled:
            bus.publish("主动任务未完成，可在设置中查看详情。", code="proactive_error",
                        source="proactive", request_id="proactive",
                        session_id=getattr(self._bridge.agent(), "_session_id", None))
        bridge_log.log("主动", str(text))

    def add_image_message(self, image_path, desc="", full_text="", is_ai=False):
        """Headless：把观察图片作为 assistant 图片消息落库，前端轮询即可渲染成莲心气泡。"""
        try:
            agent = self._bridge.agent()
            if agent is None:
                return
            history = agent.get_history_manager()
            session_id = getattr(agent, "_session_id", None)
            if history is None or session_id is None:
                return
            from pathlib import Path
            history.save_message(
                session_id, "assistant", full_text or desc or "",
                metadata={"attachments": [{
                    "kind": "image",
                    "path": str(image_path),
                    "fileName": Path(str(image_path)).name,
                    "description": desc or "",
                }]},
            )
        except Exception as exc:
            bridge_log.log("主动", f"观察图片落库失败: {exc}")

    def add_observation_bubble(self, image_path, caption="", raw_observation=""):
        """把"莲心看到的画面"落库成她自己的一条 assistant 消息。

        前端轮询到后渲染成莲心侧图片气泡（正文=她自己的话，附件=观察图片）。
        返回 True 表示已自行持久化，调用方不要再 _save_message。
        """
        try:
            agent = self._bridge.agent()
            if agent is None:
                return False
            history = agent.get_history_manager()
            session_id = getattr(agent, "_session_id", None)
            if history is None or session_id is None:
                return False
            from pathlib import Path as _Path

            from brain.observation import adopt_observation_image
            stored = adopt_observation_image(str(image_path))
            if not stored:
                return False
            history.save_message(
                session_id, "assistant", str(caption or ""),
                metadata={"attachments": [{
                    "kind": "image",
                    "path": stored,
                    "fileName": _Path(stored).name,
                    "description": str(raw_observation or ""),
                }]},
            )
            return True
        except Exception as exc:
            bridge_log.log("观察", f"观察图片落库失败: {exc}")
            return False

    def add_mooyu_data_sources(self, sources):
        pass

    def isVisible(self):
        return False


class _ProactiveRuntimeThread(QThread):
    """QThread carrier so DutyScheduler QTimer (60s master tick) works."""
    def __init__(self, runtime):
        super().__init__()
        self._runtime = runtime
    def run(self):
        self._runtime._run()


class LianxinProactiveRuntime:
    """在 api_server 内常驻的主动聊天运行时。

    在独立 Qt 线程里承载 DutyScheduler、QTimer/QThread；
    HTTP 触发请求经中继转发到 Qt 线程执行。
    """

    def __init__(self, bridge):
        self._bridge = bridge
        self._thread = None
        self._ready = threading.Event()
        self._relay = None
        self._duty_scheduler = None
        self._scheduler = None

    # 运行生命周期

    def start(self):
        if self._thread and self._thread.isRunning():
            return
        self._thread = _ProactiveRuntimeThread(self)
        self._thread.start()
        self._ready.wait(timeout=60)

    def _run(self):
        from PyQt5.QtCore import QEventLoop
        from utils.proactive_chat import ProactiveChatScheduler  # noqa: F401
        from utils.duty_scheduler import DutyScheduler, ProactiveDuty, BilibiliReplyDuty, register_duty
        from gui.proactive_controller import ProactivePresentationController
        from utils.settings import get_settings

        bridge = self._bridge
        scheduler = bridge.get_proactive_scheduler()
        chat_widget = _HeadlessProactiveChatWidget(bridge)

        duty = DutyScheduler()
        duty.setup(
            proactive_scheduler=scheduler,
            reminder_manager=None,
            global_settings=get_settings(),
            session_id_func=lambda: bridge.agent()._session_id if bridge.agent() else 0,
            history_manager_func=lambda: bridge.agent().get_history_manager() if bridge.agent() else None,
            qq_bridge_func=lambda: None,
            todo_manager=None,
            agent=lambda: bridge.agent() if bridge.agent() else None,
            chat_widget=chat_widget,
            speak_func=lambda text: self._safe_speak(bridge, text),
            is_shoulder_available=lambda: False,
            proactive_dialog=None,
        )
        register_duty(duty, ProactiveDuty())
        register_duty(duty, BilibiliReplyDuty())

        controller = ProactivePresentationController(
            scheduler=scheduler,
            chat_widget=chat_widget,
            history_manager_func=lambda: bridge.agent().get_history_manager() if bridge.agent() else None,
            session_id_func=lambda: bridge.agent()._session_id if bridge.agent() else 0,
            history_context_func=lambda content: bridge.append_agent_context(content),
            speak_func=lambda text: self._safe_speak(bridge, text),
            is_minimized_func=lambda: False,
            flash_taskbar_func=lambda *_args, **_kwargs: None,
            qq_bridge_func=lambda: None,
            dialog_func=lambda: None,
            next_track_func=lambda: None,
        )

        duty.proactive_response.connect(controller.handle_proactive_response)
        duty.proactive_error.connect(controller.handle_proactive_error)
        duty.proactive_coordination.connect(lambda msg: bridge_log.log("主动", str(msg)))
        duty.proactive_observation_text.connect(controller.handle_observation_result)
        duty.proactive_observation_image.connect(controller.handle_observation_image)
        duty.proactive_behavior_selected.connect(controller.set_behavior)
        duty.slack_response.connect(controller.handle_slack_response)
        duty.slack_error.connect(controller.handle_slack_error)
        duty.slack_action_selected.connect(controller.set_slack_action)
        duty.mooyu_data_sources.connect(controller.handle_mooyu_data_sources)
        duty.mooyu_duty_data_source.connect(controller.handle_mooyu_duty_data_source)

        relay = _ProactiveTriggerRelay()
        relay.trigger_requested.connect(self._on_trigger)
        relay.user_message_requested.connect(self._on_user_message)
        # pre-warm agent so the first _tick does not block the event loop 10-12s
        try:
            bridge.agent()
        except Exception:
            pass
        duty.start()

        self._duty_scheduler = duty
        self._scheduler = scheduler
        self._relay = relay
        self._ready.set()

        loop = QEventLoop()
        self._loop = loop
        loop.exec_()

    # 触发入口

    def trigger(self, mode: str = "normal", action: str = "") -> dict:
        relay = self._relay
        if relay is None or not self._ready.is_set():
            return {"ok": False, "error": "主动聊天运行时尚未就绪"}
        relay.trigger_requested.emit(str(mode), str(action))
        return {"ok": True}

    def notify_user_message(self):
        relay = self._relay
        if relay is not None:
            relay.user_message_requested.emit()

    def status(self) -> dict:
        duty = self._duty_scheduler
        if duty is None:
            return {"ready": False, "running": False}
        running = False
        try:
            for item in duty.get_all_statuses():
                if item.name == "proactive":
                    running = bool(item.is_running)
        except Exception:
            pass
        return {"ready": True, "running": running}

    def stop(self):
        loop = getattr(self, "_loop", None)
        if loop is not None:
            try:
                loop.quit()
            except Exception:
                pass
        duty = self._duty_scheduler
        if duty is not None:
            try:
                duty.stop()
            except Exception:
                pass

    # Qt 线程内触发的槽函数

    def _on_trigger(self, mode: str, action: str):
        duty = self._duty_scheduler
        scheduler = self._scheduler
        if duty is None or scheduler is None:
            return
        try:
            if mode == "bilibili":
                duty.manual_trigger("proactive", force_observe="bilibili")
            elif mode == "slack" or (mode or "").startswith("slack:"):
                slack_action = action
                if not slack_action and (mode or "").startswith("slack:"):
                    slack_action = mode.split(":", 1)[1]
                duty.manual_trigger("proactive", force_behavior="slack", force_action=slack_action)
            elif mode in ("screenshot", "camera"):
                duty.manual_trigger("proactive", force_observe=mode)
            else:
                # 对齐 main_window._on_proactive_debug 的默认触发逻辑
                if not (scheduler.desktop_enabled or scheduler.qq_enabled):
                    bridge_log.log("主动", "请先开启桌面或 QQ 主动聊天再触发")
                    return
                duty.manual_trigger("proactive")
        except Exception as exc:
            bridge_log.log("主动", f"触发失败: {exc}")

    def _on_user_message(self):
        duty = self._duty_scheduler
        scheduler = self._scheduler
        if duty is not None:
            try:
                duty.on_user_message()
            except Exception:
                pass
        if scheduler is not None:
            try:
                scheduler.notify_user_active()
            except Exception:
                pass

    @staticmethod
    def _safe_speak(bridge, text):
        try:
            import pygame
            if not pygame.mixer.get_init():
                pygame.mixer.init()
        except Exception:
            pass
        try:
            bridge.speak(str(text))
        except Exception as exc:
            bridge_log.log("主动", f"TTS 失败: {exc}")




bridge = LianxinBridge()


class Handler(BaseHTTPRequestHandler):
    server_version = "LianxinBridge/1.0"

    def log_message(self, *_args):
        return

    def handle_one_request(self):
        self._t0 = time.time()
        super().handle_one_request()

    def _send(self, payload, status=200, content_type="application/json"):
        started = getattr(self, "_t0", None)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
        if started is not None:
            _path = urlparse(self.path).path
            _duration = time.time() - started
            if _duration > 20:
                bridge_log.log("看门狗", f"慢请求: {self.command} {_path} 耗时 {_duration:.1f}s")

    def _body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def _memory_constellation_page(self):
        asset_dir = Path(__file__).resolve().parent / "assets" / "memory_constellation"
        try:
            template = (asset_dir / "index.html").read_text(encoding="utf-8")
        except OSError as exc:
            return self._send({"error": "constellation html missing: %s" % exc}, 404)
        payload = bridge.memory_constellation_snapshot()
        injected = "<script>window.LIANXIN_MEMORY_DATA=" + json.dumps(payload, ensure_ascii=False, default=str) + ";</script>"
        html = template.replace("<!-- LIANXIN_DATA -->", injected)
        html = html.replace('</head>', _EMBEDDED_WINDOW_SCRIPT + '</head>')
        html = html.replace('<script src="qrc:///qtwebchannel/qwebchannel.js"></script>', "")
        shim = _CONSTELLATION_BRIDGE_SHIM
        html = html.replace("<script src=\"app.js\"></script>", shim + "<script src=\"app.js\"></script>")
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
        return

    def _memory_constellation_asset(self, rel: str):
        asset_dir = Path(__file__).resolve().parent / "assets" / "memory_constellation"
        target = (asset_dir / rel).resolve()
        if asset_dir not in target.parents or not target.is_file():
            return self._send({"error": "constellation asset not found"}, 404)
        raw = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)
        return

    def _ripple_page(self):
        asset_dir = Path(__file__).resolve().parent / "assets" / "ripple_constellation"
        try:
            template = (asset_dir / "index.html").read_text(encoding="utf-8")
        except OSError as exc:
            return self._send({"error": "ripple html missing: %s" % exc}, 404)
        payload = bridge.ripple_snapshot()
        injected = "<script>window.LIANXIN_MEMORY_DATA=" + json.dumps(payload, ensure_ascii=False, default=str) + ";</script>"
        html = template.replace("<!-- LIANXIN_DATA -->", injected)
        html = html.replace('</head>', _EMBEDDED_WINDOW_SCRIPT + '</head>')
        html = html.replace('<script src="qrc:///qtwebchannel/qwebchannel.js"></script>', "")
        shim = _RIPPLE_BRIDGE_SHIM
        html = html.replace("<script src=\"app.js\"></script>", injected + shim + "<script src=\"app.js\"></script>")
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
        return

    def _ripple_asset(self, rel: str):
        asset_dir = Path(__file__).resolve().parent / "assets" / "ripple_constellation"
        target = (asset_dir / rel).resolve()
        if asset_dir not in target.parents or not target.is_file():
            return self._send({"error": "ripple asset not found"}, 404)
        raw = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)
        return

    def _data_tide_page(self):
        asset_dir = Path(__file__).resolve().parent / "gui" / "achievement" / "web"
        try:
            template = (asset_dir / "index.html").read_text(encoding="utf-8")
        except OSError as exc:
            return self._send({"error": "data-tide html missing: %s" % exc}, 404)
        html = template.replace('</head>', _EMBEDDED_WINDOW_SCRIPT + '</head>')
        html = html.replace('<script src="qrc:///qtwebchannel/qwebchannel.js"></script>', "")
        html = html.replace('<script src="app.js"></script>', _DATA_TIDE_BRIDGE_SHIM + '<script src="app.js"></script>')
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
        return

    def _data_tide_asset(self, rel: str):
        asset_dir = Path(__file__).resolve().parent / "gui" / "achievement" / "web"
        target = (asset_dir / rel).resolve()
        if asset_dir not in target.parents or not target.is_file():
            return self._send({"error": "data-tide asset not found"}, 404)
        raw = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)
        return

    def _study_room_page(self):
        asset_dir = Path(__file__).resolve().parent / "gui" / "study_room" / "web"
        try:
            template = (asset_dir / "index.html").read_text(encoding="utf-8")
        except OSError as exc:
            return self._send({"error": "study-room html missing: %s" % exc}, 404)
        html = template.replace('</head>', _STUDY_ROOM_EMBEDDED_STYLE + '</head>')
        html = html.replace('<script src="qrc:///qtwebchannel/qwebchannel.js"></script>', "")
        html = html.replace('<script src="app.js?v=20260726-16"></script>', _STUDY_ROOM_BRIDGE_SHIM + '<script src="app.js?v=20260726-16"></script>')
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
        return

    def _study_room_asset(self, rel: str):
        asset_dir = Path(__file__).resolve().parent / "gui" / "study_room" / "web"
        target = (asset_dir / rel).resolve()
        if asset_dir not in target.parents or not target.is_file():
            return self._send({"error": "study-room asset not found"}, 404)
        raw = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)
        return

    def _study_room_wallpaper(self, raw_path: str):
        try:
            target = Path(raw_path).expanduser().resolve()
            from utils.resource_path import get_asset_path
            built_in_root = get_asset_path("自习室").resolve()
            from PyQt5.QtCore import QSettings
            configured = Path(str(QSettings("Lianxin", "StudyRoom").value("space_wallpaper", "default"))).expanduser().resolve()
        except Exception:
            return self._send({"error": "wallpaper not found"}, 404)

        allowed = target.is_file() and (
            target == built_in_root or built_in_root in target.parents or target == configured
        )
        if not allowed:
            return self._send({"error": "wallpaper not found"}, 404)

        raw = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "image/jpeg")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "private, max-age=86400")
        self.end_headers()
        self.wfile.write(raw)
        return

    def _time_capsule_page(self):
        asset_dir = Path(__file__).resolve().parent / "gui" / "time_capsule" / "web"
        try:
            template = (asset_dir / "index.html").read_text(encoding="utf-8")
        except OSError as exc:
            return self._send({"error": "time-capsule html missing: %s" % exc}, 404)
        html = template.replace('</head>', _TIME_CAPSULE_EMBEDDED_STYLE + '</head>')
        html = html.replace('<script src="qrc:///qtwebchannel/qwebchannel.js"></script>', "")
        html = html.replace('<script src="app.js?v=20260728-4"></script>', _TIME_CAPSULE_BRIDGE_SHIM + '<script src="app.js?v=20260728-4"></script>')
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
        return

    def _time_capsule_asset(self, rel: str):
        asset_dir = Path(__file__).resolve().parent / "gui" / "time_capsule" / "web"
        target = (asset_dir / rel).resolve()
        if asset_dir not in target.parents or not target.is_file():
            return self._send({"error": "time-capsule asset not found"}, 404)
        raw = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)
        return

    def _video_call_asset(self, rel: str):
        root = Path(__file__).resolve().parent
        aliases = {
            "user-avatar.jpg": root / "assets" / "video_call" / "\u7528\u6237\u5934\u50cf.jpg",
            "lianxin-poster.jpg": root / "assets" / "video_call" / "\u83b2\u5fc3\u89c6\u9891\u7167\u7247.jpg",
            "startup.mp4": root / "assets" / "\u89c6\u9891\u901a\u8bdd" / "\u517c\u5bb9" / "\u5f00\u542f.mp4",
            "waiting-1.mp4": root / "assets" / "\u89c6\u9891\u901a\u8bdd" / "\u517c\u5bb9" / "\u5faa\u73af\u7b49\u5f851.mp4",
            "waiting-2.mp4": root / "assets" / "\u89c6\u9891\u901a\u8bdd" / "\u517c\u5bb9" / "\u5faa\u73af\u7b49\u5f852.mp4",
            "waiting-3.mp4": root / "assets" / "\u89c6\u9891\u901a\u8bdd" / "\u517c\u5bb9" / "\u5faa\u73af\u7b49\u5f853.mp4",
        }
        if rel in aliases:
            requested = aliases[rel].resolve()
        else:
            requested = (root / unquote(rel)).resolve()
        allowed_roots = [root / "assets" / "video_call", root / "assets" / "视频通话", root / "assets" / "GIF" / "正常与说话"]
        if not any(requested == allowed or allowed in requested.parents for allowed in allowed_roots) or not requested.is_file():
            return self._send({"error": "video-call asset not found"}, 404)
        raw = requested.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(requested.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "private, max-age=86400")
        self.end_headers()
        self.wfile.write(raw)
        return


    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/app/status":
                return self._send(bridge.status())
            if path == "/api/execution-status/events":
                from utils.execution_status import get_execution_status_bus
                query = parse_qs(urlparse(self.path).query)
                session = int((query.get("sessionId") or ["0"])[0]) or None
                return self._send(get_execution_status_bus().events(
                    int((query.get("after") or ["0"])[0]), session_id=session,
                ))
            if path == "/api/qq/status":
                return self._send(bridge.qq_status())
            if path == "/api/attachments":
                query = parse_qs(urlparse(self.path).query)
                target = Path((query.get("path") or [""])[0]).expanduser().resolve()
                allowed = [Path.home() / ".lianxin" / "images", Path.home() / ".lianxin" / "files", Path.home() / ".lianxin" / "observations"]
                if not any(target == root.resolve() or root.resolve() in target.parents for root in allowed) or not target.is_file():
                    return self._send({"error": "attachment not found"}, 404)
                raw = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(raw)
                return
            if path == "/api/conversations":
                query = parse_qs(urlparse(self.path).query)
                keyword = (query.get("q") or [""])[0].strip()
                items = bridge.agent().get_history_manager().search_sessions(keyword) if keyword else bridge.sessions()
                return self._send({"items": items})
            if path.startswith("/api/conversations/") and path.endswith("/messages"):
                sid = int(path.split("/")[3])
                query = parse_qs(urlparse(self.path).query)
                after = int((query.get("after") or [0])[0] or 0)
                return self._send({"items": bridge.messages(sid, after=after)})
            if path == "/api/note":
                return self._send({"content": read_note()})
            if path == "/api/tasks":
                return self._send(bridge.tasks())
            if path == "/api/music/ensure":
                return self._send(bridge.music_ensure())
            if path == "/api/music/cover":
                query = parse_qs(urlparse(self.path).query)
                url = (query.get("url") or [""])[0]
                if not url:
                    return self._send({"error": "url required"}, 400)
                try:
                    target = _download_cover(url)
                    raw = target.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "image/jpeg")
                    self.send_header("Content-Length", str(len(raw)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Cache-Control", "private, max-age=86400")
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                except Exception as exc:
                    return self._send({"error": str(exc)}, 502)
            if path == "/api/music/space-settings":
                return self._send(bridge._music_space_settings())
            if path == "/api/music/wallpaper":
                query = parse_qs(urlparse(self.path).query)
                raw_id = (query.get("id") or [""])[0]
                raw_path = (query.get("path") or [""])[0]
                if raw_id:
                    target = _music_wallpaper_path(raw_id)
                else:
                    try:
                        target = Path(raw_path).expanduser().resolve()
                    except Exception:
                        target = None
                if target is not None and target.is_file():
                    try:
                        from utils.resource_path import get_asset_path
                        root = get_asset_path("主界面背景图").resolve()
                    except Exception:
                        root = None
                    allowed = raw_id.startswith(_MUSIC_WALLPAPER_FILE_PREFIX) if raw_id else False
                    if not allowed and root is not None:
                        allowed = target == root or root in target.parents
                    if allowed:
                        raw = target.read_bytes()
                        self.send_response(200)
                        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "image/jpeg")
                        self.send_header("Content-Length", str(len(raw)))
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Cache-Control", "private, max-age=86400")
                        self.end_headers()
                        self.wfile.write(raw)
                        return
                return self._send({"error": "wallpaper not found"}, 404)
            if path == "/api/music/state":
                return self._send(bridge.music_state())
            if path == "/api/music/playlists":
                query = parse_qs(urlparse(self.path).query)
                return self._send(bridge.music_playlists(int((query.get("offset") or [0])[0]), int((query.get("limit") or [24])[0])))
            if path == "/api/music/playlist":
                query = parse_qs(urlparse(self.path).query)
                return self._send(bridge.music_playlist((query.get("id") or [""])[0]))
            if path == "/api/music/stats":
                return self._send(bridge.music_stats())
            if path == "/api/music/events":
                query = parse_qs(urlparse(self.path).query)
                return self._send(bridge.music_events(int((query.get("after") or [0])[0])))
            if path == "/api/music/feedback-settings":
                return self._send(bridge.music_feedback_settings())
            if path == "/api/music/settings":
                return self._send(bridge.music_feedback_settings())
            if path == "/api/music/spectrum":
                from utils.spectrum import ensure_started, snapshot
                ensure_started()
                on, bars = snapshot()
                return self._send({"on": on, "bars": bars})
            if path == "/api/voice/status":
                return self._send(bridge.voice_state())
            if path == "/api/voice/events":
                query = parse_qs(urlparse(self.path).query)
                return self._send(bridge.voice_events(int((query.get("after") or [0])[0])))
            if path == "/api/tools/events":
                query = parse_qs(urlparse(self.path).query)
                return self._send(bridge.tool_events(int((query.get("after") or [0])[0])))
            if path == "/api/capabilities":
                return self._send(bridge.capabilities())
            if path == "/api/tasks/events":
                query = parse_qs(urlparse(self.path).query)
                return self._send(bridge.task_events(int((query.get("after") or [0])[0])))
            if path == "/api/runtime/state":
                query = parse_qs(urlparse(self.path).query)
                probe = str((query.get("probe") or ["0"])[0]).lower() in ("1", "true", "yes")
                return self._send(bridge.runtime_state(probe=probe))
            if path == "/api/runtime/events":
                query = parse_qs(urlparse(self.path).query)
                return self._send(bridge.runtime_events(int((query.get("after") or [0])[0])))
            if path == "/api/time-capsule/state":
                query = parse_qs(urlparse(self.path).query)
                return self._send(bridge.time_capsule_state((query.get("day") or [""])[0]))
            if path == "/api/proactive/state":
                return self._send(bridge.proactive())
            if path == "/api/proactive/status":
                return self._send(bridge.proactive_status())
            if path == "/api/bilibili-interaction/state":
                return self._send(bridge.bilibili_interaction_state())
            if path == "/api/bilibili-surf/state":
                return self._send(bridge.bilibili_surf_state())
            if path == "/api/management/state":
                return self._send(bridge.management())
            if path == "/api/persona/state":
                return self._send(bridge.persona_state())
            if path == "/api/settings/background":
                query = parse_qs(urlparse(self.path).query)
                include_data = (query.get("include") or ["1"])[0] != "0"
                return self._send(bridge.background_state(include_data=include_data))
            if path == "/api/settings/panels":
                return self._send(bridge.settings_panel_state())
            if path == "/api/settings/avatars":
                query = parse_qs(urlparse(self.path).query)
                include_data = (query.get("include") or ["1"])[0] != "0"
                return self._send(bridge.avatar_state(include_data=include_data))
            if path == "/api/tts/status":
                # 只读接口：前端（GET 轮询）与 legacy_ui_launcher 都走这里；
                # 缺了它界面会一直拿不到 stage/notice。
                return self._send(bridge.tts_state())
            if path == "/api/avatar/action":
                query = parse_qs(urlparse(self.path).query)
                return self._send(bridge.avatar_action((query.get("action") or [""])[0]))
            if path == "/api/legacy/windows":
                return self._send({"items": [item for item in bridge.management()["modules"] if item["id"] in LEGACY_FEATURES]})
            if path == "/api/emotion/five-axis":
                return self._send(bridge.five_axis())
            if path == "/api/tasks/snapshot":
                return self._send(bridge.task_snapshot())
            if path == "/api/memory-constellation/snapshot":
                return self._send(bridge.memory_constellation_snapshot())
            if path == "/api/memory-constellation/messages":
                query = parse_qs(urlparse(self.path).query)
                ids = (query.get("ids") or [""])[0]
                return self._send(bridge.memory_constellation_messages(ids))
            if path == "/api/memory-constellation/html":
                return self._memory_constellation_page()
            if path.startswith("/api/memory-constellation/"):
                return self._memory_constellation_asset(path[len("/api/memory-constellation/"):])
            if path == "/api/ripple/snapshot":
                return self._send(bridge.ripple_snapshot())
            if path == "/api/ripple/html":
                return self._ripple_page()
            if path.startswith("/api/ripple/"):
                return self._ripple_asset(path[len("/api/ripple/"):])
            if path == "/api/data-tide/state":
                return self._send(bridge.data_tide_state())
            if path == "/api/data-tide/journey":
                query = parse_qs(urlparse(self.path).query)
                return self._send(bridge.data_tide_journey(
                    int((query.get("offset") or [0])[0]),
                    int((query.get("limit") or [20])[0]),
                    (query.get("categories") or ["[]"])[0],
                    (query.get("day") or [""])[0],
                ))
            if path == "/api/data-tide/html":
                return self._data_tide_page()
            if path.startswith("/api/data-tide/"):
                return self._data_tide_asset(path[len("/api/data-tide/"):])
            if path == "/api/study-room/html":
                return self._study_room_page()
            if path == "/api/study-room/wallpaper":
                query = parse_qs(urlparse(self.path).query)
                return self._study_room_wallpaper((query.get("path") or [""])[0])
            if path.startswith("/api/study-room/"):
                return self._study_room_asset(path[len("/api/study-room/"):])
            if path == "/api/time-capsule/html":
                return self._time_capsule_page()
            if path.startswith("/api/video-call/assets/"):
                return self._video_call_asset(path[len("/api/video-call/assets/"):])
            if path.startswith("/api/time-capsule/"):
                return self._time_capsule_asset(path[len("/api/time-capsule/"):])



            return self._send({"error": "Not found"}, 404)
        except Exception as exc:
            return self._send({"error": str(exc)}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._body()
            if path == "/api/app/shutdown":
                if self.headers.get("X-Lianxin-Shutdown") != "1":
                    return self._send({"ok": False, "error": "desktop_request_required"}, 403)
                from utils.app_lifecycle import request_shutdown
                self._send({"ok": True})
                request_shutdown(self.server)
                return
            if path == "/api/conversations":
                return self._send(bridge.new_session(), 201)
            if path.startswith("/api/conversations/") and path.endswith("/select"):
                sid = int(path.split("/")[3])
                return self._send(bridge.select_session(sid))
            if path == "/api/chat/message":
                return self._send(bridge.chat(str(body.get("message", ""))))
            if path == "/api/chat/cancel":
                return self._send(bridge.cancel_chat())
            if path == "/api/runtime/start":
                try:
                    runtime_turn = int(body.get("turn") or 0)
                except (TypeError, ValueError):
                    runtime_turn = 0
                return self._send(bridge.runtime_start(
                    str(body.get("goal", "")),
                    session_id=body.get("sessionId"),
                    turn=runtime_turn,
                    sandbox=str(body.get("sandbox") or ""),
                    cwd=str(body.get("cwd") or ""),
                    wait=bool(body.get("wait")),
                    resume_thread_id=str(body.get("resumeThreadId") or ""),
                    network_access=bool(body.get("networkAccess")),
                    goal_contract=body.get("goalContract"),
                ), 201)
            if path == "/api/runtime/steer":
                return self._send(bridge.runtime_steer(str(body.get("text") or ""), bridge.runtime_scope(body)))
            if path == "/api/runtime/continue":
                return self._send(bridge.runtime_continue(body))
            if path == "/api/runtime/stop":
                return self._send(bridge.runtime_stop(str(body.get("reason") or "user"), bridge.runtime_scope(body)))
            if path == "/api/runtime/approve":
                return self._send(bridge.runtime_approve(
                    str(body.get("requestId") or ""),
                    str(body.get("decision") or "accept"),
                    remember=bool(body.get("remember")),
                    scope=bridge.runtime_scope(body),
                ))
            if path == "/api/chat/stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                for event in bridge.chat_stream(
                    str(body.get("message", "")),
                    body.get("attachments") if isinstance(body.get("attachments"), list) else [],
                    str(body.get("forcedTool") or "") or None,
                    str(body.get("preferredTool") or "") or None,
                    body.get("quote") if isinstance(body.get("quote"), dict) else None,
                ):
                    if event.get("type") == "completed":
                        event = {**event, "message": event.get("message", {})}
                    self.wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                return
            if path == "/api/note":
                write_note(str(body.get("content", "")))
                return self._send({"content": read_note()})
            if path == "/api/music/space-settings":
                return self._send(bridge.save_music_space_settings(
                    str(body.get("wallpaper", "default")),
                    body.get("wallpaper_opacity", 0.7),
                    body.get("content_mask_opacity", 0.5),
                    str(body.get("fit", "cover")),
                ))
            if path == "/api/settings/panels":
                return self._send(bridge.save_settings_panel(body))
            if path == "/api/music/control":
                return self._send(bridge.music_control(str(body.get("action", "")), body))
            if path == "/api/music/play-playlist":
                return self._send(bridge.music_play_playlist(str(body.get("id", ""))))
            if path == "/api/music/feedback-settings":
                return self._send(bridge.save_music_feedback_settings(body))
            if path == "/api/music/settings":
                return self._send(bridge.save_music_feedback_settings(body))
            if path == "/api/open-player":
                return self._send(bridge.open_web_player())
            if path == "/api/voice/start":
                return self._send(bridge.start_voice())
            if path == "/api/voice/stop":
                return self._send(bridge.stop_voice())
            if path == "/api/voice/mic":
                return self._send(bridge.set_voice_mic(bool(body.get("muted", False))))
            if path == "/api/voice/input/start":
                return self._send(bridge.start_voice_input())
            if path == "/api/voice/input/stop":
                return self._send(bridge.stop_voice_input())
            if path == "/api/tts/speak":
                return self._send(bridge.speak(str(body.get("text", "")), str(body.get("voice", "")), body.get("generation")))
            if path == "/api/tts/prepare":
                return self._send(bridge.prepare_speech(str(body.get("text", "")), body.get("generation")))
            if path == "/api/tts/play-prepared":
                return self._send(bridge.play_prepared_speech(str(body.get("id", ""))))
            if path == "/api/tts/stop":
                return self._send(bridge.stop_speaking())
            if path == "/api/tts/status":
                return self._send(bridge.tts_state())
            if path == "/api/sound/play":
                return self._send(bridge.play_sound(str(body.get("name", ""))))
            if path == "/api/sound/call-wait/start":
                return self._send(bridge.start_call_sound())
            if path == "/api/sound/call-wait/stop":
                return self._send(bridge.stop_call_sound())
            if path == "/api/time-capsule/save":
                return self._send(bridge.time_capsule_save(str(body.get("day", "")), str(body.get("content", ""))))
            if path == "/api/time-capsule/seal":
                return self._send(bridge.time_capsule_seal(str(body.get("day", "")), str(body.get("content", ""))))
            if path == "/api/proactive/toggle":
                return self._send(bridge.set_proactive(bool(body.get("enabled"))))
            if path == "/api/bilibili-interaction/settings":
                return self._send(bridge.save_bilibili_interaction_settings(body))
            if path == "/api/bilibili-surf/tag":
                return self._send(bridge.save_bilibili_surf_tag(body))
            if path == "/api/bilibili-surf/open-video":
                return self._send(bridge.open_bilibili_video(body.get("bvid", "")))
            if path == "/api/bilibili-interaction/verify-user":
                return self._send(bridge.verify_bilibili_user_uid(str(body.get("uid", ""))))
            if path == "/api/bilibili-interaction/confirm-user":
                return self._send(bridge.confirm_bilibili_user_uid(str(body.get("uid", ""))))
            if path == "/api/bilibili-interaction/inspect-video":
                return self._send(bridge.inspect_bilibili_test_video(str(body.get("target", ""))))
            if path == "/api/bilibili-interaction/test-comment":
                return self._send(bridge.request_bilibili_test_comment(
                    str(body.get("target", "")), str(body.get("draftId", "")), body.get("confirmed") is True))
            if path == "/api/bilibili-interaction/comment/recheck":
                from utils.bilibili_interaction import get_bilibili_interaction_service
                return self._send(get_bilibili_interaction_service().recheck_comment(str(body.get("rpid", ""))))
            if path == "/api/bilibili-interaction/account/check":
                from utils.bilibili_interaction import get_bilibili_interaction_service
                return self._send(get_bilibili_interaction_service().check_authorization())
            if path == "/api/bilibili-interaction/login/start":
                return self._send(bridge.bilibili_login_start())
            if path == "/api/bilibili-interaction/login/poll":
                return self._send(bridge.bilibili_login_poll(str(body.get("key", ""))))
            if path == "/api/bilibili-interaction/logout":
                return self._send(bridge.bilibili_logout())
            if path == "/api/bilibili-interaction/draft":
                return self._send(bridge.create_bilibili_comment_draft(
                    str(body.get("target", "")), str(body.get("text", ""))))
            if path == "/api/bilibili-interaction/draft/clear":
                return self._send(bridge.clear_bilibili_comment_draft())
            if path == "/api/persona/save":
                return self._send(bridge.persona_save(body))
            if path == "/api/persona/create":
                return self._send(bridge.persona_create(str(body.get("profile_name", "新人格"))))
            if path == "/api/persona/activate":
                return self._send(bridge.persona_activate(str(body.get("id", "")), bool(body.get("enabled", True))))
            if path == "/api/persona/toggle":
                return self._send(bridge.persona_toggle(bool(body.get("enabled"))))
            if path == "/api/persona/delete":
                return self._send(bridge.persona_delete(str(body.get("id", ""))))
            if path == "/api/proactive/trigger":
                return self._send(bridge.proactive_trigger(
                    str(body.get("mode") or "normal"), str(body.get("action") or "")))
            if path == "/api/qq/start":
                return self._send(bridge.start_qq())
            if path == "/api/qq/stop":
                return self._send(bridge.stop_qq())
            if path == "/api/qq/reload":
                return self._send(bridge.reload_qq())
            if path == "/api/qq/fast-reply":
                return self._send(bridge.set_qq_fast_reply(bool(body.get("enabled"))))
            if path == "/api/memory-constellation/review":
                return self._send(bridge.memory_constellation_review(str(body.get("id", ""))))
            if path == "/api/memory-constellation/correct":
                return self._send(bridge.memory_constellation_correct(
                    str(body.get("id", "")), str(body.get("content", ""))))
            if path == "/api/memory-constellation/delete":
                return self._send(bridge.memory_constellation_delete(str(body.get("id", ""))))
            if path == "/api/ripple/simulate":
                return self._send(bridge.ripple_simulate(str(body.get("scenario", ""))))
            if path == "/api/ripple/restore":
                return self._send(bridge.ripple_restore())
            if path == "/api/ripple/configure":
                return self._send(bridge.ripple_configure(str(body.get("config", ""))))
            if path == "/api/ripple/clear":
                return self._send(bridge.ripple_clear())
            if path == "/api/data-tide/export":
                return self._send(bridge.data_tide_export(str(body.get("format", "json"))))
            if path == "/api/data-tide/mark-read":
                return self._send(bridge.data_tide_mark_read(str(body.get("ids", "[]"))))
            if path == "/api/study-room/rpc":
                return self._send(bridge.study_room_rpc(str(body.get("method", "")), body.get("args", [])))
            if path == "/api/time-capsule/rpc":
                return self._send(bridge.time_capsule_rpc(str(body.get("method", "")), body.get("args", [])))


            if path == "/api/legacy/open":
                return self._send(bridge.open_legacy_window(str(body.get("feature", ""))), 201)
            return self._send({"error": "Not found"}, 404)
        except Exception as exc:
            return self._send({"error": str(exc)}, 500)

    def do_PATCH(self):
        path = urlparse(self.path).path
        try:
            body = self._body()
            if path.startswith("/api/conversations/"):
                sid = int(path.split("/")[3])
                manager = bridge.agent().get_history_manager()
                if "title" in body:
                    manager.update_session_title(sid, str(body["title"]))
                if body.get("togglePin"):
                    manager.toggle_pin(sid)
                return self._send({"session": manager.get_session(sid)})
            return self._send({"error": "Not found"}, 404)
        except Exception as exc:
            return self._send({"error": str(exc)}, 500)

    def do_DELETE(self):
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/conversations/"):
                sid = int(path.split("/")[3])
                bridge.agent().get_history_manager().delete_session(sid)
                return self._send({"deleted": sid})
            return self._send({"error": "Not found"}, 404)
        except Exception as exc:
            return self._send({"error": str(exc)}, 500)


def _desktop_hotkey_trigger():
    """Toggle the desktop pet mode at bottom-right; launch it if not running."""
    try:
        import ctypes
        from ctypes import wintypes
        import subprocess
        user32 = ctypes.windll.user32
        title = "\u83b2\u5fc3 - \u684c\u5ba0\u6a21\u5f0f"
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            dlg = user32.FindWindowW(None, "\u83b2\u5fc3 - \u5bf9\u8bdd")
            if user32.IsWindowVisible(hwnd):
                user32.ShowWindow(hwnd, 0)
                if dlg:
                    user32.ShowWindow(dlg, 0)
            else:
                if dlg:
                    user32.ShowWindow(dlg, 5)
                user32.ShowWindow(hwnd, 5)
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                w = rect.right - rect.left
                h = rect.bottom - rect.top
                sw = user32.GetSystemMetrics(0)
                sh = user32.GetSystemMetrics(1)
                x = max(0, sw - w - 40)
                y = max(0, sh - h - 60)
                user32.SetWindowPos(hwnd, -1, x, y, 0, 0, 0x0001 | 0x0010 | 0x0040)
        else:
            launcher = Path(__file__).resolve().parent / "legacy_ui_launcher.py"
            subprocess.Popen([sys.executable, str(launcher), "galgame"])
    except Exception as exc:
        bridge_log.log("热键", f"热键触发错误: {exc}")


def _start_desktop_hotkey():
    """Register the global Ctrl+Alt+X hotkey in a background thread."""
    def _loop():
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            hotkey_id = 0x4C58
            mod_alt = 0x0001
            mod_control = 0x0002
            vk_x = 0x58
            if not user32.RegisterHotKey(None, hotkey_id, mod_control | mod_alt, vk_x):
                bridge_log.log("热键", "RegisterHotKey Ctrl+Alt+X 失败（可能已被占用）")
                return
            wm_hotkey = 0x0312
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == wm_hotkey and msg.wParam == hotkey_id:
                    _desktop_hotkey_trigger()
                else:
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            bridge_log.log("热键", f"热键错误: {exc}")
    threading.Thread(target=_loop, name="lianxin-desktop-hotkey", daemon=True).start()



def _existing_backend_running(timeout: float = 1.5) -> bool:
    """探测 127.0.0.1:8766 上是否已有莲心后端在响应。

    Windows 下 HTTPServer 默认带 SO_REUSEADDR，第二个实例不会报错，而是悄悄
    抢占同一个端口，表现为"两个莲心同时在跑、回复时好时坏、有时又绑定失败
    (WinError 10013)"。所以启动前先探测一次，从源头避免重复启动。
    """
    import urllib.request

    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:8766/api/app/status", timeout=timeout
        ) as response:
            return 200 <= int(response.status) < 300
    except Exception:
        return False


def main():
    bridge_log.install("debug.log")
    bridge_log.log("启动", "Lianxin AI2 后端启动 (api_server.py)")
    if _existing_backend_running():
        bridge_log.log("启动", "检测到 8766 已有莲心后端在运行，本次启动中止")
        print("=" * 62)
        print("[启动中止] 127.0.0.1:8766 上已经有一个莲心后端在运行。")
        print("  请先关掉旧的后端窗口（或结束 python.exe api_server.py）再重新启动。")
        print('  排查：netstat -ano | findstr ":8766"')
        print("=" * 62)
        return 1
    try:
        from config import get_api_config
        cfg = get_api_config()
        provider = str(cfg.get("provider", "deepseek") or "deepseek").strip()
        bridge_log.log("启动", f"当前 LLM provider: {provider}")
    except Exception as exc:
        bridge_log.log("启动", f"读取 LLM provider 配置失败: {exc}")
    _start_desktop_hotkey()
    bridge_log.log("启动", "桌面热键 Ctrl+Alt+X 注册完成")
    try:
        from PyQt5.QtCore import QCoreApplication
        if QCoreApplication.instance() is None:
            _QT_APP_HOLD = QCoreApplication([])
            globals()["_QT_APP_HOLD"] = _QT_APP_HOLD  # keep Qt app alive; silences early Qt warnings in skills/MCP threads
    except Exception as exc:
        bridge_log.log("启动", f"QCoreApplication 初始化失败: {exc}")
    try:
        from brain.skill_manager import discover_skills, activate_all_skills
        discover_skills()
        activate_all_skills()
        bridge_log.log("启动", "技能已激活")
    except Exception as exc:
        bridge_log.log("启动", f"技能激活失败: {exc}")
    try:
        from brain.mcp.mcp_manager import get_mcp_manager
        _mcp_mgr = get_mcp_manager()
        _mcp_mgr.initialize()
        bridge_log.log("启动", "MCP 服务扫描已启动")
    except Exception as exc:
        bridge_log.log("启动", f"MCP 初始化失败: {exc}")
    try:
        from brain.music_startup import establish_music_baseline
        _music_baseline = establish_music_baseline()
        bridge_log.log("音乐", f"启动基线: {_music_baseline}")
    except Exception as exc:
        bridge_log.log("音乐", f"启动基线失败: {exc}")
    try:
        from config import get_runtime_config
        if get_runtime_config().get("enabled", False):
            from brain.runtime.codex_backend import codex_missing_hint, default_codex_bin
            _codex_bin = default_codex_bin()
            if _codex_bin:
                bridge_log.log("执行引擎", f"后端已就绪: {_codex_bin}")
            else:
                bridge_log.log("执行引擎", f"后端不可用: {codex_missing_hint()}")
        else:
            bridge_log.log("执行引擎", "已在配置中关闭（runtime.enabled=false）")
    except Exception as exc:
        bridge_log.log("执行引擎", f"后端检测失败: {exc}")
    try:
        if bridge.install_execution_terminal_sink():
            bridge_log.log("执行引擎", "终态回灌通道已挂接（任务结束会自动回到会话）")
    except Exception as exc:
        bridge_log.log("执行引擎", f"终态回灌挂接失败: {exc}")
    try:
        bridge.start_proactive_runtime()
        bridge.start_music_watcher()
        bridge.start_music_state()
        bridge_log.log("主动", "主动聊天运行时已启动")
    except Exception as exc:
        bridge_log.log("主动", f"主动聊天运行时启动失败: {exc}")
    try:
        # 上次退出时还在跑的任务：补一张「非预期中断」卡片；顺手清理过期线程。
        _runtime = bridge.task_runtime()
        _recovered = _runtime.recover_interrupted()
        if _recovered.get("recovered"):
            bridge_log.log("执行引擎", f"已补记 {_recovered['recovered']} 个上次未完成的任务")
        _runtime.cleanup_sessions()
    except Exception as exc:
        bridge_log.log("执行引擎", f"启动恢复失败: {exc}")
    try:
        from config import get_qq_bridge_config
        qq_config = get_qq_bridge_config()
        if qq_config.get("enabled") and qq_config.get("auto_start"):
            bridge.start_qq()
            bridge_log.log("QQ", "QQ bridge auto-started")
    except Exception as exc:
        bridge_log.log("QQ", f"QQ bridge auto-start failed: {exc}")
    threading.Thread(target=_cover_cleanup_loop, name="cover-cache-cleanup", daemon=True).start()
    threading.Thread(target=_memory_heartbeat_loop, name="memory-heartbeat", daemon=True).start()
    threading.Thread(target=_watchdog_loop, name="bridge-watchdog", daemon=True).start()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 8766), Handler)
    except OSError as exc:
        # 端口被占用（WinError 10048/10013）多半是已经有一个莲心在运行，
        # 或上一个实例刚退出、端口仍在 TIME_WAIT。给可读提示而不是裸 traceback。
        bridge_log.log("启动", f"无法绑定 127.0.0.1:8766：{exc}")
        print("=" * 62)
        print(f"[启动失败] 无法监听 127.0.0.1:8766：{exc}")
        print("常见原因：")
        print("  1. 已经有一个莲心后端在运行 —— 先关掉旧的后端窗口再重试；")
        print("  2. 上一个实例刚退出，端口还在 TIME_WAIT —— 等几秒再启动；")
        print("  3. 安全软件 / 防火墙拦截了本次监听 —— 放行 python.exe 后重试。")
        print('排查：netstat -ano | findstr ":8766"   （确认是否有 LISTENING）')
        print("=" * 62)
        return 1
    bridge_log.log("启动", "HTTP 服务已就绪: http://127.0.0.1:8766")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        for stop in (bridge.stop_proactive_runtime, bridge.cancel_chat, bridge.stop_voice_input,
                     bridge.stop_voice, bridge.stop_call_sound):
            try:
                stop()
            except Exception:
                pass
        try:
            bridge.stop_qq()
        except Exception:
            pass
        try:
            bridge.stop_proactive_runtime()
        except Exception:
            pass
        try:
            bridge.stop_music_watcher()
        except Exception:
            pass
        try:
            bridge.stop_music_state()
        except Exception:
            pass
        try:
            bridge.shutdown_web_bridges()
        except Exception:
            pass
        from utils.app_lifecycle import stop_owned_processes
        stop_owned_processes()
    bridge_log.log("退出", "后端已关闭")


if __name__ == "__main__":
    sys.exit(main())
