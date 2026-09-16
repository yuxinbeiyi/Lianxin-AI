"""
AliyunTab：阿里云实时语音识别配置选项卡
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QLineEdit, QFrame,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont
import webbrowser


class _TestAliyunThread(QThread):
    """后台线程：测试阿里云连接"""
    finished = pyqtSignal(bool, str)
    
    def __init__(self, ak_id: str, ak_secret: str, app_key: str, parent=None):
        super().__init__(parent)
        self._ak_id = ak_id
        self._ak_secret = ak_secret
        self._app_key = app_key
    
    def run(self):
        try:
            import time
            start = time.time()
            
            # 测试获取 Token
            try:
                from nls.token import getToken
                token = getToken(self._ak_id, self._ak_secret)
                
                if token:
                    elapsed = time.time() - start
                    self.finished.emit(True, f"Token 获取成功 ({elapsed:.2f}s)")
                else:
                    self.finished.emit(False, "Token 返回为空")
            except ImportError:
                self.finished.emit(
                    False,
                    "阿里云 SDK 未安装，请点击「安装 SDK」按钮"
                )
        except Exception as e:
            self.finished.emit(False, str(e))


class _InstallSDKThread(QThread):
    """后台线程：安装阿里云 SDK"""
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)
    
    def run(self):
        try:
            import subprocess, sys, os
            
            sdk_path = os.path.join(os.path.dirname(__file__), "..", "..", 
                                   "alibabacloud-nls-python-sdk")
            
            if os.path.exists(sdk_path):
                self.progress.emit("正在安装本地 SDK...")
                result = subprocess.run(
                    [sys.executable, "setup.py", "install"],
                    cwd=sdk_path,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=120
                )
                
                if result.returncode == 0:
                    self.finished.emit(True, "SDK 安装成功")
                else:
                    error = result.stderr or result.stdout or "未知错误"
                    self.finished.emit(False, f"安装失败: {error[:200]}")
            else:
                self.finished.emit(False, "SDK 目录不存在")
        except Exception as e:
            self.finished.emit(False, str(e))


class AliyunTab(QWidget):
    """阿里云 STT 配置选项卡"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._config = {}
        self._test_thread = None
        self._install_thread = None
        self._build_ui()
    
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        
        # ── 标题和徽章 ──
        header = QHBoxLayout()
        
        title = QLabel("🔒 阿里云 实时语音识别")
        title.setFont(QFont("Microsoft YaHei UI", 11, QFont.Bold))
        title.setStyleSheet("color: #9B59B6;")
        header.addWidget(title)
        
        badge = QLabel("企业级 · 流式")
        badge.setFont(QFont("Microsoft YaHei UI", 8))
        badge.setStyleSheet("""
            background-color: #9B59B6;
            color: white;
            padding: 2px 8px;
            border-radius: 10px;
        """)
        header.addWidget(badge)
        header.addStretch()
        layout.addLayout(header)
        
        # ── 描述 ──
        desc = QLabel(
            "<b>阿里云智能语音交互</b> NLS SDK<br><br>"
            "✅ <b>优势：</b><br>"
            "• 流式识别，实时返回结果<br>"
            "• 企业级稳定性保障<br>"
            "• 支持多种语言和方言<br>"
            "• 适合长时间录音场景<br><br>"
            "💰 <b>费用：</b><br>"
            "• 按量付费（约 ¥0.004/秒）<br>"
            "• 新用户有免费试用额度"
        )
        desc.setWordWrap(True)
        desc.setFont(QFont("Microsoft YaHei UI", 9))
        desc.setStyleSheet("color: #B0B0C0; padding: 8px; line-height: 1.5;")
        desc.setTextFormat(Qt.RichText)
        layout.addWidget(desc)
        
        # ── 启用开关 ──
        enable_row = QHBoxLayout()
        self._enable_cb = QCheckBox("启用此引擎")
        self._enable_cb.setChecked(False)
        self._enable_cb.setFont(QFont("Microsoft YaHei UI", 9))
        self._enable_cb.setStyleSheet("color: #E0E0E0;")
        enable_row.addWidget(self._enable_cb)
        enable_row.addStretch()
        layout.addLayout(enable_row)
        
        # ── 配置表单 ──
        form_frame = QFrame()
        form_frame.setStyleSheet("""
            QFrame {
                background-color: #252538;
                border-radius: 6px;
                padding: 4px;
            }
            QLabel { color: #E0E0E0; background: transparent; }
            QLineEdit { 
                color: #E0E0E0; 
                background: #1E1E30; 
                border: 1px solid #3D3D5A;
                border-radius: 4px;
                padding: 6px 8px;
            }
            QLineEdit:focus { border: 1px solid #6C7BFF; }
        """)
        form_layout = QVBoxLayout(form_frame)
        form_layout.setSpacing(10)
        form_layout.setContentsMargins(12, 12, 12, 12)
        
        # AccessKey ID
        akid_row = QHBoxLayout()
        akid_label = QLabel("AccessKey ID:")
        akid_label.setFont(QFont("Microsoft YaHei UI", 9))
        akid_label.setFixedWidth(110)
        akid_row.addWidget(akid_label)
        
        self._akid_edit = QLineEdit()
        self._akid_edit.setPlaceholderText("LTAI...")
        akid_row.addWidget(self._akid_edit)
        form_layout.addLayout(akid_row)
        
        # AccessKey Secret
        aks_row = QHBoxLayout()
        aks_label = QLabel("AccessKey Secret:")
        aks_label.setFont(QFont("Microsoft YaHei UI", 9))
        aks_label.setFixedWidth(110)
        aks_row.addWidget(aks_label)
        
        self._aks_edit = QLineEdit()
        self._aks_edit.setPlaceholderText("请妥善保管")
        self._aks_edit.setEchoMode(QLineEdit.Password)
        aks_row.addWidget(self._aks_edit)
        
        show_btn = QPushButton("显示")
        show_btn.setFixedSize(50, 28)
        show_btn.setCheckable(True)
        show_btn.setCursor(Qt.PointingHandCursor)
        show_btn.setFont(QFont("Microsoft YaHei UI", 8))
        show_btn.setStyleSheet("""
            QPushButton {
                background-color: #ECEEFF;
                color: #5060DD;
                border-radius: 4px;
                border: 1px solid #C8CCEE;
            }
            QPushButton:checked {
                background-color: #5060DD;
                color: white;
            }
            QPushButton:hover { background-color: #DDE0FF; }
        """)
        show_btn.toggled.connect(
            lambda checked: self._aks_edit.setEchoMode(
                QLineEdit.Normal if checked else QLineEdit.Password
            )
        )
        aks_row.addWidget(show_btn)
        form_layout.addLayout(aks_row)
        
        # AppKey
        appkey_row = QHBoxLayout()
        appkey_label = QLabel("AppKey:")
        appkey_label.setFont(QFont("Microsoft YaHei UI", 9))
        appkey_label.setFixedWidth(110)
        appkey_row.addWidget(appkey_label)
        
        self._appkey_edit = QLineEdit()
        self._appkey_edit.setPlaceholderText("ZCaEYdFPhr9kuR3V")
        appkey_row.addWidget(self._appkey_edit)
        form_layout.addLayout(appkey_row)
        
        layout.addWidget(form_frame)
        
        # ── 状态显示 ──
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setFont(QFont("Microsoft YaHei UI", 9))
        self._status_label.setStyleSheet("color: #888; padding: 8px;")
        layout.addWidget(self._status_label)
        
        # ── 操作按钮 ──
        btn_row1 = QHBoxLayout()
        btn_row1.setSpacing(8)
        
        get_ak_btn = QPushButton("🔑 获取 AccessKey")
        get_ak_btn.setFixedHeight(30)
        get_ak_btn.setFont(QFont("Microsoft YaHei UI", 9))
        get_ak_btn.setCursor(Qt.PointingHandCursor)
        get_ak_btn.setStyleSheet("""
            QPushButton {
                background-color: #2D2D3F;
                color: #E0E0E0;
                border: 1px solid #3D3D5A;
                border-radius: 6px;
                padding: 0 12px;
            }
            QPushButton:hover { background-color: #3D3D55; }
        """)
        get_ak_btn.clicked.connect(
            lambda: webbrowser.open("https://ram.console.aliyun.com/manage/ak")
        )
        btn_row1.addWidget(get_ak_btn)
        
        create_project_btn = QPushButton("📋 创建语音项目")
        create_project_btn.setFixedHeight(30)
        create_project_btn.setFont(QFont("Microsoft YaHei UI", 9))
        create_project_btn.setCursor(Qt.PointingHandCursor)
        create_project_btn.setStyleSheet("""
            QPushButton {
                background-color: #2D2D3F;
                color: #E0E0E0;
                border: 1px solid #3D3D5A;
                border-radius: 6px;
                padding: 0 12px;
            }
            QPushButton:hover { background-color: #3D3D55; }
        """)
        create_project_btn.clicked.connect(
            lambda: webbrowser.open("https://nls-portal.console.aliyun.com/")
        )
        btn_row1.addWidget(create_project_btn)
        
        layout.addLayout(btn_row1)
        
        btn_row2 = QHBoxLayout()
        btn_row2.setSpacing(8)
        
        install_sdk_btn = QPushButton("📦 安装 SDK")
        install_sdk_btn.setFixedHeight(30)
        install_sdk_btn.setFont(QFont("Microsoft YaHei UI", 9))
        install_sdk_btn.setCursor(Qt.PointingHandCursor)
        install_sdk_btn.setStyleSheet("""
            QPushButton {
                background-color: #2D2D3F;
                color: #E0E0E0;
                border: 1px solid #3D3D5A;
                border-radius: 6px;
                padding: 0 12px;
            }
            QPushButton:hover { background-color: #3D3D55; }
        """)
        install_sdk_btn.clicked.connect(self._on_install_sdk)
        btn_row2.addWidget(install_sdk_btn)
        
        test_btn = QPushButton("🔗 测试连接")
        test_btn.setFixedHeight(30)
        test_btn.setFont(QFont("Microsoft YaHei UI", 9))
        test_btn.setCursor(Qt.PointingHandCursor)
        test_btn.setStyleSheet("""
            QPushButton {
                background-color: #6C7BFF;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 0 12px;
            }
            QPushButton:hover { background-color: #5A6AEE; }
        """)
        test_btn.clicked.connect(self._on_test)
        btn_row2.addWidget(test_btn)
        
        layout.addLayout(btn_row2)
        
        # ── 使用指南 ──
        guide = QLabel(
            "💡 <b>配置步骤（共5步）：</b><br><br>"
            "<b>第1步：获取 AccessKey</b><br>"
            "• 访问 RAM 控制台创建 AccessKey<br><br>"
            "<b>第2步：创建语音项目</b><br>"
            "• 在 NLS 控制台创建项目并获取 AppKey<br><br>"
            "<b>第3步：安装 Python SDK</b><br>"
            "• 已内置在 alibabacloud-nls-python-sdk/ 目录<br>"
            "• 点击「安装 SDK」按钮即可<br><br>"
            "<b>第4步：填写配置</b><br>"
            "• 将上述3个值填入左侧输入框<br><br>"
            "<b>第5步：测试验证</b><br>"
            "• 点击「测试连接」确认配置正确<br><br>"
            "⚠️ <b>安全提醒：</b>AccessKey Secret 相当于密码，请妥善保管！"
        )
        guide.setOpenExternalLinks(True)
        guide.setWordWrap(True)
        guide.setFont(QFont("Microsoft YaHei UI", 8))
        guide.setStyleSheet("color: #888; padding: 8px; line-height: 1.4;")
        guide.setTextFormat(Qt.RichText)
        layout.addWidget(guide)
        
        layout.addStretch()
    
    def load_config(self, config: dict):
        """加载配置"""
        self._config = config.copy()
        self._enable_cb.setChecked(config.get("enabled", False))
        self._akid_edit.setText(config.get("access_key_id", ""))
        self._aks_edit.setText(config.get("access_key_secret", ""))
        self._appkey_edit.setText(config.get("app_key", ""))
    
    def collect_config(self) -> dict:
        """收集配置"""
        return {
            "enabled": self._enable_cb.isChecked(),
            "access_key_id": self._akid_edit.text().strip(),
            "access_key_secret": self._aks_edit.text().strip(),
            "app_key": self._appkey_edit.text().strip(),
        }
    
    def _on_test(self):
        """测试连接"""
        ak_id = self._akid_edit.text().strip()
        ak_secret = self._aks_edit.text().strip()
        app_key = self._appkey_edit.text().strip()
        
        if not all([ak_id, ak_secret, app_key]):
            self._status_label.setText("⚠️ 请填写完整的配置信息")
            self._status_label.setStyleSheet("color: #FFA500; padding: 8px;")
            return
        
        self._status_label.setText("正在测试连接...")
        self._status_label.setStyleSheet("color: #FFA500; padding: 8px;")
        
        self._test_thread = _TestAliyunThread(ak_id, ak_secret, app_key, self)
        self._test_thread.finished.connect(self._on_test_finished)
        self._test_thread.start()
    
    def _on_test_finished(self, success: bool, msg: str):
        """测试完成回调"""
        if success:
            self._status_label.setText(f"✅ {msg}")
            self._status_label.setStyleSheet("color: #27AE60; padding: 8px;")
        else:
            self._status_label.setText(f"❌ 测试失败: {msg}")
            self._status_label.setStyleSheet("color: #E74C3C; padding: 8px;")
    
    def _on_install_sdk(self):
        """安装 SDK"""
        self._status_label.setText("正在安装 SDK，请稍候...")
        self._status_label.setStyleSheet("color: #FFA500; padding: 8px;")
        
        self._install_thread = _InstallSDKThread(self)
        self._install_thread.progress.connect(
            lambda msg: self._status_label.setText(f"⏳ {msg}")
        )
        self._install_thread.finished.connect(self._on_install_finished)
        self._install_thread.start()
    
    def _on_install_finished(self, success: bool, msg: str):
        """安装完成回调"""
        if success:
            self._status_label.setText(f"✅ {msg}")
            self._status_label.setStyleSheet("color: #27AE60; padding: 8px;")
        else:
            self._status_label.setText(f"❌ {msg}")
            self._status_label.setStyleSheet("color: #E74C3C; padding: 8px;")