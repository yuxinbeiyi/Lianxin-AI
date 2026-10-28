"""Real, bounded native adapter verification in a disposable read-only workspace."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain.runtime.backend import SessionHandle, TaskSpec
from brain.runtime.goals import GoalContract
from brain.runtime.native_goal_backend import NativeGoalBackend


def wait_until(predicate, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def scope(backend):
    with backend._scope_lock:
        return SessionHandle(backend._scope_thread, backend._scope_turn, generation=backend._generation)


def run_probe(pause_resume_only=False):
    report = {"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "scope": "real native adapter, no production API/UI or business delivery verification"}
    config = {"features.shell_tool": False, "features.multi_agent": False,
              "web_search": "disabled", "model_reasoning_effort": "low"}
    backends = []
    thread_ids = set()
    with tempfile.TemporaryDirectory(prefix="lianxin-3d-native-", ignore_cleanup_errors=True) as cwd:
        def checkpoint(snapshot):
            target = Path(cwd) / "progress.json"
            target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
            return True

        try:
            contract = GoalContract(
                "Two autonomous turns only: on the first turn output STAGE_ONE and end the turn, "
                "leaving the goal active. On the second turn mark the goal complete using update_goal "
                "and output STAGE_TWO_DONE. Do not read files, run commands or use other tools.",
                ("Two native turns, ending in model-reported completion; not verified business delivery.",),
                max_turns=3, max_tokens=180000, max_elapsed_seconds=120)
            if not pause_resume_only:
                backend = NativeGoalBackend(checkpoint=checkpoint)
                backends.append(backend)
                backend.start(TaskSpec("No external actions. Protocol-only probe.", cwd,
                                       approval_policy="untrusted", config=config,
                                       goal_contract=contract, generation=1))
                thread_ids.add(backend._goal_thread)
                report["version"] = backend._version
                report["normalCompletionObserved"] = wait_until(lambda:
                    (backend.progress_snapshot() or {}).get("state") == "awaiting_verification")
                report["normalCompletion"] = backend.progress_snapshot()
                backend.shutdown()

            contract = GoalContract(
                "On each autonomous turn output PAUSE_TICK once, then end the turn. "
                "Leave the goal active; an external operator will pause it. "
                "Do not run commands, read files, use network or any tools except goal tools.",
                ("External pause and explicit resume protocol only, no business completion claim.",),
                max_turns=8, max_tokens=180000, max_elapsed_seconds=120)
            backend = NativeGoalBackend(checkpoint=checkpoint)
            backends.append(backend)
            spec = TaskSpec("No external actions. Protocol-only pause/resume probe.", cwd,
                            approval_policy="untrusted", config=config,
                            goal_contract=contract, generation=2)
            backend.start(spec)
            thread_id = backend._goal_thread
            thread_ids.add(thread_id)
            report["firstTurnCompletedNormally"] = wait_until(lambda:
                "completed" in (backend.progress_snapshot() or {}).get("turnResults", {}).values(), 50)
            report["version"] = backend._version
            report["pause"] = backend.interrupt(scope(backend)).to_dict()
            before = backend.progress_snapshot()
            time.sleep(2)
            after = backend.progress_snapshot()
            report["noRestartAfterPause"] = (after["turnsStarted"] == before["turnsStarted"] and
                                             not backend._active_turn and after["stopConfirmed"])
            backend.shutdown()
            snapshot = json.loads((Path(cwd) / "progress.json").read_text(encoding="utf-8"))
            backend = NativeGoalBackend(checkpoint=checkpoint)
            backends.append(backend)
            resumed_spec = TaskSpec(spec.goal, cwd, approval_policy="untrusted", config=config,
                                    goal_contract=contract, generation=3, resume_thread_id=thread_id)
            backend.start(resumed_spec, resume_progress=snapshot)
            previous_turn_ids = set(snapshot.get("startedTurnIds") or [])
            report["resumeCompletedTurn"] = wait_until(lambda:
                any(turn_id not in previous_turn_ids and status == "completed"
                    for turn_id, status in (backend.progress_snapshot() or {}).get("turnResults", {}).items()), 60)
            report["resumeEvents"] = [{"kind": event.kind, "turnId": event.turn_id,
                                        "payload": event.payload}
                                       for event in backend.events(0)
                                       if event.kind in ("turn_finished", "note") and
                                       not event.payload.get("agentDelta")]
            report["resumedPause"] = backend.interrupt(scope(backend)).to_dict()
            resumed = backend.progress_snapshot()
            report["resume"] = resumed
            report["identityAndBudgetsPreserved"] = (
                resumed["goalId"] == snapshot["goalId"] and resumed["threadId"] == thread_id and
                resumed["tokensUsed"] >= snapshot["tokensUsed"] and
                resumed["elapsedSeconds"] >= snapshot["elapsedSeconds"] and
                resumed["turnsStarted"] > snapshot["turnsStarted"])
        except Exception as exc:
            report["error"] = {"type": type(exc).__name__, "message": str(exc)[:500]}
        finally:
            for backend in reversed(backends):
                if backend._goal_thread:
                    thread_ids.add(backend._goal_thread)
                backend.shutdown()
            cleaner = NativeGoalBackend()
            report["deletedThreads"] = {thread_id: cleaner.delete_session(thread_id)
                                        for thread_id in thread_ids}
            cleaner.shutdown()
    report["workspaceRemoved"] = not Path(cwd).exists()
    normal = report.get("normalCompletion") or {}
    normal_passed = pause_resume_only or all((report.get("normalCompletionObserved"),
                                             normal.get("turnsCompleted", 0) >= 2,
                                             normal.get("verificationPassed") is False))
    report["passed"] = all((normal_passed, report.get("firstTurnCompletedNormally"),
                             report.get("pause", {}).get("ok"), report.get("noRestartAfterPause"),
                             report.get("resumeCompletedTurn"), report.get("resumedPause", {}).get("ok"),
                             report.get("identityAndBudgetsPreserved"), report["workspaceRemoved"],
                             all(report["deletedThreads"].values()), "error" not in report))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Permit finite, billable real model calls")
    parser.add_argument("--output", default="docs/codex_stage3d_native_adapter_20261028.json")
    parser.add_argument("--pause-resume-only", action="store_true")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required; no model calls without explicit opt-in")
    report = run_probe(args.pause_resume_only)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
