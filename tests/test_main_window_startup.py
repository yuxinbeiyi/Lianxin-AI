import ast
import unittest
from pathlib import Path
from types import SimpleNamespace


SOURCE_PATH = Path(__file__).resolve().parents[1] / "gui" / "main_window.py"


class MainWindowStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE_PATH.read_text(encoding="utf-8")
        module = ast.parse(cls.source)
        cls.main_window = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
        )

    def _method(self, name):
        return next(
            node for node in self.main_window.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        )

    def test_initial_overdue_check_is_deferred_until_avatar_router_exists(self):
        init_source = ast.get_source_segment(self.source, self._method("__init__"))
        router_assignment = init_source.index("self._avatar_actions = AvatarActionRouter(")
        deferred_check = init_source.index(
            "QTimer.singleShot(0, self._check_overdue_todos)"
        )

        self.assertGreater(deferred_check, router_assignment)
        self.assertNotIn("self._check_overdue_todos()", init_source[:router_assignment])

    def test_speak_guards_avatar_router_during_early_startup(self):
        speak_source = ast.get_source_segment(self.source, self._method("_speak"))

        self.assertIn(
            'avatar_actions = getattr(self, "_avatar_actions", None)',
            speak_source,
        )
        self.assertNotIn("self._avatar_actions.speaking_", speak_source)

    def test_speak_starts_without_avatar_router_during_early_startup(self):
        class Signal:
            def __init__(self):
                self.callbacks = []

            def connect(self, callback):
                self.callbacks.append(callback)

        class SpeakerWorker:
            def __init__(self, *_args):
                self.speaking_started = Signal()
                self.speaking_finished = Signal()
                self.started = False

            def start(self):
                self.started = True

        speak_node = self._method("_speak")
        standalone = ast.Module(
            body=[ast.FunctionDef(
                name="speak",
                args=speak_node.args,
                body=speak_node.body,
                decorator_list=[],
                returns=speak_node.returns,
                type_comment=speak_node.type_comment,
            )],
            type_ignores=[],
        )
        ast.fix_missing_locations(standalone)
        namespace = {
            "SpeakerWorker": SpeakerWorker,
            "QTimer": SimpleNamespace(singleShot=lambda *_args: None),
        }
        exec(compile(standalone, str(SOURCE_PATH), "exec"), namespace)

        window = SimpleNamespace(
            _global_settings=SimpleNamespace(silent_mode=False),
            _speaker=object(),
            _voice_duplex=None,
            _input_panel=SimpleNamespace(set_mute_visible=lambda _visible: None),
            _on_galgame_speaking_start=lambda: None,
            _on_galgame_speaking_stop=lambda: None,
            _play_speak_cue=lambda: None,
        )

        namespace["speak"](window, "逾期待办提醒")

        self.assertTrue(window._speaker_worker.started)

    def test_webengine_windows_are_imported_on_demand(self):
        self.assertNotIn(
            "from gui.time_capsule.web_window import TimeCapsuleWindow",
            self.source.split("class MainWindow", 1)[0],
        )
        self.assertNotIn(
            "from gui.study_room import StudyRoomWebWindow",
            self.source.split("class MainWindow", 1)[0],
        )
        diary_source = ast.get_source_segment(self.source, self._method("_open_diary_dialog"))
        study_source = ast.get_source_segment(self.source, self._method("_on_study_room_clicked"))
        self.assertIn("from gui.time_capsule.web_window import TimeCapsuleWindow", diary_source)
        self.assertIn("from gui.study_room import StudyRoomWebWindow", study_source)

    def test_auxiliary_message_route_uses_its_own_request_context(self):
        method_source = ast.get_source_segment(
            self.source, self._method("_send_user_text_to_agent")
        )

        self.assertIn(
            "from brain.request_context import parse_request_context", method_source
        )
        self.assertIn("routing_text = request_context.routing_text", method_source)
        self.assertIn("args=(routing_text, text)", method_source)
        self.assertNotIn("route(active_text)", method_source)

    def test_auxiliary_message_route_reports_background_failures_to_ui(self):
        method_source = ast.get_source_segment(
            self.source, self._method("_send_user_text_to_agent")
        )
        failure_source = ast.get_source_segment(
            self.source, self._method("_on_route_failed")
        )

        self.assertIn("self._route_failed.emit(str(exc))", method_source)
        self.assertIn("self._chat_widget.add_system_tip", failure_source)
        self.assertIn("self._set_idle_state()", failure_source)


if __name__ == "__main__":
    unittest.main()
