"""
ApiConfigDialog：API Key 配置对话框
支持填写 DeepSeek API、QQ 桥接等配置信息。
语音识别相关配置已统一迁移至「语音转录中心」。
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QSpinBox, QFrame, QMessageBox, QTabWidget,
    QWidget, QFormLayout, QCheckBox, QComboBox, QApplication,
    QDoubleSpinBox,
    QRadioButton, QButtonGroup,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont

from config import (
    get_api_config, save_api_config,
    normalize_local_base_url, normalize_local_model_name,
    get_qq_bridge_config, save_qq_bridge_config,
    get_siliconflow_config, save_siliconflow_config,
    get_qweather_config, save_qweather_config,
    get_tavily_config, save_tavily_config,
    get_firecrawl_config, save_firecrawl_config,

)

# ── 测试 DeepSeek 连接的后台线程 ──────────────────────────────

class _TestWorker(QThread):
    success = pyqtSignal(str)
    failed  = pyqtSignal(str)

    def __init__(self, api_key: str, base_url: str, model: str, is_local: bool = False, parent=None):
        super().__init__(parent)
        self._api_key  = api_key
        self._base_url = base_url
        self._model    = model
        self._is_local = is_local

    def run(self):
        try:
            from openai import OpenAI
            client = OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=30.0,  # 30 秒超时，避免 API 故障时永远卡住
            )
            kwargs = dict(
                model=self._model,
                max_tokens=16,
                messages=[{"role": "user", "content": "你好"}],
                timeout=30.0,
            )
            # 本地模型温度可能需要设为 0 避免随机性
            if self._is_local:
                kwargs["temperature"] = 0.0
            resp = client.chat.completions.create(**kwargs)
            reply = resp.choices[0].message.content or ""
            self.success.emit(reply[:20])
        except Exception as e:
            self.failed.emit(str(e))


class _BalanceWorker(QThread):
    success = pyqtSignal(dict)
    failed  = pyqtSignal(str)

    def __init__(self, api_key: str, parent=None):
        super().__init__(parent)
        self._api_key = api_key

    def run(self):
        from utils.balance import get_balance_info
        result, error = get_balance_info(self._api_key)
        if error:
            self.failed.emit(error)
        else:
            self.success.emit(result)


class _ImageGenTestWorker(QThread):
    """在工作线程中执行生图和下载，结果通过 Qt 信号回到界面线程。"""
    success = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, endpoint: str, api_key: str, body: dict, siliconflow: bool = False, parent=None):
        super().__init__(parent)
        self._endpoint = endpoint
        self._api_key = api_key
        self._body = body
        self._siliconflow = siliconflow

    def run(self):
        try:
            import base64
            import os
            import time
            import requests

            resp = requests.post(
                self._endpoint,
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                json=self._body,
                timeout=120,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            if self._siliconflow and data.get("code") not in (None, 20000, 0):
                raise RuntimeError(str(data.get("message") or data.get("msg") or "SiliconFlow 返回业务错误"))
            images = data.get("data", []) or data.get("images", [])
            if isinstance(images, str):
                image_value = images.strip()
            else:
                if isinstance(images, dict):
                    images = [images]
                image_value = (images[0].get("url") or images[0].get("b64_json")) if images else ""
            if not image_value:
                raise RuntimeError("返回数据中未找到图片 URL")

            save_dir = os.path.join(os.path.expanduser("~"), ".lianxin", "generated_images")
            os.makedirs(save_dir, exist_ok=True)
            save_path = os.path.join(save_dir, f"test_{int(time.time())}.png")
            if image_value.startswith("http"):
                image_resp = requests.get(image_value, timeout=60)
                image_resp.raise_for_status()
                content = image_resp.content
            else:
                content = base64.b64decode(image_value)
            with open(save_path, "wb") as image_file:
                image_file.write(content)
            self.success.emit(save_path)
        except Exception as exc:
            self.failed.emit(str(exc))


# ── 对话框主体 ────────────────────────────────────────────────

class ApiConfigDialog(QDialog):
    config_saved = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("API 配置")
        self.setMinimumWidth(560)
        self.setMinimumHeight(500)
        self.resize(600, 600)
        self.setWindowFlags(Qt.Window)
        
        self._test_worker: _TestWorker | None = None
        self._build_ui()
        self._load()

    # ── 界面构建 ──────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 24, 24, 24)

        # 标题
        title = QLabel("🔑 API 配置")
        title.setFont(QFont("Microsoft YaHei UI", 14, QFont.Bold))
        title.setStyleSheet("color: #3A3A5C;")
        layout.addWidget(title)

        desc = QLabel(
            "配置 DeepSeek API Key 和 QQ 桥接等参数。\n"
            "语音识别相关配置请使用「语音转录中心」管理。\n"
            "所有信息仅保存在本地 data/user_config.json，不会上传到任何服务器。"
        )
        desc.setFont(QFont("Microsoft YaHei UI", 9))
        desc.setStyleSheet("color: #888888;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: #E0E0E8; max-height: 1px;")
        layout.addWidget(line)

        # ── Tab 页 ────────────────────────────────────────────
        tabs = QTabWidget()
        tabs.setFont(QFont("Microsoft YaHei UI", 9))
        tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #3D3D5A;
                border-radius: 6px;
                background-color: #1E1E30;
            }
            QTabBar::tab {
                background-color: #1E1E30;
                border: 1px solid #D8D8EE;
                border-bottom: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                padding: 8px 20px;
                margin-right: 2px;
                
            }
            QTabBar::tab:selected {
                background-color: #FFFFFF;
                color: #3A3A5C;
                font-weight: bold;
                border-bottom: 1px solid #FFFFFF;
            }
            QTabBar::tab:hover:!selected {
                background-color: #E4E4F0;
            }
        """)
        self._tab_widget = tabs

        # Tab 0: DeepSeek API
        tab_ds = QWidget()
        self._build_tab_deepseek(tab_ds)
        tabs.addTab(tab_ds, "DeepSeek API")

        # Tab 1: NapCat QQ 聊天
        tab_qq = QWidget()
        self._build_tab_qq(tab_qq)
        tabs.addTab(tab_qq, "NapCat QQ 聊天")

        # Tab 3: 视觉理解 (SiliconFlow)
        tab_vision = QWidget()
        self._build_tab_siliconflow(tab_vision)
        tabs.addTab(tab_vision, "视觉理解")

        # Tab 4: 创作生图 (Agnes Image)
        tab_image = QWidget()
        self._build_tab_image_gen(tab_image)
        tabs.addTab(tab_image, "🎨 创作生图")

        # Tab 4.5: 创作视频 (Agnes Video)
        tab_video = QWidget()
        self._build_tab_video_gen(tab_video)
        tabs.addTab(tab_video, "🎬 创作视频")

        # Tab 5: 和风天气
        tab_qw = QWidget()
        self._build_tab_qweather(tab_qw)
        tabs.addTab(tab_qw, "☁️ 和风天气")

        layout.addWidget(tabs)

        # ── 底部按钮区 ──
        btn_row = QHBoxLayout()

        self._test_btn = QPushButton("测试 DeepSeek 连接")
        self._test_btn.setFixedHeight(36)
        self._test_btn.setFont(QFont("Microsoft YaHei UI", 9))
        self._test_btn.setCursor(Qt.PointingHandCursor)
        self._test_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF9500;
                color: white;
                border-radius: 8px;
                border: none;
                padding: 0 16px;
            }
            QPushButton:hover   { background-color: #E08600; }
            QPushButton:pressed { background-color: #C07600; }
            QPushButton:disabled{ background-color: #CCCCCC; }
        """)
        self._test_btn.clicked.connect(self._on_test)
        btn_row.addWidget(self._test_btn)

        btn_row.addStretch()

        btn_cancel = QPushButton("取消")
        btn_cancel.setFixedSize(80, 36)
        btn_cancel.setFont(QFont("Microsoft YaHei UI", 9))
        btn_cancel.setCursor(Qt.PointingHandCursor)
        btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: #1E1E30;
                 
                border-radius: 8px;
                border: 1px solid #D8D8EE;
            }
            QPushButton:hover { background-color: #E4E4F0; }
        """)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_save = QPushButton("保存")
        btn_save.setFixedSize(80, 36)
        btn_save.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
        btn_save.setCursor(Qt.PointingHandCursor)
        btn_save.setStyleSheet("""
            QPushButton {
                background-color: #6C7BFF;
                color: white;
                border-radius: 8px;
                border: none;
            }
            QPushButton:hover   { background-color: #5A6AEE; }
            QPushButton:pressed { background-color: #4A5ADE; }
        """)
        btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(btn_save)

        layout.addLayout(btn_row)

    # ── Tab: DeepSeek API ───────────────────────────────────

    def _build_tab_deepseek(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(16, 20, 16, 16)

        # ── Provider 选择器（RadioButton 三选一） ──
        provider_label = QLabel("选择 AI 提供商：")
        provider_label.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        provider_label.setStyleSheet("color: #3A3A5C;")
        layout.addWidget(provider_label)

        provider_row = QHBoxLayout()
        provider_row.setSpacing(12)

        self._provider_group = QButtonGroup(self)

        self._radio_deepseek = QRadioButton("DeepSeek（云端）")
        self._radio_agnes = QRadioButton("Agnes AI（云端）")
        self._radio_local = QRadioButton("Ollama（本地）")

        radio_style = """
            QRadioButton {
                color: #3A3A5C;
                spacing: 6px;
                padding: 8px 14px;
                background-color: #1E1E30;
                border-radius: 8px;
                border: 1px solid #D8D8EE;
                font-size: 9pt;
            }
            QRadioButton:hover { background-color: #E4E4F0; }
            QRadioButton:checked { background-color: #ECEEFF; border: 1px solid #6C7BFF; color: #5060DD; font-weight: bold; }
            QRadioButton::indicator { width: 16px; height: 16px; }
        """
        for rb in [self._radio_deepseek, self._radio_agnes, self._radio_local]:
            rb.setFont(QFont("Microsoft YaHei UI", 9))
            rb.setStyleSheet(radio_style)
            self._provider_group.addButton(rb)
            provider_row.addWidget(rb)

        provider_row.addStretch()
        layout.addLayout(provider_row)
        layout.addSpacing(12)

        # ── 提示文本 ──
        self._provider_hint = QLabel("")
        self._provider_hint.setFont(QFont("Microsoft YaHei UI", 8))
        self._provider_hint.setStyleSheet("color: #999999; background-color: #1E1E30; padding: 8px; border-radius: 6px;")
        self._provider_hint.setWordWrap(True)
        layout.addWidget(self._provider_hint)
        layout.addSpacing(4)

        # ── DeepSeek 配置分组 ──
        self._deepseek_group = QFrame()
        self._deepseek_group.setStyleSheet("QFrame { border: none; }")
        ds_layout = QVBoxLayout(self._deepseek_group)
        ds_layout.setContentsMargins(0, 0, 0, 0)
        ds_form = QFormLayout()
        ds_form.setSpacing(16)
        ds_form.setContentsMargins(0, 0, 0, 0)

        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText("sk-xxxxxxxxxxxxxxxxxxxxxxxx")
        self._key_edit.setEchoMode(QLineEdit.Password)
        self._key_edit.setFont(QFont("Consolas", 10))
        self._apply_field_style(self._key_edit)
        key_layout = QHBoxLayout()
        key_layout.addWidget(self._key_edit)
        self._show_btn = QPushButton("显示")
        self._show_btn.setFixedSize(52, 32)
        self._show_btn.setCheckable(True)
        self._show_btn.setCursor(Qt.PointingHandCursor)
        self._show_btn.setFont(QFont("Microsoft YaHei UI", 8))
        self._show_btn.setStyleSheet("""
            QPushButton {
                background-color: #ECEEFF;
                color: #5060DD;
                border-radius: 6px;
                border: 1px solid #C8CCEE;
            }
            QPushButton:checked { background-color: #5060DD; color: white; }
            QPushButton:hover { background-color: #DDE0FF; }
        """)
        self._show_btn.toggled.connect(self._toggle_key_visibility)
        key_layout.addWidget(self._show_btn)
        ds_form.addRow("API Key:", key_layout)

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("https://api.deepseek.com")
        self._url_edit.setFont(QFont("Consolas", 10))
        self._apply_field_style(self._url_edit)
        ds_form.addRow("Base URL:", self._url_edit)

        self._model_edit = QLineEdit()
        self._model_edit.setPlaceholderText("deepseek-v4-flash")
        self._model_edit.setFont(QFont("Consolas", 10))
        self._apply_field_style(self._model_edit)
        ds_form.addRow("模型名称:", self._model_edit)

        self._api_format_combo = QComboBox()
        self._api_format_combo.addItems(["openai", "anthropic"])
        self._api_format_combo.setFixedHeight(34)
        self._api_format_combo.setFont(QFont("Microsoft YaHei UI", 10))
        self._api_format_combo.setStyleSheet("""
            QComboBox {
                border: 1px solid #D8D8EE; border-radius: 8px; padding: 4px 8px;
                background-color: #FFFFFF; color: #2C2C2C;
            }
            QComboBox:focus { border: 1px solid #6C7BFF; }
            QComboBox::drop-down { border: none; width: 24px; }
            QComboBox QAbstractItemView {
                background-color: #FFFFFF; border: 1px solid #D8D8EE;
                selection-background-color: #ECEEFF; color: #2C2C2C;
            }
        """)
        ds_form.addRow("API 格式:", self._api_format_combo)

        self._tokens_spin = QSpinBox()
        self._tokens_spin.setRange(512, 32768)
        self._tokens_spin.setSingleStep(512)
        self._tokens_spin.setFixedHeight(34)
        self._tokens_spin.setFont(QFont("Microsoft YaHei UI", 10))
        self._tokens_spin.setStyleSheet("""
            QSpinBox {
                border: 1px solid #D8D8EE; border-radius: 8px; padding: 4px 8px;
                background-color: #FFFFFF; color: #2C2C2C;
            }
            QSpinBox:focus { border: 1px solid #6C7BFF; }
        """)
        ds_form.addRow("最大 Token 数:", self._tokens_spin)

        ds_layout.addLayout(ds_form)

        self._balance_btn = QPushButton("💰 查询余额")
        self._balance_btn.setFixedHeight(36)
        self._balance_btn.setFont(QFont("Microsoft YaHei UI", 9))
        self._balance_btn.setCursor(Qt.PointingHandCursor)
        self._balance_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF9500; color: white; border-radius: 8px;
                border: none; padding: 0 16px;
            }
            QPushButton:hover   { background-color: #E08600; }
            QPushButton:pressed { background-color: #C07600; }
            QPushButton:disabled{ background-color: #CCCCCC; }
        """)
        self._balance_btn.clicked.connect(self._on_balance_query)
        ds_layout.addWidget(self._balance_btn)

        layout.addWidget(self._deepseek_group)

        # ── Agnes AI 配置分组 ──
        self._agnes_group = QFrame()
        self._agnes_group.setStyleSheet("QFrame { border: none; }")
        ag_layout = QVBoxLayout(self._agnes_group)
        ag_layout.setContentsMargins(0, 0, 0, 0)
        ag_form = QFormLayout()
        ag_form.setSpacing(16)
        ag_form.setContentsMargins(0, 0, 0, 0)

        self._agnes_key_edit = QLineEdit()
        self._agnes_key_edit.setPlaceholderText("sk-xxxxxxxxxxxxxxxxxxxxxxxx")
        self._agnes_key_edit.setEchoMode(QLineEdit.Password)
        self._agnes_key_edit.setFont(QFont("Consolas", 10))
        self._apply_field_style(self._agnes_key_edit)
        ag_key_layout = QHBoxLayout()
        ag_key_layout.addWidget(self._agnes_key_edit)
        self._show_agnes_btn = QPushButton("显示")
        self._show_agnes_btn.setFixedSize(52, 32)
        self._show_agnes_btn.setCheckable(True)
        self._show_agnes_btn.setCursor(Qt.PointingHandCursor)
        self._show_agnes_btn.setFont(QFont("Microsoft YaHei UI", 8))
        self._show_agnes_btn.setStyleSheet("""
            QPushButton {
                background-color: #ECEEFF; color: #5060DD;
                border-radius: 6px; border: 1px solid #C8CCEE;
            }
            QPushButton:checked { background-color: #5060DD; color: white; }
            QPushButton:hover { background-color: #DDE0FF; }
        """)
        self._show_agnes_btn.toggled.connect(self._toggle_agnes_key_visibility)
        ag_key_layout.addWidget(self._show_agnes_btn)
        ag_form.addRow("API Key:", ag_key_layout)

        self._agnes_model_edit = QLineEdit()
        self._agnes_model_edit.setPlaceholderText("agnes-2.0-flash")
        self._agnes_model_edit.setFont(QFont("Consolas", 10))
        self._apply_field_style(self._agnes_model_edit)
        ag_form.addRow("模型名称:", self._agnes_model_edit)

        ag_layout.addLayout(ag_form)

        # Agnes 测试连接按钮
        self._agnes_test_btn = QPushButton("🔗 测试 Agnes 连接")
        self._agnes_test_btn.setFixedHeight(36)
        self._agnes_test_btn.setFont(QFont("Microsoft YaHei UI", 9))
        self._agnes_test_btn.setCursor(Qt.PointingHandCursor)
        self._agnes_test_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF9500; color: white; border-radius: 8px;
                border: none; padding: 0 16px;
            }
            QPushButton:hover   { background-color: #E08600; }
            QPushButton:pressed { background-color: #C07600; }
            QPushButton:disabled{ background-color: #CCCCCC; }
        """)
        self._agnes_test_btn.clicked.connect(self._on_agnes_test)
        ag_layout.addWidget(self._agnes_test_btn)

        self._agnes_group.hide()
        layout.addWidget(self._agnes_group)

        # ── 本地 Ollama 配置分组 ──
        self._local_group = QFrame()
        self._local_group.setStyleSheet("QFrame { border: none; }")
        local_layout = QVBoxLayout(self._local_group)
        local_layout.setContentsMargins(0, 0, 0, 0)
        local_form = QFormLayout()
        local_form.setSpacing(16)
        local_form.setContentsMargins(0, 0, 0, 0)

        self._local_url_edit = QLineEdit()
        self._local_url_edit.setPlaceholderText("http://localhost:11434/v1")
        self._local_url_edit.setFont(QFont("Consolas", 10))
        self._apply_field_style(self._local_url_edit)
        local_form.addRow("Ollama 地址:", self._local_url_edit)

        self._local_model_edit = QLineEdit()
        self._local_model_edit.setPlaceholderText("qwen2.5:3b-instruct")
        self._local_model_edit.setFont(QFont("Consolas", 10))
        self._apply_field_style(self._local_model_edit)
        local_form.addRow("本地模型名:", self._local_model_edit)

        self._router_model_edit = QLineEdit()
        self._router_model_edit.setPlaceholderText("my-qwen（留空则回退到规则路由）")
        self._router_model_edit.setFont(QFont("Consolas", 10))
        self._apply_field_style(self._router_model_edit)
        local_form.addRow("路由模型名:", self._router_model_edit)

        local_layout.addLayout(local_form)
        self._local_group.hide()
        layout.addWidget(self._local_group)

        layout.addStretch()

        # ── 连接 radio 切换信号 ──
        self._provider_group.buttonClicked.connect(self._on_provider_changed)

    def _on_provider_changed(self, btn: QRadioButton):
        """切换 AI 提供商时更新 UI 可见性。"""
        if btn is self._radio_deepseek:
            self._deepseek_group.setVisible(True)
            self._agnes_group.setVisible(False)
            self._local_group.setVisible(False)
            self._provider_hint.setText(
                "💡 DeepSeek V4 模型，支持 Function Calling 工具调用。\n"
                "    需在 platform.deepseek.com 注册并申请 API Key。"
            )
            self._provider_hint.show()
            self._test_btn.setText("测试 DeepSeek 连接")
            self._test_btn.show()
            self._agnes_test_btn.hide()
        elif btn is self._radio_agnes:
            self._deepseek_group.setVisible(False)
            self._agnes_group.setVisible(True)
            self._local_group.setVisible(False)
            self._provider_hint.setText(
                "💡 Agnes AI 免费大模型，OpenAI 兼容协议，1M 上下文窗口，支持 Function Calling。\n"
                "    需在 platform.agnes-ai.com 注册并申请 API Key。完全免费，不限量调用。"
            )
            self._provider_hint.show()
            self._test_btn.hide()
            self._agnes_test_btn.show()
        elif btn is self._radio_local:
            self._deepseek_group.setVisible(False)
            self._agnes_group.setVisible(False)
            self._local_group.setVisible(True)
            self._provider_hint.setText(
                "💡 使用本地 Ollama 部署的模型，无需联网，不消耗 API 额度。\n"
                "    注意：本地模型不支持工具调用（打开应用、文件操作等），仅限纯文本聊天。"
            )
            self._provider_hint.show()
            self._test_btn.setText("测试本地模型连接")
            self._test_btn.show()
            self._agnes_test_btn.hide()

    def _toggle_agnes_key_visibility(self, checked: bool):
        self._agnes_key_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self._show_agnes_btn.setText("隐藏" if checked else "显示")

    # ── Tab: NapCat QQ 聊天 ──────────────────────────────────

    def _build_tab_qq(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(16, 20, 16, 16)
        form = QFormLayout()
        form.setSpacing(16)
        form.setContentsMargins(0, 0, 0, 0)

        # QQ 账号（机器人）
        self._qq_account = QLineEdit()
        self._qq_account.setPlaceholderText("机器人 QQ 号")
        self._apply_field_style(self._qq_account)
        form.addRow("QQ 账号:", self._qq_account)

        # WebSocket 地址
        self._ws_url = QLineEdit()
        self._ws_url.setPlaceholderText("ws://127.0.0.1:3001")
        self._ws_url.setFont(QFont("Consolas", 10))
        self._apply_field_style(self._ws_url)
        form.addRow("WebSocket 地址:", self._ws_url)

        # 主人 QQ 号
        self._owner_qq = QLineEdit()
        self._owner_qq.setPlaceholderText("主人的 QQ 号")
        self._apply_field_style(self._owner_qq)
        form.addRow("主人 QQ 号:", self._owner_qq)

        # 主人称呼
        self._owner_name = QLineEdit()
        self._owner_name.setPlaceholderText("主人")
        self._owner_name.setText("主人")
        self._apply_field_style(self._owner_name)
        form.addRow("主人称呼:", self._owner_name)

        # 自动连接
        self._auto_enable = QCheckBox("程序启动时自动连接 QQ")
        self._auto_enable.setFont(QFont("Microsoft YaHei UI", 9))
        self._auto_enable.setStyleSheet("spacing: 6px;")
        form.addRow("", self._auto_enable)

        layout.addLayout(form)

        # 帮助提示
        help_text = QLabel(
            "💡 使用前需要自行部署 NapCatQQ（开源 OneBot v11 实现），\n"
            "    并在 NapCatQQ 配置中开启 WebSocket 服务端。\n"
            "    莲心AI 不支持也无法内置 NapCatQQ。"
        )
        help_text.setFont(QFont("Microsoft YaHei UI", 8))
        help_text.setStyleSheet("color: #999999; background-color: #1E1E30; padding: 8px; border-radius: 6px;")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        layout.addStretch()

    # ── Tab: 视觉理解 (SiliconFlow) ──────────────────────────

    def _build_tab_siliconflow(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(16, 20, 16, 16)
        form = QFormLayout()
        form.setSpacing(16)
        form.setContentsMargins(0, 0, 0, 0)

        # API Key
        self._sf_key_edit = QLineEdit()
        self._sf_key_edit.setPlaceholderText("sk-xxxxxxxxxxxxxxxxxxxxxxxx")
        self._sf_key_edit.setEchoMode(QLineEdit.Password)
        self._sf_key_edit.setFont(QFont("Consolas", 10))
        self._apply_field_style(self._sf_key_edit)
        sf_key_layout = QHBoxLayout()
        sf_key_layout.addWidget(self._sf_key_edit)
        self._show_sf_btn = QPushButton("显示")
        self._show_sf_btn.setFixedSize(52, 32)
        self._show_sf_btn.setCheckable(True)
        self._show_sf_btn.setCursor(Qt.PointingHandCursor)
        self._show_sf_btn.setFont(QFont("Microsoft YaHei UI", 8))
        self._show_sf_btn.setStyleSheet("""
            QPushButton {
                background-color: #ECEEFF;
                color: #5060DD;
                border-radius: 6px;
                border: 1px solid #C8CCEE;
            }
            QPushButton:checked {
                background-color: #5060DD;
                color: white;
            }
            QPushButton:hover { background-color: #DDE0FF; }
        """)
        self._show_sf_btn.toggled.connect(self._toggle_sf_secret_visibility)
        sf_key_layout.addWidget(self._show_sf_btn)
        form.addRow("API Key:", sf_key_layout)

        # Base URL
        self._sf_url_edit = QLineEdit()
        self._sf_url_edit.setPlaceholderText("https://api.siliconflow.cn/v1")
        self._sf_url_edit.setFont(QFont("Consolas", 10))
        self._apply_field_style(self._sf_url_edit)
        form.addRow("Base URL:", self._sf_url_edit)

        # 模型名称
        self._sf_model_edit = QLineEdit()
        self._sf_model_edit.setPlaceholderText("Qwen/Qwen3-VL-30B-A3B-Instruct")
        self._sf_model_edit.setFont(QFont("Consolas", 10))
        self._apply_field_style(self._sf_model_edit)
        form.addRow("模型名称:", self._sf_model_edit)

        layout.addLayout(form)
        layout.addSpacing(8)

        # 帮助提示
        help_text = QLabel(
            "🔍 SiliconFlow 提供托管的视觉大模型 API。\n"
            "    推荐模型 Qwen/Qwen3-VL-30B-A3B-Instruct（性价比高、262K上下文）。\n"
            "    也可尝试 Qwen/Qwen2.5-VL-72B-Instruct 或 Qwen/Qwen3-Omni。\n"
            "    需在 siliconflow.cn 注册并申请 API Key。"
        )
        help_text.setFont(QFont("Microsoft YaHei UI", 8))
        help_text.setStyleSheet("color: #999999; background-color: #1E1E30; padding: 8px; border-radius: 6px;")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        layout.addStretch()

    # ── 创作生图选项卡（Agnes Image API） ────────────────────

    def _build_tab_image_gen(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        title = QLabel("🎨 创作生图")
        title.setFont(QFont("Microsoft YaHei UI", 11, QFont.Bold))
        title.setStyleSheet("color: #3A3A5C;")
        layout.addWidget(title)

        desc = QLabel(
            "在 Agnes Image API 与 SiliconFlow 的 Kwai-Kolors/Kolors 之间切换。\n"
            "SiliconFlow 生图复用“视觉理解”选项卡中的 API Key 和 Base URL。"
        )
        desc.setFont(QFont("Microsoft YaHei UI", 9))
        desc.setStyleSheet("color: #888888;")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        layout.addSpacing(8)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)

        # 启用开关
        self._ig_enabled_cb = QCheckBox("启用图片生成（关闭后 AI 将无法调用生图工具）")
        self._ig_enabled_cb.setFont(QFont("Microsoft YaHei UI", 9))
        self._ig_enabled_cb.setStyleSheet("color: #3A3A5C;")
        form.addRow("", self._ig_enabled_cb)

        self._ig_provider_combo = QComboBox()
        self._ig_provider_combo.addItems([
            "Agnes Image API",
            "SiliconFlow · Kwai-Kolors/Kolors",
        ])
        self._ig_provider_combo.setStyleSheet(self._ig_size_combo_style())
        form.addRow("生图提供商:", self._ig_provider_combo)

        # 模型名称
        self._ig_model_edit = QLineEdit()
        self._ig_model_edit.setPlaceholderText("agnes-image-2.1-flash")
        self._ig_model_edit.setFont(QFont("Consolas", 10))
        self._apply_field_style(self._ig_model_edit)
        self._ig_model_label = QLabel("图片模型:")
        form.addRow(self._ig_model_label, self._ig_model_edit)

        self._ig_sf_model_edit = QLineEdit()
        self._ig_sf_model_edit.setPlaceholderText("Kwai-Kolors/Kolors")
        self._ig_sf_model_edit.setFont(QFont("Consolas", 10))
        self._apply_field_style(self._ig_sf_model_edit)
        self._ig_sf_model_label = QLabel("Kolors 模型:")
        form.addRow(self._ig_sf_model_label, self._ig_sf_model_edit)

        self._ig_steps_spin = QSpinBox()
        self._ig_steps_spin.setRange(1, 100)
        self._ig_steps_spin.setValue(20)
        self._ig_steps_spin.setSuffix(" steps")
        self._ig_steps_spin.setStyleSheet(self._ig_size_combo_style())
        self._ig_steps_label = QLabel("采样步数:")
        form.addRow(self._ig_steps_label, self._ig_steps_spin)

        self._ig_guidance_spin = QDoubleSpinBox()
        self._ig_guidance_spin.setRange(0.0, 30.0)
        self._ig_guidance_spin.setSingleStep(0.5)
        self._ig_guidance_spin.setDecimals(1)
        self._ig_guidance_spin.setValue(7.5)
        self._ig_guidance_spin.setStyleSheet(self._ig_size_combo_style())
        self._ig_guidance_label = QLabel("引导系数:")
        form.addRow(self._ig_guidance_label, self._ig_guidance_spin)

        # 默认尺寸
        self._ig_size_combo = QComboBox()
        self._ig_size_combo.setFont(QFont("Microsoft YaHei UI", 9))
        self._ig_size_combo.addItems([
            "1024x1024 — 正方形",
            "1792x1024 — 宽屏横版",
            "1024x1792 — 竖版",
            "4k — 超高清",
        ])
        self._ig_size_combo.setStyleSheet("""
            QComboBox {
                border: 1px solid #D8D8EE;
                border-radius: 8px;
                padding: 4px 10px;
                background-color: #FFFFFF;
                color: #3A3A5C;
            }
        """)
        form.addRow("默认尺寸:", self._ig_size_combo)

        # 默认质量
        self._ig_quality_combo = QComboBox()
        self._ig_quality_combo.setFont(QFont("Microsoft YaHei UI", 9))
        self._ig_quality_combo.addItems([
            "standard — 标准质量（快速）",
            "hd — 高清质量（细节更丰富）",
        ])
        self._ig_quality_combo.setStyleSheet(self._ig_size_combo.styleSheet())
        form.addRow("默认质量:", self._ig_quality_combo)

        self._ig_provider_combo.currentIndexChanged.connect(self._on_image_provider_changed)

        layout.addLayout(form)

        # 测试按钮
        layout.addSpacing(8)
        test_row = QHBoxLayout()
        test_row.addStretch()
        self._ig_test_btn = QPushButton("🖼 测试生成一张图片")
        self._ig_test_btn.setFixedHeight(36)
        self._ig_test_btn.setFont(QFont("Microsoft YaHei UI", 9))
        self._ig_test_btn.setCursor(Qt.PointingHandCursor)
        self._ig_test_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF9500;
                color: white;
                border-radius: 8px;
                border: none;
                padding: 0 20px;
            }
            QPushButton:hover   { background-color: #E08600; }
            QPushButton:pressed { background-color: #C07600; }
            QPushButton:disabled{ background-color: #CCCCCC; }
        """)
        self._ig_test_btn.clicked.connect(self._on_image_gen_test)
        test_row.addWidget(self._ig_test_btn)
        test_row.addStretch()
        layout.addLayout(test_row)

        layout.addStretch()

    def _ig_size_combo_style(self):
        return """
            QComboBox, QSpinBox, QDoubleSpinBox {
                border: 1px solid #D8D8EE;
                border-radius: 8px;
                padding: 4px 10px;
                background-color: #FFFFFF;
                color: #3A3A5C;
            }
        """

    def _on_image_provider_changed(self, index: int):
        is_sf = index == 1
        self._ig_model_edit.setVisible(not is_sf)
        self._ig_model_label.setVisible(not is_sf)
        self._ig_sf_model_edit.setVisible(is_sf)
        self._ig_sf_model_label.setVisible(is_sf)
        self._ig_steps_spin.setVisible(is_sf)
        self._ig_steps_label.setVisible(is_sf)
        self._ig_guidance_spin.setVisible(is_sf)
        self._ig_guidance_label.setVisible(is_sf)
        self._ig_quality_combo.setVisible(not is_sf)
        # Kolors 接口不接受 Agnes 的 4k 尺寸，显示时仍保留原组合框但运行时会映射。

    # ── 创作视频选项卡（Agnes Video API） ──────────────────

    def _build_tab_video_gen(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        title = QLabel("🎬 创作视频 — Agnes Video API")
        title.setFont(QFont("Microsoft YaHei UI", 11, QFont.Bold))
        title.setStyleSheet("color: #3A3A5C;")
        layout.addWidget(title)

        desc = QLabel(
            "使用 Agnes Video API 根据文字或图片生成视频。\n"
            "与 Agnes 聊天模型共用同一 API Key。\n"
            "视频生成是异步任务，约需 1-5 分钟完成。"
        )
        desc.setFont(QFont("Microsoft YaHei UI", 9))
        desc.setStyleSheet("color: #888888;")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        layout.addSpacing(8)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)

        self._vg_enabled_cb = QCheckBox("启用视频生成（关闭后 AI 将无法调用生视频工具）")
        self._vg_enabled_cb.setFont(QFont("Microsoft YaHei UI", 9))
        self._vg_enabled_cb.setStyleSheet("color: #3A3A5C;")
        form.addRow("", self._vg_enabled_cb)

        self._vg_model_edit = QLineEdit()
        self._vg_model_edit.setPlaceholderText("agnes-video-v2.0")
        self._vg_model_edit.setFont(QFont("Consolas", 10))
        self._apply_field_style(self._vg_model_edit)
        form.addRow("视频模型:", self._vg_model_edit)

        self._vg_duration_combo = QComboBox()
        self._vg_duration_combo.setFont(QFont("Microsoft YaHei UI", 9))
        self._vg_duration_combo.addItems(["3 秒 — 短视频", "5 秒 — 常用", "10 秒 — 长视频", "18 秒 — 超长"])
        self._vg_duration_combo.setStyleSheet("""
            QComboBox {
                border: 1px solid #D8D8EE;
                border-radius: 8px;
                padding: 4px 10px;
                background-color: #FFFFFF;
                color: #3A3A5C;
            }
        """)
        self._vg_duration_combo.setCurrentIndex(1)
        form.addRow("默认时长:", self._vg_duration_combo)

        self._vg_fps_combo = QComboBox()
        self._vg_fps_combo.setFont(QFont("Microsoft YaHei UI", 9))
        self._vg_fps_combo.addItems(["16", "24 — 标准", "30", "60 — 流畅"])
        self._vg_fps_combo.setCurrentIndex(1)
        self._vg_fps_combo.setStyleSheet(self._vg_duration_combo.styleSheet())
        form.addRow("默认帧率:", self._vg_fps_combo)

        layout.addLayout(form)

        layout.addSpacing(8)
        test_row = QHBoxLayout()
        test_row.addStretch()
        self._vg_test_btn = QPushButton("🎬 测试生成一段视频")
        self._vg_test_btn.setFixedHeight(36)
        self._vg_test_btn.setFont(QFont("Microsoft YaHei UI", 9))
        self._vg_test_btn.setCursor(Qt.PointingHandCursor)
        self._vg_test_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF9500;
                color: white;
                border-radius: 8px;
                border: none;
                padding: 0 20px;
            }
            QPushButton:hover   { background-color: #E08600; }
            QPushButton:pressed { background-color: #C07600; }
            QPushButton:disabled{ background-color: #CCCCCC; }
        """)
        self._vg_test_btn.clicked.connect(self._on_video_gen_test)
        test_row.addWidget(self._vg_test_btn)
        test_row.addStretch()
        layout.addLayout(test_row)

        layout.addStretch()

    # ── 和风天气选项卡 ───────────────────────────────────────

    def _build_tab_qweather(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        title = QLabel("☁️ 和风天气 API 配置")
        title.setFont(QFont("Microsoft YaHei UI", 11, QFont.Bold))
        title.setStyleSheet("color: #3A3A5C;")
        layout.addWidget(title)

        desc = QLabel(
            "配置和风天气 API Key 后，莲心就能查询实时天气和预报，"
            "并在适当时机主动提醒你天气变化和出行建议～"
        )
        desc.setFont(QFont("Microsoft YaHei UI", 9))
        desc.setStyleSheet("color: #888888;")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        layout.addSpacing(8)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)

        # API Key（密码模式）
        key_row = QHBoxLayout()
        self._qw_key_edit = QLineEdit()
        self._qw_key_edit.setPlaceholderText("输入和风天气 API Key")
        self._qw_key_edit.setEchoMode(QLineEdit.Password)
        self._qw_key_edit.setFont(QFont("Consolas", 10))
        self._apply_field_style(self._qw_key_edit)
        key_row.addWidget(self._qw_key_edit)

        self._show_qw_btn = QPushButton("显示")
        self._show_qw_btn.setFixedSize(60, 34)
        self._show_qw_btn.setFont(QFont("Microsoft YaHei UI", 9))
        self._show_qw_btn.setCursor(Qt.PointingHandCursor)
        self._show_qw_btn.setStyleSheet("""
            QPushButton {
                background-color: #1E1E30;
                
                border-radius: 8px;
                border: 1px solid #D8D8EE;
            }
            QPushButton:hover { background-color: #E4E4F0; }
        """)
        self._show_qw_btn.setCheckable(True)
        self._show_qw_btn.clicked.connect(self._toggle_qw_key_visibility)
        key_row.addWidget(self._show_qw_btn)

        form.addRow("API Key:", key_row)
        # API 专属域名
        self._qw_host_edit = QLineEdit()
        self._qw_host_edit.setPlaceholderText("pp65npvqtt.re.qweatherapi.com")
        self._qw_host_edit.setFont(QFont("Consolas", 10))
        self._apply_field_style(self._qw_host_edit)
        form.addRow("API 主机:", self._qw_host_edit)

        # 开发者 ID
        self._qw_dev_id_edit = QLineEdit()
        self._qw_dev_id_edit.setPlaceholderText("Q158859C18（选填）")
        self._qw_dev_id_edit.setFont(QFont("Consolas", 10))
        self._apply_field_style(self._qw_dev_id_edit)
        form.addRow("开发者 ID:", self._qw_dev_id_edit)

        # 默认城市（用户未指定城市时使用）
        self._qw_city_edit = QLineEdit()
        self._qw_city_edit.setPlaceholderText("广州（选填，不填则询问或读取记忆）")
        self._qw_city_edit.setFont(QFont("Microsoft YaHei UI", 10))
        self._apply_field_style(self._qw_city_edit)
        form.addRow("默认城市:", self._qw_city_edit)

        # 主动天气提醒开关
        self._qw_auto_remind = QCheckBox("开启主动天气提醒")
        self._qw_auto_remind.setFont(QFont("Microsoft YaHei UI", 9))
        self._qw_auto_remind.setStyleSheet("color: #3A3A5C;")
        form.addRow("", self._qw_auto_remind)

        # 每日提醒时间
        self._qw_remind_time = QComboBox()
        self._qw_remind_time.setFont(QFont("Microsoft YaHei UI", 9))
        self._qw_remind_time.setFixedWidth(120)
        self._qw_remind_time.setStyleSheet("""
            QComboBox {
                border: 1px solid #D8D8EE;
                border-radius: 8px;
                padding: 4px 10px;
                background-color: #FFFFFF;
                color: #2C2C2C;
                 
            }
            QComboBox:focus { border: 1px solid #6C7BFF; }
        """)
        for h in range(6, 23):
            self._qw_remind_time.addItem(f"{h:02d}:00")
            self._qw_remind_time.addItem(f"{h:02d}:30")
        form.addRow("提醒时间:", self._qw_remind_time)

        layout.addLayout(form)
        layout.addSpacing(8)

        help_text = QLabel(
            "💡 和风天气（QWeather）免费版每日 1000 次调用，个人使用绰绰有余。\n"
            "    API 主机和 Key 可在 console.qweather.com 获取。\n"
            "    建议开启主动提醒，莲心会在每天早上提醒你今日天气和出行建议。"
        )
        help_text.setFont(QFont("Microsoft YaHei UI", 8))
        help_text.setStyleSheet("color: #999999; background-color: #1E1E30; padding: 8px; border-radius: 6px;")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        layout.addStretch()

        # ── Tavily Search 选项卡 ─────────────────────────────────

    def _build_tab_tavily(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        title = QLabel("🔍 Tavily Search AI 配置")
        title.setFont(QFont("Microsoft YaHei UI", 11, QFont.Bold))
        title.setStyleSheet("color: #FFFFFF;")
        layout.addWidget(title)

        desc = QLabel(
            "配置 Tavily Search API Key 后，莲心就能使用高质量 AI 搜索，"
            "获取实时新闻和公开网页内容，绕过后端网络限制。\n"
            "注册地址：https://tavily.com/，免费额度 1000次/月。"
        )
        desc.setFont(QFont("Microsoft YaHei UI", 9))
        desc.setStyleSheet("color: #888888;")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        layout.addSpacing(8)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)

        # API Key（密码模式）
        key_row = QHBoxLayout()
        self._tv_key_edit = QLineEdit()
        self._tv_key_edit.setPlaceholderText("tvly-xxxxxxxxxxxxxxxxxxxxxxxx")
        self._tv_key_edit.setEchoMode(QLineEdit.Password)
        self._tv_key_edit.setFont(QFont("Consolas", 10))
        self._apply_field_style(self._tv_key_edit)
        key_row.addWidget(self._tv_key_edit)

        self._show_tv_btn = QPushButton("显示")
        self._show_tv_btn.setFixedSize(60, 34)
        self._show_tv_btn.setFont(QFont("Microsoft YaHei UI", 9))
        self._show_tv_btn.setCursor(Qt.PointingHandCursor)
        self._show_tv_btn.setStyleSheet("""
            QPushButton {
                background-color: #1E1E30;
                
                border-radius: 8px;
                border: 1px solid #D8D8EE;
            }
            QPushButton:hover { background-color: #E4E4F0; }
        """)
        self._show_tv_btn.setCheckable(True)
        self._show_tv_btn.clicked.connect(self._toggle_tv_key_visibility)
        key_row.addWidget(self._show_tv_btn)

        form.addRow("API Key:", key_row)

        layout.addLayout(form)
        layout.addStretch()

        # 帮助提示
        help_text = QLabel(
            "💡 提示：MCP Tavily 请求从你本地发出，绕过后端被墙限制，搜索质量优于 DuckDuckGo。"
        )
        help_text.setFont(QFont("Microsoft YaHei UI", 8))
        help_text.setStyleSheet("color: #999999; background-color: #1E1E30; padding: 8px; border-radius: 6px;")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

    # ── Firecrawl 选项卡 ─────────────────────────────────
    def _build_tab_firecrawl(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        title = QLabel("🕷️ Firecrawl 网页爬虫配置")
        title.setFont(QFont("Microsoft YaHei UI", 11, QFont.Bold))
        title.setStyleSheet("color: #3A3A5C;")
        layout.addWidget(title)

        desc = QLabel(
            "配置 Firecrawl API Key 后，莲心就能抓取任意网页的纯净内容，"
            "输出干净的 Markdown 格式，供 AI 分析使用。\n"
            "配合 Tavily 搜索：搜索 → 发现网页 → 爬取完整内容。\n"
            "注册地址：https://firecrawl.org.cn/，免费额度 500页/月。"
        )
        desc.setFont(QFont("Microsoft YaHei UI", 9))
        desc.setStyleSheet("color: #888888;")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        layout.addSpacing(8)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)

        # API Key（密码模式）
        key_row = QHBoxLayout()
        self._fc_key_edit = QLineEdit()
        self._fc_key_edit.setPlaceholderText("fc-xxxxxxxxxxxxxxxxxxxxxxxx")
        self._fc_key_edit.setEchoMode(QLineEdit.Password)
        self._fc_key_edit.setFont(QFont("Consolas", 10))
        self._apply_field_style(self._fc_key_edit)
        key_row.addWidget(self._fc_key_edit)

        self._show_fc_btn = QPushButton("显示")
        self._show_fc_btn.setFixedSize(60, 34)
        self._show_fc_btn.setFont(QFont("Microsoft YaHei UI", 9))
        self._show_fc_btn.setCursor(Qt.PointingHandCursor)
        self._show_fc_btn.setStyleSheet("""
            QPushButton {
                background-color: #1E1E30;
                
                border-radius: 8px;
                border: 1px solid #D8D8EE;
            }
            QPushButton:hover { background-color: #E4E4F0; }
        """)
        self._show_fc_btn.setCheckable(True)
        self._show_fc_btn.clicked.connect(self._toggle_fc_key_visibility)
        key_row.addWidget(self._show_fc_btn)

        form.addRow("API Key:", key_row)

        layout.addLayout(form)
        layout.addStretch()

        # 帮助提示
        help_text = QLabel(
            "💡 提示：Firecrawl 将网页转为 LLM 友好的 Markdown，自动去除广告和噪音。"
        )
        help_text.setFont(QFont("Microsoft YaHei UI", 8))
        help_text.setStyleSheet("color: #999999; background-color: #1E1E30; padding: 8px; border-radius: 6px;")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

    def _toggle_fc_key_visibility(self, checked: bool):
        self._fc_key_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self._show_fc_btn.setText("隐藏" if checked else "显示")


    def _toggle_qw_key_visibility(self, checked: bool):
        self._qw_key_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self._show_qw_btn.setText("隐藏" if checked else "显示")
    
    def _toggle_tv_key_visibility(self, checked: bool):
        self._tv_key_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self._show_tv_btn.setText("隐藏" if checked else "显示")

    # ── 网络搜索重试与回退设置 ───────────────────────────────
    def _build_tab_search_fallback(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        desc = QLabel(
            "配置 MCP 搜索（Tavily/Firecrawl）失败后的重试和回退策略。\n"
            "- 重试：同一请求失败后自动重试几次，偶发网络问题可以自动恢复\n"
            "- 回退：重试全部失败后，是改用内建工具，还是基于已有信息直接回答\n"
            "- 额度检测：免费额度用完时自动切换，不用手动改配置"
        )
        desc.setWordWrap(True)
        desc.setFont(QFont("Microsoft YaHei UI", 9))
        desc.setStyleSheet("color: #666; background: #F5F5FF; padding: 8px; border-radius: 4px;")
        layout.addWidget(desc)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)

        # 最大重试次数
        self._search_max_retries_spin = QSpinBox()
        self._search_max_retries_spin.setRange(0, 5)
        self._search_max_retries_spin.setSuffix(" 次")
        form.addRow("MCP 最大重试次数：", self._search_max_retries_spin)

        # 重试失败策略
        from PyQt5.QtWidgets import QRadioButton, QButtonGroup
        strategy_widget = QWidget()
        strategy_vbox = QVBoxLayout(strategy_widget)
        strategy_vbox.setContentsMargins(0, 4, 0, 4)
        strategy_vbox.setSpacing(6)
        self._search_strategy_group = QButtonGroup(strategy_widget)
        self._search_strategy_builtin = QRadioButton("回退到内建搜索工具（web_search/fetch_webpage）")
        self._search_strategy_direct = QRadioButton("基于已有信息直接回答")
        self._search_strategy_group.addButton(self._search_strategy_builtin)
        self._search_strategy_group.addButton(self._search_strategy_direct)
        strategy_vbox.addWidget(self._search_strategy_builtin)
        strategy_vbox.addWidget(self._search_strategy_direct)
        form.addRow("重试失败后策略：", strategy_widget)

        # 额度不足自动回退
        self._search_auto_fallback_check = QCheckBox("启用")
        form.addRow("额度不足自动回退：", self._search_auto_fallback_check)

        layout.addLayout(form)

        # 从 config 加载初始值
        from config import get_search_fallback_config
        cfg = get_search_fallback_config()
        self._search_max_retries_spin.setValue(cfg.get("max_retries", 2))
        if cfg.get("fallback_strategy", "builtin") == "builtin":
            self._search_strategy_builtin.setChecked(True)
        else:
            self._search_strategy_direct.setChecked(True)
        self._search_auto_fallback_check.setChecked(cfg.get("auto_fallback_on_quota", True))

        layout.addStretch()

    # ── 辅助样式 ──────────────────────────────────────────────

    def _apply_field_style(self, widget: QLineEdit):
        widget.setFixedHeight(34)
        widget.setStyleSheet("""
            QLineEdit {
                border: 1px solid #D8D8EE;
                border-radius: 8px;
                padding: 4px 10px;
                background-color: #FFFFFF;
                color: #2C2C2C;
                 
            }
            QLineEdit:focus { border: 1px solid #6C7BFF; }
        """)

    def _toggle_key_visibility(self, checked: bool):
        self._key_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self._show_btn.setText("隐藏" if checked else "显示")

    def _toggle_sf_secret_visibility(self, checked: bool):
        self._sf_key_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self._show_sf_btn.setText("隐藏" if checked else "显示")

    # ── 数据加载 ─────────────────────────────────────────────

    def _load(self):
        # DeepSeek 配置
        ds_cfg = get_api_config()
        provider = ds_cfg.get("provider", "deepseek")
        self._key_edit.setText(ds_cfg.get("api_key", ""))
        self._url_edit.setText(ds_cfg.get("base_url", "https://api.deepseek.com"))
        self._model_edit.setText(ds_cfg.get("model", "deepseek-v4-flash"))
        self._tokens_spin.setValue(ds_cfg.get("max_tokens", 4096))
        api_format = ds_cfg.get("api_format", "openai")
        idx = self._api_format_combo.findText(api_format)
        if idx >= 0:
            self._api_format_combo.setCurrentIndex(idx)
        self._local_url_edit.setText(ds_cfg.get("local_base_url", "http://localhost:11434/v1"))
        self._local_model_edit.setText(
            ds_cfg.get("local_model_name", "qwen2.5:3b-instruct")
        )
        self._router_model_edit.setText(ds_cfg.get("router_model", "my-qwen"))

        # Agnes AI 配置
        from config import get_agnes_config
        agnes_cfg = get_agnes_config()
        self._agnes_key_edit.setText(agnes_cfg.get("api_key", ""))
        self._agnes_model_edit.setText(agnes_cfg.get("model", "agnes-2.0-flash"))

        # 根据 provider 恢复 RadioButton 选中状态
        if provider == "local":
            self._radio_local.setChecked(True)
        elif provider == "agnes":
            self._radio_agnes.setChecked(True)
        else:
            self._radio_deepseek.setChecked(True)

        # 手动触发 UI 更新（setChecked 不会触发 buttonClicked 信号）
        checked = self._provider_group.checkedButton()
        if checked:
            self._on_provider_changed(checked)

        # QQ 桥接配置
        qq_cfg = get_qq_bridge_config()
        self._qq_account.setText(qq_cfg.get("qq_account", ""))
        self._ws_url.setText(qq_cfg.get("ws_url", "ws://127.0.0.1:3001"))
        self._owner_qq.setText(qq_cfg.get("owner_qq", ""))
        owner_name = qq_cfg.get("owner_name", "")
        if owner_name:
            self._owner_name.setText(owner_name)
        self._auto_enable.setChecked(qq_cfg.get("enabled", False))

        # SiliconFlow 视觉 API 配置
        sf_cfg = get_siliconflow_config()
        self._sf_key_edit.setText(sf_cfg.get("api_key", ""))
        self._sf_url_edit.setText(sf_cfg.get("base_url", "https://api.siliconflow.cn/v1"))
        self._sf_model_edit.setText(sf_cfg.get("vision_model", "Qwen/Qwen3-VL-30B-A3B-Instruct"))

        # 和风天气配置
        qw_cfg = get_qweather_config()
        self._qw_key_edit.setText(qw_cfg.get("api_key", ""))
        self._qw_auto_remind.setChecked(qw_cfg.get("auto_remind", True))
        self._qw_host_edit.setText(qw_cfg.get("api_host", ""))
        self._qw_dev_id_edit.setText(qw_cfg.get("dev_id", ""))
        self._qw_city_edit.setText(qw_cfg.get("default_city", ""))

        # 图片生成配置
        from config import get_image_gen_config
        ig_cfg = get_image_gen_config()
        self._ig_enabled_cb.setChecked(ig_cfg.get("enabled", True))
        self._ig_model_edit.setText(ig_cfg.get("model", "agnes-image-2.1-flash"))
        self._ig_sf_model_edit.setText(ig_cfg.get("siliconflow_model", "Kwai-Kolors/Kolors"))
        self._ig_steps_spin.setValue(int(ig_cfg.get("num_inference_steps", 20)))
        self._ig_guidance_spin.setValue(float(ig_cfg.get("guidance_scale", 7.5)))
        self._ig_provider_combo.setCurrentIndex(1 if ig_cfg.get("provider", "agnes") == "siliconflow" else 0)
        self._ig_quality_combo.setCurrentIndex(0 if ig_cfg.get("default_quality") != "hd" else 1)
        size_map = {"1024x1024": 0, "1792x1024": 1, "1024x1792": 2, "4k": 3}
        self._ig_size_combo.setCurrentIndex(size_map.get(ig_cfg.get("default_size", "1024x1024"), 0))

        # 视频生成配置
        from config import get_video_gen_config
        vg_cfg = get_video_gen_config()
        self._vg_enabled_cb.setChecked(vg_cfg.get("enabled", True))
        self._vg_model_edit.setText(vg_cfg.get("model", "agnes-video-v2.0"))
        dur_map = {3: 0, 5: 1, 10: 2, 18: 3}
        self._vg_duration_combo.setCurrentIndex(dur_map.get(vg_cfg.get("default_duration", 5), 1))
        fps_map = {16: 0, 24: 1, 30: 2, 60: 3}
        self._vg_fps_combo.setCurrentIndex(fps_map.get(vg_cfg.get("default_frame_rate", 24), 1))

        self._qw_remind_time.setCurrentText(qw_cfg.get("remind_time", "07:00"))
        remind_time = qw_cfg.get("remind_time", "07:00")
        idx = self._qw_remind_time.findText(remind_time)
        if idx >= 0:
            self._qw_remind_time.setCurrentIndex(idx)


    # ── 数据收集 ─────────────────────────────────────────────

    def _collect_deepseek(self) -> dict:
        """收集 DeepSeek 配置（含 provider 选择）。"""
        if self._radio_local.isChecked():
            provider = "local"
        elif self._radio_agnes.isChecked():
            provider = "agnes"
        else:
            provider = "deepseek"
        return {
            "api_key":    self._key_edit.text().strip(),
            "base_url":   self._url_edit.text().strip() or "https://api.deepseek.com",
            "model":      self._model_edit.text().strip() or "deepseek-v4-flash",
            "max_tokens": self._tokens_spin.value(),
            "api_format": self._api_format_combo.currentText(),
            "provider":   provider,
            "use_local":  (provider == "local"),
            "local_base_url": self._local_url_edit.text().strip() or "http://localhost:11434/v1",
            "local_model_name": self._local_model_edit.text().strip() or "qwen2.5:3b-instruct",
            "router_model": self._router_model_edit.text().strip(),
        }

    def _collect_agnes(self) -> dict:
        """收集 Agnes AI 配置。"""
        return {
            "api_key":  self._agnes_key_edit.text().strip(),
            "base_url": "https://apihub.agnes-ai.com/v1",
            "model":    self._agnes_model_edit.text().strip() or "agnes-2.0-flash",
        }

    def _collect_image_gen(self) -> dict:
        """收集图片生成配置。"""
        size_map = ["1024x1024", "1792x1024", "1024x1792", "4k"]
        quality_map = ["standard", "hd"]
        return {
            "provider":         "siliconflow" if self._ig_provider_combo.currentIndex() == 1 else "agnes",
            "enabled":         self._ig_enabled_cb.isChecked(),
            "model":           self._ig_model_edit.text().strip() or "agnes-image-2.1-flash",
            "default_size":    size_map[self._ig_size_combo.currentIndex()],
            "default_quality": quality_map[self._ig_quality_combo.currentIndex()],
            "siliconflow_model": self._ig_sf_model_edit.text().strip() or "Kwai-Kolors/Kolors",
            "num_inference_steps": self._ig_steps_spin.value(),
            "guidance_scale": self._ig_guidance_spin.value(),
            "save_dir":        "",
        }

    def _collect_video_gen(self) -> dict:
        """收集视频生成配置。"""
        duration_map = [3, 5, 10, 18]
        fps_map = [16, 24, 30, 60]
        return {
            "enabled":            self._vg_enabled_cb.isChecked(),
            "model":              self._vg_model_edit.text().strip() or "agnes-video-v2.0",
            "default_duration":   duration_map[self._vg_duration_combo.currentIndex()],
            "default_frame_rate": fps_map[self._vg_fps_combo.currentIndex()],
            "default_width":      1152,
            "default_height":     768,
            "save_dir":           "",
        }

    def _collect_qq_bridge(self) -> dict:
        return {
            "enabled":    self._auto_enable.isChecked(),
            "ws_url":     self._ws_url.text().strip() or "ws://127.0.0.1:3001",
            "qq_account": self._qq_account.text().strip(),
            "owner_qq":   self._owner_qq.text().strip(),
            "owner_name": self._owner_name.text().strip() or "主人",
        }

    def _collect_siliconflow(self) -> dict:
        return {
            "api_key":      self._sf_key_edit.text().strip(),
            "base_url":     self._sf_url_edit.text().strip() or "https://api.siliconflow.cn/v1",
            "vision_model": self._sf_model_edit.text().strip() or "deepseek-ai/deepseek-vl2",
        }

    def _collect_qweather(self) -> dict:
        return {
            "api_key":     self._qw_key_edit.text().strip(),
            "api_host":    self._qw_host_edit.text().strip(),
            "dev_id":      self._qw_dev_id_edit.text().strip(),
            "default_city": self._qw_city_edit.text().strip(),
            "auto_remind": self._qw_auto_remind.isChecked(),
            "remind_time": self._qw_remind_time.currentText(),
        }

    # ── 保存 ─────────────────────────────────────────────────

    def _on_save(self):
        # DeepSeek（含 provider 选择）
        ds_cfg = self._collect_deepseek()
        provider = ds_cfg.get("provider", "deepseek")
        if provider == "deepseek" and not ds_cfg["api_key"]:
            QMessageBox.warning(self, "提示", "DeepSeek API Key 不能为空！")
            return
        if provider == "agnes" and not self._agnes_key_edit.text().strip():
            QMessageBox.warning(self, "提示", "Agnes AI API Key 不能为空！")
            return
        save_api_config(ds_cfg)

        # Agnes AI 配置（独立存储）
        agnes_cfg = self._collect_agnes()
        from config import save_agnes_config
        save_agnes_config(agnes_cfg)

        # QQ 桥接
        qq_cfg = self._collect_qq_bridge()
        save_qq_bridge_config(qq_cfg)

        # SiliconFlow 视觉 API
        sf_cfg = self._collect_siliconflow()
        save_siliconflow_config(sf_cfg)

        # 和风天气
        qw_cfg = self._collect_qweather()
        save_qweather_config(qw_cfg)

        # 图片生成
        ig_cfg = self._collect_image_gen()
        from config import save_image_gen_config
        save_image_gen_config(ig_cfg)

        # 视频生成
        vg_cfg = self._collect_video_gen()
        from config import save_video_gen_config
        save_video_gen_config(vg_cfg)

        self.config_saved.emit()
        self.accept()

    def _on_image_gen_test(self):
        """测试图片生成。"""
        from config import get_agnes_config, get_siliconflow_config
        is_sf = self._ig_provider_combo.currentIndex() == 1
        active_cfg = get_siliconflow_config() if is_sf else get_agnes_config()
        api_key = active_cfg.get("api_key", "").strip()
        if not api_key:
            location = "视觉理解" if is_sf else "DeepSeek API"
            provider_name = "SiliconFlow" if is_sf else "Agnes AI"
            QMessageBox.warning(self, "提示", f"请先在“{location}”选项卡中填写 {provider_name} API Key！")
            return

        size_map = ["1024x1024", "1792x1024", "1024x1792", "4k"]
        quality_map = ["standard", "hd"]
        size = size_map[self._ig_size_combo.currentIndex()]
        quality = quality_map[self._ig_quality_combo.currentIndex()]
        model = (
            self._ig_sf_model_edit.text().strip() or "Kwai-Kolors/Kolors"
            if is_sf else self._ig_model_edit.text().strip() or "agnes-image-2.1-flash"
        )
        sf_size_map = {"1792x1024": "1024x768", "1024x1792": "768x1024", "4k": "1024x1024"}
        request_body = (
            {"model": model, "prompt": "一只可爱的卡通小猫，坐在窗台上看月亮",
             "image_size": sf_size_map.get(size, size), "batch_size": 1,
             "num_inference_steps": self._ig_steps_spin.value(),
             "guidance_scale": self._ig_guidance_spin.value()}
            if is_sf else
            {"model": model, "prompt": "一只可爱的卡通小猫，坐在窗台上看月亮",
             "size": size, "quality": quality, "n": 1}
        )
        endpoint = (
            f"{active_cfg.get('base_url', 'https://api.siliconflow.cn/v1').rstrip('/')}/images/generations"
            if is_sf else "https://apihub.agnes-ai.com/v1/images/generations"
        )
        self._ig_test_btn.setEnabled(False)
        self._ig_test_btn.setText("生成中…")
        self._ig_test_worker = _ImageGenTestWorker(endpoint, api_key, request_body, is_sf, self)
        self._ig_test_worker.success.connect(self._on_image_gen_test_success)
        self._ig_test_worker.failed.connect(self._on_image_gen_test_failed)
        self._ig_test_worker.finished.connect(self._ig_test_worker.deleteLater)
        self._ig_test_worker.start()

    def _on_image_gen_test_success(self, save_path: str):
        self._ig_test_btn.setText("🖼 测试生成一张图片")
        self._ig_test_btn.setEnabled(True)
        QMessageBox.information(self, "生成成功", f"图片已保存到：\n{save_path}")

    def _on_image_gen_test_failed(self, message: str):
        self._ig_test_btn.setText("🖼 测试生成一张图片")
        self._ig_test_btn.setEnabled(True)
        QMessageBox.warning(self, "生成失败", message)

    def _on_video_gen_test(self):
        """测试视频生成（文生视频，异步轮询）。"""
        from config import get_agnes_config
        agnes_cfg = get_agnes_config()
        api_key = agnes_cfg.get("api_key", "").strip()
        if not api_key:
            QMessageBox.warning(self, "提示", "请先在 DeepSeek API 选项卡中选择 Agnes AI 并填写 API Key！")
            return

        self._vg_test_btn.setEnabled(False)
        self._vg_test_btn.setText("生成中…")

        def _test():
            import requests, time, os
            duration_map = [3, 5, 10, 18]
            fps_map = [16, 24, 30, 60]
            dur = duration_map[self._vg_duration_combo.currentIndex()]
            fps = fps_map[self._vg_fps_combo.currentIndex()]
            num_frames = dur * fps
            # 确保 num_frames ≤ 441 且满足 8n+1
            if num_frames > 441:
                num_frames = 441
            num_frames = ((num_frames - 1) // 8) * 8 + 1
            model = self._vg_model_edit.text().strip() or "agnes-video-v2.0"

            try:
                resp = requests.post(
                    "https://apihub.agnes-ai.com/v1/videos",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "prompt": "A cute cat playing with a ball of yarn in a sunny room, soft lighting",
                        "num_frames": num_frames,
                        "frame_rate": fps,
                    },
                    timeout=120,
                )
                if resp.status_code != 200:
                    self._vg_test_btn.setText("🎬 测试生成")
                    self._vg_test_btn.setEnabled(True)
                    QMessageBox.warning(self, "创建失败", f"HTTP {resp.status_code}: {resp.text[:200]}")
                    return

                data = resp.json()
                video_id = data.get("video_id", "")
                if not video_id:
                    self._vg_test_btn.setText("🎬 测试生成")
                    self._vg_test_btn.setEnabled(True)
                    QMessageBox.warning(self, "创建失败", "未获取到 video_id")
                    return

                for _ in range(60):
                    time.sleep(5)
                    q_resp = requests.get(
                        f"https://apihub.agnes-ai.com/agnesapi?video_id={video_id}",
                        headers={"Authorization": f"Bearer {api_key}"},
                        timeout=120,
                    )
                    if q_resp.status_code != 200:
                        continue
                    q_data = q_resp.json()
                    status = q_data.get("status", "")
                    if status == "completed":
                        video_url = q_data.get("remixed_from_video_id", "")
                        if not video_url:
                            self._vg_test_btn.setText("🎬 测试生成")
                            self._vg_test_btn.setEnabled(True)
                            QMessageBox.warning(self, "生成失败", "未获取到视频 URL")
                            return
                        save_dir = os.path.join(os.path.expanduser("~"), ".lianxin", "videos")
                        os.makedirs(save_dir, exist_ok=True)
                        save_path = os.path.join(save_dir, f"test_{int(time.time())}.mp4")
                        v_resp = requests.get(video_url, timeout=300)
                        with open(save_path, "wb") as f:
                            f.write(v_resp.content)
                        self._vg_test_btn.setText("🎬 测试生成")
                        self._vg_test_btn.setEnabled(True)
                        QMessageBox.information(self, "生成成功", f"视频已保存到：\n{save_path}")
                        return
                    elif status == "failed":
                        self._vg_test_btn.setText("🎬 测试生成")
                        self._vg_test_btn.setEnabled(True)
                        QMessageBox.warning(self, "生成失败", q_data.get("error", "未知错误"))
                        return

                self._vg_test_btn.setText("🎬 测试生成")
                self._vg_test_btn.setEnabled(True)
                QMessageBox.warning(self, "超时", "视频生成超时（5 分钟），请稍后重试")

            except Exception as e:
                self._vg_test_btn.setText("🎬 测试生成")
                self._vg_test_btn.setEnabled(True)
                QMessageBox.warning(self, "生成异常", str(e))

        import threading
        threading.Thread(target=_test, daemon=True).start()

    # ── 测试 DeepSeek 连接 ────────────────────────────────────

    def _on_test(self):
        """测试连接（DeepSeek 或 Ollama 本地）。"""
        if self._radio_local.isChecked():
            cfg = self._collect_deepseek()
            api_key = "ollama"
            base_url = normalize_local_base_url(
                cfg.get("local_base_url", "http://localhost:11434/v1")
            )
            model = normalize_local_model_name(
                cfg.get("local_model_name", "qwen2.5:3b-instruct")
            )
            is_local = True
            api_name = "本地 Ollama"
        elif self._radio_agnes.isChecked():
            # Agnes 有独立的测试按钮，不应该走到这里
            return
        else:
            cfg = self._collect_deepseek()
            if not cfg["api_key"]:
                QMessageBox.warning(self, "提示", "请先填写 DeepSeek API Key！")
                return
            api_key = cfg["api_key"]
            base_url = cfg["base_url"]
            model = cfg["model"]
            is_local = False
            api_name = "DeepSeek API"

        if self._test_worker and self._test_worker.isRunning():
            return

        self._test_btn.setEnabled(False)
        self._test_btn.setText("连接中…")

        self._test_worker = _TestWorker(api_key, base_url, model, is_local=is_local, parent=self)
        self._test_worker.success.connect(lambda reply: self._on_test_success(reply, api_name))
        self._test_worker.failed.connect(lambda err: self._on_test_failed(err, api_name, is_local))
        self._test_worker.start()

    def _on_agnes_test(self):
        """测试 Agnes AI 连接。"""
        agnes_cfg = self._collect_agnes()
        if not agnes_cfg["api_key"]:
            QMessageBox.warning(self, "提示", "请先填写 Agnes AI API Key！")
            return
        if self._test_worker and self._test_worker.isRunning():
            return

        self._agnes_test_btn.setEnabled(False)
        self._agnes_test_btn.setText("连接中…")

        self._test_worker = _TestWorker(
            agnes_cfg["api_key"], agnes_cfg["base_url"], agnes_cfg["model"],
            is_local=False, parent=self
        )
        self._test_worker.success.connect(lambda reply: self._on_agnes_test_success(reply))
        self._test_worker.failed.connect(lambda err: self._on_agnes_test_failed(err))
        self._test_worker.start()

    def _on_agnes_test_success(self, reply: str):
        self._agnes_test_btn.setEnabled(True)
        self._agnes_test_btn.setText("🔗 测试 Agnes 连接")
        QMessageBox.information(self, "连接成功", f"Agnes AI 连接正常！\n模型回复了：{reply}…")

    def _on_agnes_test_failed(self, err: str):
        self._agnes_test_btn.setEnabled(True)
        self._agnes_test_btn.setText("🔗 测试 Agnes 连接")
        QMessageBox.warning(
            self, "连接失败",
            f"无法连接到 Agnes AI。请检查 API Key 是否正确。\n\n错误信息：{err}"
        )

    def _on_test_success(self, reply: str, api_name: str = "API"):
        self._test_btn.setEnabled(True)
        if self._radio_local.isChecked():
            self._test_btn.setText("测试本地模型连接")
        else:
            self._test_btn.setText("测试 DeepSeek 连接")
        QMessageBox.information(self, "连接成功", f"{api_name} 连接正常！\n模型回复了：{reply}…")

    def _on_test_failed(self, err: str, api_name: str = "API", is_local: bool = False):
        self._test_btn.setEnabled(True)
        if is_local:
            self._test_btn.setText("测试本地模型连接")
        else:
            self._test_btn.setText("测试 DeepSeek 连接")
        hint = (
            "请确认 Ollama 已启动（命令行运行 ollama serve），\n"
            f"且模型已通过 ollama create 导入。"
        ) if is_local else "请检查 Key 和 URL 是否正确。"
        QMessageBox.warning(
            self, "连接失败",
            f"无法连接到 {api_name}。{hint}\n\n错误信息：{err}"
        )

    # ── 余额查询 ──────────────────────────────────────────

    def _on_balance_query(self):
        """仅支持 DeepSeek 余额查询。"""
        if not self._radio_deepseek.isChecked():
            QMessageBox.information(self, "提示", "余额查询仅支持 DeepSeek API。")
            return
        api_key = self._key_edit.text().strip()
        if not api_key:
            QMessageBox.warning(self, "提示", "请先在 DeepSeek API 选项卡中填写 API Key！")
            return
        self._balance_worker = _BalanceWorker(api_key, self)
        self._balance_worker.success.connect(self._on_balance_success)
        self._balance_worker.failed.connect(self._on_balance_failed)
        self._balance_worker.start()

    def _on_balance_success(self, info: dict):
        total = info["total_balance"]
        currency = info["currency"]
        if total < 1.0:
            message = f"⚠️ 余额预警：当前余额为 {total:.2f} {currency}，已不足 1 元，请尽快充值以免影响使用！"
        else:
            message = f"✅ 当前账户余额为：{total:.2f} {currency}"
        mb = QMessageBox(self)
        mb.setWindowTitle("💰 余额查询")
        mb.setText(message)
        if total < 1.0:
            import webbrowser
            recharge_btn = mb.addButton("去充值", QMessageBox.AcceptRole)
            mb.addButton(QMessageBox.Cancel)
            mb.exec_()
            if mb.clickedButton() == recharge_btn:
                webbrowser.open("https://platform.deepseek.com/usage")
        else:
            mb.exec_()

    def _on_balance_failed(self, err: str):
        QMessageBox.warning(self, "余额查询失败", f"无法获取余额信息：\n{err}")
