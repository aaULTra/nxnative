#!/usr/bin/env python3
"""Observe a private MCD2 end-to-end build without exposing raw game logs.

A second GitHub Actions job can run in parallel with the original. Completed
nxnative extraction/shader outputs remain cached ONLY inside this runner.
The public repository never receives game binaries, keys, shaders or IPA.
"""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

import mcd2_integrated_build as base

HEARTBEAT_SECONDS = 20
STALL_LOG_SECONDS = 600
STALL_IDLE_SECONDS = 180
PHASES = (
    (r"build nx_extract", "Prepare extractor"),
    (r"extract", "Extract merged base/update"),
    (r"rewrite", "Rewrite executable modules"),
    (r"UE shader decode|Oodle", "Decode Unreal shaders"),
    (r"shader split", "Split and inventory shaders"),
    (r"(?:iOS|Mac|NVN) shaders|shader libraries", "Translate and compile Metal shaders"),
    (r"Pipeline|PSO", "Compile shader pipelines"),
    (r"Mac host", "Build Apple Silicon host"),
    (r"app build|IPA pack|pack IPA|prepare Xcode", "Build/package iOS IPA"),
    (r"font", "Prepare game interface font"),
)


def phase_name(name):
    for pattern, phase in PHASES:
        if re.search(pattern, name, re.I):
            return phase
    return "Other build stage"


def descendants_cpu(pid):
    """Sum the CPU readings for this process group; None if ps is unavailable."""
    try:
        output = subprocess.check_output(
            ["ps", "-axo", "pid=,ppid=,%cpu="],
            text=True, stderr=subprocess.DEVNULL, timeout=5,
        )
        rows = [line.split() for line in output.splitlines()]
        edges = {}
        cpu = {}
        for row in rows:
            if len(row) != 3:
                continue
            try:
                child, parent, use = int(row[0]), int(row[1]), float(row[2])
            except ValueError:
                continue
            edges.setdefault(parent, []).append(child)
            cpu[child] = use
        pending, seen = [pid], set()
        while pending:
            current = pending.pop()
            if current not in seen:
                seen.add(current)
                pending.extend(edges.get(current, []))
        return round(sum(cpu.get(process, 0.0) for process in seen), 1)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def stop_group(proc):
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=7)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)


def stage_observed(name, cmd, timeout, env, log, repo, redact):
    # Never stream the child process's raw logs to a public Actions job:
    # a tool may include decrypted content or paths in its diagnostics.
    # Stream ONLY whitelisted stage descriptions and numeric progress.
    command = list(map(str, cmd))
    for i in range(len(command) - 1):
        if command[i] == "--progress":
            command[i + 1] = "json"
            break
    start = time.monotonic()
    log.parent.mkdir(parents=True, exist_ok=True)
    journal = redact / "mcd2-progress-checkpoints.json"
    checkpoints = []
    current = "Starting build"
    last_log_change = start
    last_idle = None
    last_heartbeat = start
    log_pos = 0
    pending = ""
    print(f"[OBSERVED] start {name}; watchdog enabled", flush=True)
    with log.open("wb") as out:
        proc = subprocess.Popen(
            command, cwd=repo, env=env, stdin=subprocess.DEVNULL,
            stdout=out, stderr=subprocess.STDOUT, start_new_session=True,
        )
        code = None
        stall = False
        while proc.poll() is None:
            now = time.monotonic()
            try:
                with log.open("rb") as reader:
                    reader.seek(log_pos)
                    new = reader.read(1024 * 1024)
                    log_pos = reader.tell()
            except OSError:
                new = b""
            if new:
                last_log_change = now
                pending += new.decode("utf-8", errors="replace")
                if len(pending) > 200000:
                    pending = pending[-4000:]
                lines = pending.split("\n")
                pending = lines.pop()
                for line in lines[-1500:]:
                    try:
                        event = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    ev = event.get("ev")
                    if ev in ("stage", "step", "progress"):
                        raw_name = event.get("name")
                        if isinstance(raw_name, str):
                            current = phase_name(raw_name)
                        if ev == "stage":
                            print(f"[OBSERVED] phase: {current}", flush=True)
                    elif ev == "stage_end" and event.get("ok") is True:
                        label = phase_name(str(event.get("name", "")))
                        checkpoint = {"phase": label, "seconds": round(now - start)}
                        checkpoints.append(checkpoint)
                        # Metadata marker only. Underlying private build
                        # outputs remain in this ephemeral runner, where
                        # nxnative's own fingerprint cache reuses them.
                        journal.write_text(json.dumps(checkpoints, indent=2))
                        print(f"[CHECKPOINT] completed {label}", flush=True)
            if now - last_heartbeat >= HEARTBEAT_SECONDS:
                usage = descendants_cpu(proc.pid)
                active = usage is not None and usage >= 1.0
                idle = None if active else (last_idle or now)
                last_idle = idle
                elapsed = round(now - start)
                status = "working" if active else "CPU low/unknown"
                print(f"[HEARTBEAT] {name}: {current}; {elapsed}s; "
                      f"log idle {round(now - last_log_change)}s; {status}"
                      + (f" ({usage:.1f}% CPU)" if usage is not None else ""),
                      flush=True)
                last_heartbeat = now
            if now - start >= timeout:
                code = 124
                print(f"[WATCHDOG] stage time limit reached for {name}", flush=True)
                break
            # Only stop a genuinely idle subprocess. Long, CPU-heavy
            # shader compilation must continue even without new log lines.
            if (now - last_log_change > STALL_LOG_SECONDS
                    and last_idle is not None
                    and now - last_idle > STALL_IDLE_SECONDS):
                code, stall = 124, True
                print(f"[WATCHDOG] no log output and low CPU for prolonged period "
                      f"({name}); stopping this stage", flush=True)
                break
            time.sleep(2)
        if code is not None:
            stop_group(proc)
        else:
            code = proc.wait()
    sec = round(time.monotonic() - start)
    error = base.private_error(log, redact) if code else ""
    if stall:
        error = "Low CPU and no build-log activity for over 10 minutes; stalled process group terminated"
    elif code == 124:
        error = "Build stage exceeded the configured time limit; child process group terminated"
    print(f"[OBSERVED] finished {name}; exit={code}; elapsed={sec}s; "
          f"{len(checkpoints)} completed local checkpoints", flush=True)
    return {"name": name, "code": code, "seconds": sec, "error": error}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game_dir")
    parser.add_argument("--keys", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    base.stage = stage_observed
    return base.run(args)


if __name__ == "__main__":
    sys.exit(main())
