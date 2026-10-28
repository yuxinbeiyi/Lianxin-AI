"""Opt-in, bounded production TaskRuntime repair/acceptance probe in a temporary workspace."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain.runtime import session_store
from brain.runtime.goals import GoalContract
from brain.runtime.native_goal_backend import NativeGoalBackend
from brain.runtime.task_runtime import TaskRuntime


class RecordingBackend(NativeGoalBackend):
    def __init__(self):
        super().__init__(log=lambda *args: None)
        self.commands = []

    def _on_scoped_notification(self, message):
        params = message.get("params") or {}
        item = params.get("item") or {}
        if (message.get("method") == "item/completed" and item.get("type") == "commandExecution"
                and params.get("threadId") == self._goal_thread):
            self.commands.append({key: copy.deepcopy(item.get(key)) for key in
                                  ("id", "command", "cwd", "status", "exitCode", "aggregatedOutput")})
        super()._on_scoped_notification(message)


def run_probe():
    report = {"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "scope": "real TaskRuntime + native Codex + local test/repair; not main App/Tauri acceptance"}
    backend = RecordingBackend()
    with tempfile.TemporaryDirectory(prefix="lianxin-3d-runtime-live-", ignore_cleanup_errors=True) as cwd:
        workspace = Path(cwd)
        module = workspace / "calculator.py"
        tests = workspace / "test_calculator.py"
        module.write_text("def add(left, right):\n    return left + right + 1\n", encoding="utf-8")
        tests.write_text("import unittest\nfrom calculator import add\n\n"
                         "class CalculatorTests(unittest.TestCase):\n"
                         "    def test_add(self):\n        self.assertEqual(5, add(2, 3))\n"
                         "    def test_negative(self):\n        self.assertEqual(-3, add(-1, -2))\n", encoding="utf-8")
        test_hash = hashlib.sha256(tests.read_bytes()).hexdigest()
        baseline = subprocess.run([sys.executable, "-B", "-m", "unittest", "-q"], cwd=cwd,
                                  capture_output=True, text=True, timeout=15)
        report["baselineExitCode"] = baseline.returncode
        command = "& '%s' -B -m unittest -q" % sys.executable
        contract = GoalContract.from_dict({
            "objective": "Fix calculator.add in this disposable workspace, then verify the unchanged test suite.",
            "acceptanceCriteria": ["Both original calculator unit tests pass without modifying their source."],
            "maxTurns": 4, "maxTokens": 180000, "maxElapsedSeconds": 120,
            "verificationChecks": [{"criterionIndex": 0, "command": command}],
        })
        report["contract"] = contract.to_dict()
        cfg = {"enabled": True, "workspace": cwd, "max_rounds": 30, "timeout_seconds": 130}
        original_model_config = TaskRuntime._model_config

        def model_config(runtime, sandbox="", network=False):
            return {**original_model_config(runtime, sandbox, network), "web_search": "disabled",
                    "features.multi_agent": False, "model_reasoning_effort": "low"}

        with patch.object(TaskRuntime, "_check_orphan_pid"), \
                patch.object(TaskRuntime, "_runtime_config", return_value=cfg), \
                patch.object(TaskRuntime, "_model_config", model_config), \
                patch.object(session_store, "_SESSION_DIR", workspace / "runtime-sessions"), \
                patch("brain.runtime.task_runtime._PID_DIR", workspace), \
                patch("brain.runtime.task_runtime._PID_FILE", workspace / "runtime.pid"):
            runtime = TaskRuntime(goal_backend=backend, log=lambda *args: None)
            try:
                response = runtime.start(
                    "Read calculator.py and test_calculator.py. First run the exact verification command to see "
                    "the failure, repair calculator.py using apply_patch, then rerun the same verification command. "
                    "Each verification must be a standalone shell tool call containing ONLY the exact command: "
                    "do not prepend reads, append git commands, or combine commands with semicolons. "
                    "Do not alter tests or touch any path outside this workspace. Do not use network or subagents. "
                    "When the tests pass, use the native goal tool to mark complete and finish normally.",
                    session_id=1, sandbox="workspace-write", approval_mode="auto_accept",
                    goal_contract=contract, wait=True, wait_timeout=140)
                report["accepted"] = response.get("ok")
                report["state"] = response.get("state")
                report["version"] = backend._version
                report["nativeCommands"] = copy.deepcopy(backend.commands)
                result = (response.get("state") or {}).get("lastResult") or {}
                report["checkpoint"] = session_store.read_meta(backend._goal_thread)
                report["testsUnchanged"] = hashlib.sha256(tests.read_bytes()).hexdigest() == test_hash
                checked = subprocess.run([sys.executable, "-B", "-m", "unittest", "-q"], cwd=cwd,
                                         capture_output=True, text=True, timeout=15)
                report["independentExitCode"] = checked.returncode
                report["independentOutput"] = (checked.stdout + checked.stderr)[-1500:]
                matching = [item for item in backend.commands
                            if backend._verification_command(str(item.get("command") or "")) == command]
                report["nativeFailureObserved"] = any(type(item.get("exitCode")) is int and item["exitCode"] != 0
                                                       for item in matching)
                report["nativePassObserved"] = any(type(item.get("exitCode")) is int and item["exitCode"] == 0
                                                    for item in matching)
                report["businessPassed"] = all((result.get("status") == "completed",
                    (result.get("goalProgress") or {}).get("verificationPassed"),
                    report["testsUnchanged"], report["independentExitCode"] == 0,
                    report["baselineExitCode"] != 0, report["nativeFailureObserved"], report["nativePassObserved"]))
            except Exception as exc:
                report["error"] = {"type": type(exc).__name__, "message": str(exc)[:500]}
            finally:
                runtime.shutdown()
                if runtime._thread is not None:
                    runtime._thread.join(timeout=20)
                report["workerStopped"] = runtime._thread is None or not runtime._thread.is_alive()
                report["threadDeleted"] = bool(backend._goal_thread) and backend.delete_session(backend._goal_thread)
                backend.shutdown()
    report["workspaceRemoved"] = not Path(cwd).exists()
    report["passed"] = all((report.get("businessPassed"), report.get("workerStopped"),
                             report.get("threadDeleted"), report["workspaceRemoved"], "error" not in report))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Permit one bounded, billable model goal")
    parser.add_argument("--output", default="docs/codex_stage3d_runtime_20261028.json")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required; no model call was started")
    report = run_probe()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report.get(key) for key in
                      ("passed", "businessPassed", "version", "nativeFailureObserved", "nativePassObserved",
                       "testsUnchanged", "independentExitCode", "workerStopped", "threadDeleted", "workspaceRemoved", "error")},
                     ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
