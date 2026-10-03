"""Canvas-based Memory Constellations view.

This is a deliberately separate renderer from the native Qt Memory Universe:
it gives Lianxin a browser-style, cached Canvas 2D star map for comparison and
future visual experimentation without changing the underlying memory model.
"""
from __future__ import annotations

import json
from pathlib import Path

from PyQt5.QtCore import QEvent, QUrl, Qt, QObject, pyqtSlot
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QDialog, QMainWindow, QVBoxLayout, QTextBrowser, QPushButton, QWidget
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineSettings
from PyQt5.QtWebChannel import QWebChannel


class _ConstellationBridge(QObject):
    """Small, read-only bridge used by the Canvas page.

    The page is deliberately not allowed to mutate memory directly.  It asks
    the Python side for a fresh snapshot and opens source messages through the
    existing database layer, keeping the visualizer safe and auditable.
    """

    def __init__(self, snapshot_provider=None, window=None, parent=None):
        super().__init__(parent)
        self._snapshot_provider = snapshot_provider
        self._window = window

    @pyqtSlot(result=bool)
    def toggleFullscreen(self):
        """Toggle the native Qt window; WebEngine fullscreen is permission-bound."""
        if self._window is None:
            return False
        if self._window.isFullScreen():
            self._window.showNormal()
            return False
        self._window.showFullScreen()
        return True

    @pyqtSlot(result=str)
    def refreshSnapshot(self):
        try:
            payload = self._snapshot_provider() if self._snapshot_provider else {}
            return json.dumps(payload, ensure_ascii=False, default=str)
        except Exception as exc:
            return json.dumps({"error": f"snapshot refresh failed: {exc}"}, ensure_ascii=False)

    @pyqtSlot(str)
    def openOriginalMessages(self, raw_ids: str):
        try:
            ids = sorted({int(value) for value in json.loads(raw_ids or "[]") if str(value).isdigit()})
        except (TypeError, ValueError, json.JSONDecodeError):
            ids = []
        dialog = QDialog()
        dialog.setWindowTitle("原始消息来源")
        dialog.resize(760, 520)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser(dialog)
        try:
            from brain.graph_memory import _get_conn
            if not ids:
                browser.setPlainText("该记忆没有可定位的原始消息编号。")
            else:
                placeholders = ",".join("?" for _ in ids)
                rows = _get_conn().execute(
                    f"SELECT id,session_id,role,content,timestamp FROM messages WHERE id IN ({placeholders}) ORDER BY timestamp,id",
                    tuple(ids),
                ).fetchall()
                browser.setPlainText("\n\n".join(
                    f"[{row['timestamp'] or ''}] #{row['id']} {row['role'] or ''}\n{row['content'] or ''}"
                    for row in rows
                ) or "原始消息已不存在。")
        except Exception as exc:
            browser.setPlainText(f"读取原始消息失败：{exc}")
        layout.addWidget(browser)
        close = QPushButton("关闭", dialog)
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec_()

    @pyqtSlot(str, result=bool)
    def queueMemoryReview(self, raw_id: str):
        """Mark a fact for human review without deleting or rewriting it."""
        try:
            fact_id = int(raw_id)
            if fact_id <= 0:
                return False
            from brain.graph_memory import _get_conn
            conn = _get_conn()
            cur = conn.execute(
                "UPDATE memory_facts SET review_status='needs_confirmation', quality_updated_at=datetime('now','localtime') WHERE id=? AND status='active'",
                (fact_id,),
            )
            conn.commit()
            return cur.rowcount > 0
        except Exception:
            return False

    @pyqtSlot(str, str, result=str)
    def correctMemory(self, raw_id: str, content: str):
        try:
            fact_id = int(raw_id)
            if fact_id <= 0 or not str(content or '').strip():
                return json.dumps({'ok': False, 'error': '记忆内容不能为空'}, ensure_ascii=False)
            from brain.graph_memory import correct_fact_by_id
            fact = correct_fact_by_id(fact_id, content)
            return json.dumps({'ok': bool(fact), 'fact': fact, 'error': '' if fact else '记忆不存在'}, ensure_ascii=False, default=str)
        except Exception as exc:
            return json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False)

    @pyqtSlot(str, result=str)
    def deleteMemory(self, raw_id: str):
        try:
            fact_id = int(raw_id)
            if fact_id <= 0:
                return json.dumps({'ok': False, 'error': '无效的记忆编号'}, ensure_ascii=False)
            from brain.graph_memory import delete_fact_by_id
            ok = delete_fact_by_id(fact_id)
            return json.dumps({'ok': ok, 'error': '' if ok else '记忆不存在'}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False)

    @pyqtSlot(str, result=str)
    def simulateEmotion(self, scenario: str):
        try:
            from brain.emotional import get_manager
            result = get_manager().simulate_scenario(str(scenario or ""))
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as exc:
            return json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=False)

    @pyqtSlot(result=str)
    def restoreEmotionSimulation(self):
        try:
            from brain.emotional import get_manager
            return json.dumps(get_manager().restore_simulation(), ensure_ascii=False, default=str)
        except Exception as exc:
            return json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=False)

    @pyqtSlot(str, result=bool)
    def configureEmotion(self, raw_config: str):
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
            return True
        except Exception:
            return False

    @pyqtSlot(result=bool)
    def clearEmotionSimulation(self):
        try:
            from brain.emotional import get_manager
            get_manager().clear_simulation_events()
            return True
        except Exception:
            return False


class MemoryConstellationWebWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("莲心 · 记忆星图系统")
        self.setMinimumSize(1120, 720)
        self.resize(1500, 920)
        self.setWindowFlags(Qt.Window)
        self._asset_dir = Path(__file__).resolve().parent.parent / "assets" / "memory_constellation"
        self._view = QWebEngineView(self)
        self._view.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self._view.page().setBackgroundColor(QColor("#020410"))
        # Keep accelerated rendering enabled for smooth animation. The earlier
        # flicker was caused by forced transparent compositing layers, which
        # have been removed from the page CSS; disabling the GPU made the map
        # visibly sluggish on larger memory graphs.
        settings = self._view.settings()
        settings.setAttribute(QWebEngineSettings.Accelerated2dCanvasEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebGLEnabled, True)
        self._bridge = _ConstellationBridge(self._snapshot, self)
        channel = QWebChannel(self._view)
        channel.registerObject("lianxinBridge", self._bridge)
        self._view.page().setWebChannel(channel)
        self._view.setContextMenuPolicy(Qt.NoContextMenu)
        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)
        self.setCentralWidget(root)
        self._load_map()

    def _snapshot(self):
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
                item["label"] = item.get("content") or "未命名记忆"
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
                        if key == "source_fact_ids": fact_ids.extend(int(v) for v in values)
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
                source_ids[f"entity:{item.get('id')}"] = collect_sources(item)
                source_ids.setdefault(str(item.get("id")), source_ids[f"entity:{item.get('id')}"])
            for item in episodes:
                source_ids[f"episode:{item.get('id')}"] = collect_sources(item)
                source_ids.setdefault(str(item.get("id")), source_ids[f"episode:{item.get('id')}"])
            for item in facts:
                source_ids[f"fact:{item.get('id')}"] = collect_sources(item)
                source_ids.setdefault(str(item.get("id")), source_ids[f"fact:{item.get('id')}"])
            for saga in sagas:
                try:
                    episode_ids = [int(v) for v in json.loads(saga.get("episode_ids", "[]") or "[]")]
                except (TypeError, ValueError, json.JSONDecodeError):
                    episode_ids = []
                source_ids[f"saga:{saga.get('id')}"] = sorted({sid for eid in episode_ids for sid in source_ids.get(f"episode:{eid}", source_ids.get(str(eid), []))})
                source_ids.setdefault(str(saga.get("id")), source_ids[f"saga:{saga.get('id')}"])
        except Exception:
            pass
        try:
            from brain.persona.manager import PersonaManager
            assistant_name = PersonaManager().get_snapshot().profile.assistant_name or "莲心"
        except Exception:
            assistant_name = "莲心"
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
        payload = {
            "entities": entities, "episodes": episodes, "sagas": sagas, "facts": facts,
            "events": list_narrative_events(80), "source_ids": source_ids,
            "core": {"user": get_user_name() or "主人", "assistant": assistant_name},
            "model": get_last_narrative_run() or {"status": "未运行"},
            "maintenance": maintenance or {"status": "未运行", "stats": {}},
            "health": health,
            "diagnostics": diagnostics,
        }
        return payload

    def _load_map(self):
        payload = self._snapshot()
        template = (self._asset_dir / "index.html").read_text(encoding="utf-8")
        injected = "<script>window.LIANXIN_MEMORY_DATA=" + json.dumps(
            payload, ensure_ascii=False, default=str
        ) + ";</script>"
        html = template.replace("<!-- LIANXIN_DATA -->", injected)
        self._view.setHtml(html, QUrl.fromLocalFile(str(self._asset_dir) + "/"))

    def showEvent(self, event):
        super().showEvent(event)
        self._view.page().runJavaScript(
            "window.lianxinSetConstellationActive && window.lianxinSetConstellationActive(true);"
        )

    def hideEvent(self, event):
        self._view.page().runJavaScript(
            "window.lianxinSetConstellationActive && window.lianxinSetConstellationActive(false);"
        )
        super().hideEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            active = not self.isMinimized() and self.isVisible()
            self._view.page().runJavaScript(
                "window.lianxinSetConstellationActive && "
                f"window.lianxinSetConstellationActive({'true' if active else 'false'});"
            )

    def refresh(self):
        self._load_map()
