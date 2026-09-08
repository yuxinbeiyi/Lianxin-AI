"""Fetch model IDs from OpenAI-compatible providers without blocking Qt."""

from __future__ import annotations

import requests
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import QDialog, QLineEdit, QListWidget, QPushButton, QVBoxLayout, QHBoxLayout, QLabel

_TIMEOUT = (10.0, 20.0)


def normalize_base_urls(api_base: str) -> list[str]:
    base = (api_base or "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[:-len("/chat/completions")].rstrip("/")
    if not base.startswith(("http://", "https://")):
        return []
    if base.endswith("/models"):
        return [base]
    if base.rsplit("/", 1)[-1] in {"v1", "v2", "v3", "v4", "api", "openai"}:
        return [base + "/models"]
    return [base + "/v1/models", base + "/models"]


def fetch_model_ids(api_base: str, api_key: str) -> list[str]:
    candidates = normalize_base_urls(api_base)
    if not candidates:
        raise RuntimeError("Base URL 必须以 http:// 或 https:// 开头")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    last_error = "未知错误"
    session = requests.Session()
    try:
        session.trust_env = False
        for url in candidates:
            try:
                response = session.get(url, headers=headers, timeout=_TIMEOUT)
            except requests.RequestException as exc:
                last_error = f"网络请求失败：{type(exc).__name__}"
                continue
            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}（{url}）"
                continue
            try:
                payload = response.json()
            except ValueError:
                last_error = f"接口返回的不是 JSON（{url}）"
                continue
            values = payload.get("data", payload.get("models", payload)) if isinstance(payload, dict) else payload
            if not isinstance(values, list):
                last_error = f"返回结构中没有模型列表（{url}）"
                continue
            ids = {
                str(item.get("id") or item.get("name") or "").strip()
                for item in values if isinstance(item, dict)
            }
            ids.update(str(item).strip() for item in values if isinstance(item, str))
            ids.discard("")
            if ids:
                return sorted(ids)
            last_error = f"接口返回空模型列表（{url}）"
    finally:
        session.close()
    raise RuntimeError(last_error)


class ModelListFetcher(QThread):
    fetched = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, api_base, api_key, parent=None):
        super().__init__(parent)
        self.api_base, self.api_key = api_base, api_key

    def run(self):
        try:
            self.fetched.emit(fetch_model_ids(self.api_base, self.api_key))
        except Exception as exc:
            self.failed.emit(str(exc))


class ModelPickerDialog(QDialog):
    def __init__(self, models, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择模型")
        self.resize(440, 520)
        self._picked = ""
        root = QVBoxLayout(self)
        root.addWidget(QLabel(f"可用模型（共 {len(models)} 个）"))
        search = QLineEdit()
        search.setPlaceholderText("搜索模型")
        root.addWidget(search)
        listing = QListWidget()
        root.addWidget(listing, 1)
        actions = QHBoxLayout(); actions.addStretch()
        cancel = QPushButton("取消"); use = QPushButton("使用")
        actions.addWidget(cancel); actions.addWidget(use); root.addLayout(actions)

        def refresh(value=""):
            keyword = value.strip().lower(); listing.clear()
            listing.addItems([model for model in models if not keyword or keyword in model.lower()])
            if listing.count(): listing.setCurrentRow(0)
        def confirm(*_):
            if listing.currentItem():
                self._picked = listing.currentItem().text(); self.accept()
        search.textChanged.connect(refresh); search.returnPressed.connect(confirm)
        listing.itemDoubleClicked.connect(confirm); use.clicked.connect(confirm); cancel.clicked.connect(self.reject)
        refresh()

    def picked(self):
        return self._picked


def run_model_fetch(api_base, api_key, on_ok, on_fail, on_settle=None):
    fetcher = ModelListFetcher(api_base, api_key)
    def settle():
        if on_settle:
            on_settle()
        fetcher.deleteLater()
    fetcher.fetched.connect(on_ok); fetcher.failed.connect(on_fail)
    fetcher.finished.connect(settle); fetcher.start()
    return fetcher


def show_model_picker(anchor, models):
    dialog = ModelPickerDialog(models, anchor)
    dialog.exec_()
    return dialog.picked()
