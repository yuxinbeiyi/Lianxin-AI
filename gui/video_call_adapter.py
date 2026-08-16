"""Presentation-only bridge between the stable voice system and call UI."""

from PyQt5.QtCore import QObject

from gui.video_call_window import VideoCallWindow


class VideoCallPresentationAdapter(QObject):
    """Maps existing voice events to UI without importing voice internals."""

    def __init__(self, host):
        super().__init__(host)
        self._host = host
        self._window = None

    @property
    def is_open(self) -> bool:
        return self._window is not None and self._window.isVisible()

    def open(self):
        if self.is_open:
            self._window.raise_()
            self._window.activateWindow()
            return
        self._window = VideoCallWindow(self._host, preview_mode=False)
        self._window.hangup_requested.connect(self._host._exit_standby)
        self._window.microphone_toggled.connect(self._host._on_video_call_mic_toggled)
        self._window.speaker_toggled.connect(self._host._on_video_call_speaker_toggled)
        self._window.chat_requested.connect(self._show_chat)
        self._window.closed.connect(self._clear_window)
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()
        self._window.set_stt_loading(True)

    def close(self):
        if self._window is not None:
            self._window.close_from_host()
        self._window = None

    def set_duplex_state(self, state: str):
        if self.is_open:
            self._window.set_state(state)

    def set_stt_loading(self, active: bool):
        if self.is_open:
            self._window.set_stt_loading(active)

    def set_user_speaking(self, active: bool):
        if not self.is_open:
            return
        self._window.set_user_speaking(active)
        if active:
            self._window.set_state("USER_SPEAKING")

    def set_user_transcript(self, text: str):
        if self.is_open:
            self._window.set_user_speaking(False)
            self._window.set_subtitle(text, "你")

    def set_tts_started(self, text: str):
        if self.is_open:
            self._window.set_state("SPEAKING")
            self._window.set_subtitle(text, "莲心")

    def set_tts_finished(self):
        if self.is_open and self._host._standby_state == "STANDBY":
            self._window.set_state("LISTENING")

    def set_interrupted(self):
        if self.is_open:
            self._window.set_user_speaking(True)
            self._window.set_state("USER_SPEAKING")

    def _show_chat(self):
        self._host.showNormal()
        self._host.raise_()
        self._host.activateWindow()

    def _clear_window(self):
        self._window = None
