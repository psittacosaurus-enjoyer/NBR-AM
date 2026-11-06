"""Observe Ninja Road state/events without connecting ADB or sending game input.

An output directory is one durable observation window. Reusing it resumes the
same baseline and deduplicates terminal run IDs. Defeats are reported separately
and never count toward the requested successful configured exits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time


ROOT = Path(__file__).resolve().parents[1]
TERMINALS = {
    "ninja_early_exit_completed": "early_exit",
    "ninja_attempt_completed": "completed",
    "ninja_attempt_failed": "failed",
    "ninja_mission_returned_home": "abandoned",
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(path)


def default_progress():
    """Resolve saved runtime metadata only, without importing the controller."""
    runtime = ROOT / "runtime"
    selected_path = runtime / "instance_selection.json"
    if not selected_path.exists():
        return runtime / "ninja_road.json"
    selected = read_json(selected_path)
    owner_path = runtime / "legacy_instance.json"
    if owner_path.exists():
        owner = read_json(owner_path)
        if (owner.get("key"), owner.get("android_id")) == (selected.get("key"), selected.get("android_id")):
            return runtime / "ninja_road.json"
    identity = hashlib.sha256((selected["key"] + "\0" + selected["android_id"]).encode()).hexdigest()[:24]
    candidate = runtime / "instances" / identity / "ninja_road.json"
    if candidate.exists():
        return candidate
    raise ValueError("Selected instance progress is unavailable; pass --progress with its verified status path")


def observation(data, now):
    boss = data.get("boss_swap") or {}
    strategy = data.get("stage15_strategy") or {}
    pending = data.get("pending") or strategy.get("pending") or boss.get("pending") or {}
    run = data.get("run") or {}
    return {
        "time": now, "heartbeat": data.get("heartbeat"),
        "screen": data.get("screen"), "map": data.get("stage15_map"),
        "team": data.get("current_team"), "run": run, "counts": data.get("counts", {}),
        "blocked": data.get("blocked"), "boss_phase": boss.get("phase"),
        "strategy_phase": strategy.get("phase"),
        "pending": {key: pending[key] for key in
                    ("id", "purpose", "time", "column", "source", "returned") if key in pending},
    }


def terminal_result(run_id, outcome, ended, mode, cleared_map, baseline):
    expected_map = {"stage10": 10, "stage15": 15, "stage15_clean": 15}.get(baseline["exit_mode"])
    success = outcome == "completed" or (
        outcome == "early_exit" and cleared_map == expected_map)
    if mode and mode != baseline["exit_mode"]:
        success = False
    return {"run_id": run_id, "outcome": outcome, "ended": ended,
            "exit_mode": mode or baseline["exit_mode"], "cleared_map": cleared_map,
            "successful": success,
            "observed_from_start": run_id != baseline.get("active_run_id")}


class Monitor:
    def __init__(self, output, progress, events, expected=3, *, now=None):
        self.output, self.progress, self.events = map(Path, (output, progress, events))
        self.output.mkdir(parents=True, exist_ok=True)
        now = time.time() if now is None else now
        baseline_path = self.output / "baseline.json"
        if baseline_path.exists():
            self.baseline = read_json(baseline_path)
            if (self.baseline["expected_successes"] != expected
                    or self.baseline["progress_path"] != str(self.progress.resolve())):
                raise ValueError("Output directory belongs to a different observation window")
        else:
            data = read_json(self.progress)
            run = data.get("run") or {}
            settings_path = ROOT / "runtime/manager_settings.json"
            settings = read_json(settings_path) if settings_path.exists() else {}
            self.baseline = {
                "started": now, "expected_successes": expected,
                "progress_path": str(self.progress.resolve()),
                "events_path": str(self.events.resolve()),
                "event_offset": self.events.stat().st_size if self.events.exists() else 0,
                "exit_mode": run.get("exit_mode", settings.get("ninja_exit_mode", "stage10")),
                "active_run_id": run.get("id") if not run.get("outcome") else None,
                "counts": data.get("counts", {}), "instance": data.get("instance"),
            }
            write_json(baseline_path, self.baseline)
        summary_path = self.output / "summary.json"
        self.summary = read_json(summary_path) if summary_path.exists() else {
            "status": "running", "runs": [], "observations": 0, "seen_runs": {},
        }
        self.last_key = None
        self.stall_key, self.stall_since = None, now

    def results_from_events(self):
        # The controller rotates one 2 MB log. Re-reading these bounded files
        # tolerates rotation/restarts and run-ID deduplication keeps this cheap.
        results = {}
        for path in (self.events.with_name(self.events.name + ".1"), self.events):
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if not any(name in line for name in TERMINALS):
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue  # A writer may still be appending the last line.
                name, ended, run_id = event.get("event"), event.get("time", 0), event.get("run_id")
                if (name in TERMINALS and run_id in self.summary.get("seen_runs", {})
                        and ended >= self.baseline["started"]):
                    results[run_id] = terminal_result(
                        run_id, TERMINALS[name], ended, self.summary["seen_runs"][run_id],
                        event.get("cleared_map"), self.baseline)
        return results

    def poll(self, *, now=None, startup_grace=15, stale_seconds=90, stall_seconds=60):
        now = time.time() if now is None else now
        data = read_json(self.progress)
        observed = observation(data, now)
        run = data.get("run") or {}
        if run.get("id"):
            self.summary.setdefault("seen_runs", {})[run["id"]] = run.get("exit_mode", self.baseline["exit_mode"])
        results = {item["run_id"]: item for item in self.summary["runs"]}
        results.update(self.results_from_events())
        if run.get("outcome") and run.get("ended", 0) >= self.baseline["started"]:
            results[run["id"]] = terminal_result(
                run["id"], run["outcome"], run["ended"], run.get("exit_mode"),
                (data.get("exit_after_stage", results.get(run["id"], {}).get("cleared_map"))
                 if run["outcome"] == "early_exit" else None),
                self.baseline)
        runs = sorted(results.values(), key=lambda item: item["ended"])
        successes = sum(item["successful"] for item in runs)
        status, reason = "running", None
        if successes >= self.baseline["expected_successes"]:
            status, reason = "completed", "Requested successful exits observed"
        elif now - self.baseline["started"] >= startup_grace:
            if (ROOT / "runtime/STOP").exists():
                status, reason = "attention", "Controller stop requested before target completion"
            elif now - (data.get("heartbeat") or 0) > stale_seconds:
                status, reason = "attention", "Controller heartbeat is stale"
        pending = observed["pending"]
        stall_key = json.dumps({"pending": pending, "blocked": observed["blocked"]}, sort_keys=True)
        if stall_key != self.stall_key:
            self.stall_key, self.stall_since = stall_key, now
        if (status == "running" and (pending or observed["blocked"])
                and now - self.stall_since >= stall_seconds
                and (not pending or now - pending.get("time", now) >= stall_seconds)):
            status, reason = "attention", "Unresolved input or blocked state persisted"
        key = json.dumps({k: v for k, v in observed.items() if k not in {"time", "heartbeat"}}, sort_keys=True)
        changed = key != self.last_key
        if changed:
            self.summary["observations"] += 1
            observed["sequence"] = self.summary["observations"]
            latest = self.progress.with_name("ninja_live_latest.jpg")
            if latest.exists() and 0 <= now - latest.stat().st_mtime < 15:
                filename = f"frame_{observed['sequence']:05d}.jpg"
                shutil.copy2(latest, self.output / filename)
                observed["screenshot"] = filename
                observed["screenshot_mtime"] = latest.stat().st_mtime
            with (self.output / "observations.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(observed) + "\n")
            self.last_key = key
        # Request only the next worker-owned frame; this never opens ADB.
        self.progress.with_name("ninja_live_request").touch()
        self.summary.update({"status": status, "reason": reason, "updated": now,
                             "baseline": self.baseline, "runs": runs,
                             "successful_runs": successes, "terminal_runs": len(runs),
                             "failed_runs": sum(item["outcome"] == "failed" for item in runs),
                             "latest": observed})
        write_json(self.output / "summary.json", self.summary)
        return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--progress", type=Path, help="Pinned progress path; defaults to saved instance metadata")
    parser.add_argument("--events", type=Path, default=ROOT / "runtime/events.jsonl")
    parser.add_argument("--expected", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=float, default=10800)
    parser.add_argument("--poll-seconds", type=float, default=3)
    parser.add_argument("--startup-grace-seconds", type=float, default=15)
    parser.add_argument("--once", action="store_true", help="Update the durable window once, then return")
    args = parser.parse_args()
    if min(args.expected, args.timeout_seconds, args.poll_seconds) <= 0:
        parser.error("Expected count, timeout, and polling interval must be positive")
    monitor = Monitor(args.output_dir, args.progress or default_progress(), args.events, args.expected)
    last_print = 0
    while True:
        now = time.time()
        try:
            changed = monitor.poll(now=now, startup_grace=args.startup_grace_seconds)
        except (OSError, ValueError) as exc:
            monitor.summary.update(status="attention", reason=f"Cannot read/write observation evidence: {exc}", updated=now)
            write_json(monitor.output / "summary.json", monitor.summary)
            print(json.dumps(monitor.summary), flush=True)
            return 3
        if monitor.summary["status"] == "running" and now - monitor.baseline["started"] >= args.timeout_seconds:
            monitor.summary.update(status="timeout", reason="Observation time limit reached")
            write_json(monitor.output / "summary.json", monitor.summary)
        if changed or now - last_print >= 30 or monitor.summary["status"] != "running":
            latest = monitor.summary["latest"]
            print(json.dumps({
                "status": monitor.summary["status"], "reason": monitor.summary["reason"],
                "successful_runs": monitor.summary["successful_runs"], "expected": args.expected,
                "failed_runs": monitor.summary["failed_runs"], "terminal_runs": monitor.summary["terminal_runs"],
                "screen": latest["screen"], "team": latest["team"], "map": latest["map"],
                "run_id": latest["run"].get("id"), "boss_phase": latest["boss_phase"],
                "pending": latest["pending"], "blocked": latest["blocked"],
            }), flush=True)
            last_print = now
        if args.once or monitor.summary["status"] != "running":
            return {"completed": 0, "running": 0, "timeout": 2, "attention": 3}[monitor.summary["status"]]
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
