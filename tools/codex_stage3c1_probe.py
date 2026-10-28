"""Version-scoped protocol probe. Model calls require --live; goals also require --native-goals."""

from __future__ import annotations

import argparse
import hashlib
import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain.runtime.backend import TaskSpec
from brain.runtime.codex_backend import (
    CodexAppServerClient, CodexExecutionBackend, CodexRpcError, _checked_result,
    default_codex_bin,
)


def safe_error(exc):
    return {"type": type(exc).__name__, "method": getattr(exc, "method", ""),
            "code": getattr(exc, "code", None)}


def run_cli(exe, *args, timeout=20):
    return subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def rpc(client, method, params=None):
    return _checked_result(method, client.request(method, params, timeout=15))


class Probe:
    def __init__(self, exe):
        self.exe = exe
        self.notifications = queue.Queue()
        self.thread_ids = set()
        self.timeline = []
        self.client = None
        self.active = {}
        self.deadline = 0
        self.restricted_config = {"web_search": "disabled", "features.multi_agent": False,
                                  "features.shell_tool": False}

    def open(self):
        def server_request(message):
            method = message.get("method", "")
            if method == "currentTime/read":
                return {"currentTimeAt": int(time.time())}, None
            if "Approval" in method:
                return {"decision": "decline"}, None
            return None, {"code": -32601, "message": "probe rejects external tools"}

        self.client = CodexAppServerClient(self.exe, on_notification=self.notifications.put,
                                          on_server_request=server_request)
        self.client.start()
        result = rpc(self.client, "initialize", {"clientInfo": {"name": "lianxin-3c1-probe", "version": "1"},
                                                 "capabilities": {"experimentalApi": True}})
        self.client.notify("initialized")
        return result

    def collect(self, seconds=0.2):
        deadline = time.monotonic() + seconds
        collected = []
        while time.monotonic() < deadline:
            try:
                note = self.notifications.get(timeout=min(0.1, max(0.001, deadline - time.monotonic())))
            except queue.Empty:
                continue
            method = note.get("method", "")
            params = note.get("params") or {}
            thread_id = params.get("threadId", "")
            turn = params.get("turn") or {}
            turn_id = params.get("turnId") or turn.get("id") or ""
            if method == "turn/started":
                self.active[thread_id] = turn_id
            elif method == "turn/completed" and self.active.get(thread_id) == turn_id:
                self.active.pop(thread_id, None)
            if thread_id in self.thread_ids:
                if method in ("turn/started", "turn/completed", "thread/goal/updated", "error"):
                    self.timeline.append({"method": method, "threadId": thread_id, "turnId": turn_id,
                                          "status": turn.get("status") or (params.get("goal") or {}).get("status"),
                                          "at": round(time.monotonic(), 3)})
                collected.append(note)
        return collected

    def wait(self, predicate, seconds=30):
        deadline = min(time.monotonic() + seconds, self.deadline or float("inf"))
        while time.monotonic() < deadline:
            self.collect()
            if predicate():
                return True
            if self.client is None or not self.client.alive():
                break
        return False

    def started(self, offset):
        return [item for item in self.timeline[offset:] if item["method"] == "turn/started"]

    def completed(self, offset):
        return [item for item in self.timeline[offset:] if item["method"] == "turn/completed"
                and item["status"] == "completed"]

    def pause_stop(self, thread_id):
        goal = rpc(self.client, "thread/goal/set", {"threadId": thread_id, "status": "paused"})
        self.collect(0.1)
        turn_id = self.active.get(thread_id)
        if turn_id:
            try:
                rpc(self.client, "turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
            except CodexRpcError:
                current = rpc(self.client, "thread/read", {"threadId": thread_id, "includeTurns": True})
                if any(turn.get("id") == turn_id and turn.get("status") == "inProgress"
                       for turn in current["thread"].get("turns", [])):
                    raise
        return goal

    def native_goals(self, workspace):
        self.deadline = time.monotonic() + 100
        client = self.client
        timer = threading.Timer(105, client.terminate)
        timer.daemon = True
        timer.start()
        result = {}
        thread_id = ""
        try:
            thread = rpc(client, "thread/start", {
                "cwd": workspace, "sandbox": "read-only", "approvalPolicy": "untrusted", "ephemeral": False,
                "baseInstructions": "You are a bounded protocol probe. Never read/write files, run commands, access network, or spawn agents. Use only goal tools if needed.",
                "config": {**self.restricted_config, "features.goals": True},
            })
            thread_id = thread["thread"]["id"]
            self.thread_ids.add(thread_id)
            result["threadId"] = thread_id
            offset = len(self.timeline)
            rpc(client, "turn/start", {"threadId": thread_id,
                "input": [{"type": "text", "text": "Reply exactly PROBE_READY. Do not use tools."}]})
            if not self.wait(lambda: len(self.completed(offset)) >= 1, 30):
                result["warmup"] = "unknown"
                return result
            result["warmup"] = "passed"
            objective = ("Bounded native goal continuation probe. Each assistant turn must output exactly PROBE_TICK "
                         "once, then end that turn. Keep the goal active, do not mark complete or blocked. "
                         "The external probe operator will pause the goal after two completed turns. "
                         "Do not use any tools, shell, files, network, subagents, or ask the operator for input.")
            paused = rpc(client, "thread/goal/set", {"threadId": thread_id, "objective": objective,
                                                    "status": "paused", "tokenBudget": 40000})
            result["setPaused"] = paused["goal"]["status"]
            result["getPaused"] = rpc(client, "thread/goal/get", {"threadId": thread_id})["goal"]["status"]
            self.collect(1)
            offset = len(self.timeline)
            rpc(client, "thread/goal/set", {"threadId": thread_id, "status": "active"})
            continued = self.wait(lambda: len(self.completed(offset)) >= 2, 40)
            result["crossTurnContinuation"] = "passed" if continued else "unknown"
            result["autonomousCompletedTurns"] = len(self.completed(offset))
            result["pauseResponse"] = self.pause_stop(thread_id)["goal"]["status"]
            self.collect(0.5)
            stopped_offset = len(self.timeline)
            self.collect(3)
            result["noRestartAfterPauseAndInterrupt"] = not self.started(stopped_offset)
            result["pausedSnapshot"] = rpc(client, "thread/goal/get", {"threadId": thread_id})["goal"]
            offset = len(self.timeline)
            rpc(client, "thread/goal/set", {"threadId": thread_id, "status": "active"})
            resumed = self.wait(lambda: len(self.completed(offset)) >= 1, 20)
            result["resumePausedGoal"] = "passed" if resumed else "unknown"
            self.pause_stop(thread_id)
            client.terminate()
            self.collect(0.1)
            self.open()
            client = self.client
            self.active.clear()
            offset = len(self.timeline)
            rpc(client, "thread/resume", {"threadId": thread_id, "cwd": workspace, "sandbox": "read-only",
                                          "approvalPolicy": "untrusted", "excludeTurns": True,
                                          "config": {**self.restricted_config, "features.goals": True}})
            self.collect(3)
            snapshot = rpc(client, "thread/goal/get", {"threadId": thread_id})["goal"]
            result["restartPreservesPause"] = snapshot["status"] == "paused" and not self.started(offset)
            result["timeline"] = list(self.timeline)
        except Exception as exc:
            result["error"] = safe_error(exc)
        finally:
            if thread_id and client.alive():
                try:
                    self.pause_stop(thread_id)
                    rpc(client, "thread/goal/clear", {"threadId": thread_id})
                    result["cleared"] = rpc(client, "thread/goal/get", {"threadId": thread_id})["goal"] is None
                except Exception as exc:
                    result["cleanupError"] = safe_error(exc)
            timer.cancel()
        return result

    def close(self):
        if self.client is not None:
            self.client.terminate()
            self.client = None
        cleanup = {}
        for thread_id in self.thread_ids:
            try:
                command = run_cli(self.exe, "delete", "--force", thread_id, timeout=60)
                cleanup[thread_id] = command.returncode == 0
            except Exception as exc:
                cleanup[thread_id] = safe_error(exc)
        return cleanup


def protocol_report(exe, probe):
    result = {"binary": str(Path(exe)).replace(str(Path.home()), "%USERPROFILE%"),
              "binarySha256": hashlib.sha256(Path(exe).read_bytes()).hexdigest(),
              "version": run_cli(exe, "--version").stdout.strip(),
              "initializeRequest": {"capabilities": {"experimentalApi": True}}}
    initialized = probe.open()
    result["initializeResponse"] = {key: initialized.get(key) for key in ("userAgent", "platformFamily", "platformOs")}
    config = rpc(probe.client, "config/read").get("config") or {}
    mcp_servers = config.get("mcp_servers") or {}
    probe.restricted_config["mcp_servers"] = {name: {"enabled": False} for name in mcp_servers}
    result["disabledMcpServerCount"] = len(mcp_servers)
    result["modelConfig"] = {key: config.get(key) for key in ("model", "model_provider", "model_reasoning_effort")}
    features = rpc(probe.client, "experimentalFeature/list", {"limit": 100})
    result["goalsFeature"] = [{key: item.get(key) for key in ("name", "stage", "enabled", "defaultEnabled")}
                              for item in features.get("data", []) if item.get("name") == "goals"]
    names = ("TurnSteerParams", "TurnSteerResponse", "TurnInterruptParams", "ThreadResumeParams",
             "ThreadGoalSetParams", "ThreadGoalStatus", "ThreadGoal", "ThreadTokenUsageUpdatedNotification")
    with tempfile.TemporaryDirectory(prefix="lianxin-3c1-schema-") as directory:
        command = run_cli(exe, "app-server", "generate-ts", "--experimental", "--out", directory, timeout=30)
        if command.returncode:
            raise RuntimeError("schema generation failed")
        result["schema"] = {}
        for name in names:
            path = Path(directory) / "v2" / (name + ".ts")
            source = path.read_text(encoding="utf-8")
            result["schema"][name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                       "declaration": source[source.index("export type"):].strip()}
    return result


def adapter_live(exe, workspace, config):
    backend = CodexExecutionBackend(exe=exe)
    thread_id = ""
    timer = threading.Timer(50, backend.shutdown)
    timer.daemon = True
    timer.start()
    result = {}
    try:
        handle = backend.start(TaskSpec(
            "Protocol probe only. Do not use tools, shell, files, network, or subagents. "
            "Print the integers 1 through 300 one per line, slowly and in order.",
            workspace, approval_policy="untrusted", config=config, generation=1))
        thread_id = handle.thread_id
        result["threadId"] = thread_id
        features = rpc(backend._client, "experimentalFeature/list", {"threadId": thread_id, "limit": 100})
        result["threadGoalsEnabled"] = next((item["enabled"] for item in features.get("data", [])
                                             if item.get("name") == "goals"), None)
        backend._turn_ready.wait(5)
        try:
            rpc(backend._client, "turn/steer", {"threadId": thread_id, "expectedTurnId": "probe-stale-turn",
                                              "input": [{"type": "text", "text": "Must be rejected."}]})
            result["wrongExpectedTurnRejected"] = False
        except CodexRpcError as exc:
            result["wrongExpectedTurnRejected"] = True
            result["wrongExpectedTurnError"] = safe_error(exc)
        result["steer"] = backend.steer(handle, "Stop counting. Reply exactly PROBE_STEER_ACK and end the turn.").to_dict()
        result["interrupt"] = backend.interrupt(handle).to_dict()
        deadline = time.monotonic() + 8
        observed = []
        while time.monotonic() < deadline:
            observed.extend(backend.events(0.2))
            if any(event.kind == "turn_finished" for event in observed):
                break
        result["events"] = [{"kind": event.kind, "turnId": event.turn_id, "generation": event.generation,
                             "status": event.payload.get("finalStatus")} for event in observed
                            if event.kind in ("turn_finished", "task_progress")]
        backend.shutdown()
        resumed = backend.start(TaskSpec("Reply exactly PROBE_RESUME_ACK. Do not use any tools.", workspace,
                                        approval_policy="untrusted", config=config,
                                        resume_thread_id=thread_id, generation=2))
        result["resume"] = {"sameThread": resumed.thread_id == thread_id,
                            "newTurn": resumed.turn_id != handle.turn_id, "generation": resumed.generation}
        result["resumeStop"] = backend.interrupt(resumed).to_dict()
    except Exception as exc:
        result["error"] = safe_error(exc)
    finally:
        backend.shutdown()
        timer.cancel()
        if thread_id:
            result["deleted"] = backend.delete_session(thread_id)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default="")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--native-goals", action="store_true")
    parser.add_argument("--output", default="docs/codex_stage3c1_probe_20261027.json")
    args = parser.parse_args()
    if args.native_goals and not args.live:
        parser.error("--native-goals requires --live (model calls incur usage)")
    exe = args.codex or default_codex_bin()
    probe = Probe(exe)
    report = {"phase": "3c-1", "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "liveRequested": args.live, "nativeGoalsRequested": args.native_goals}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    def checkpoint():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        report.update(protocol_report(exe, probe))
        checkpoint()
        with tempfile.TemporaryDirectory(prefix="lianxin-3c1-workspace-", ignore_cleanup_errors=True) as workspace:
            try:
                if args.live:
                    report["adapterLive"] = adapter_live(exe, workspace, probe.restricted_config)
                    checkpoint()
                if args.native_goals:
                    report["nativeGoals"] = probe.native_goals(workspace)
                    checkpoint()
            finally:
                if probe.client is not None:
                    probe.client.terminate()
        report["workspaceRemoved"] = not Path(workspace).exists()
    except Exception as exc:
        report["error"] = safe_error(exc)
    finally:
        report["cleanup"] = probe.close()
    passed = "error" not in report
    if args.live:
        adapter = report.get("adapterLive") or {}
        passed = passed and all((adapter.get("steer", {}).get("ok"), adapter.get("interrupt", {}).get("ok"),
                                 adapter.get("resume", {}).get("sameThread"), adapter.get("deleted")))
    if args.native_goals:
        native = report.get("nativeGoals") or {}
        passed = passed and all((native.get("crossTurnContinuation") == "passed",
                                 native.get("resumePausedGoal") == "passed", native.get("restartPreservesPause"),
                                 native.get("noRestartAfterPauseAndInterrupt"), native.get("cleared")))
    report["validationPassed"] = bool(passed)
    checkpoint()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(not passed)


if __name__ == "__main__":
    raise SystemExit(main())
