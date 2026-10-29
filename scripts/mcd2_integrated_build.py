#!/usr/bin/env python3
"""One owner-initiated MCD2 iOS package build.

GitHub's public repository must never publish game binaries or keys.
Public artifacts contain compact text only; the workflow separately delivers
the verified IPA into the owner's Drive output file.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import time

GAME = "0100A7C01B792000"
VERSION = "v65536"
PROFILE_MAX = 0x140000000
# Run 40888376530 reached packaging at ~53 minutes and was killed at 55.
# Build only the target's shaders and allow a bounded three-hour cold build.
BUILD_LIMIT = 10800
BOOT_LIMIT = 35


def guard(repo):
    profile = (repo / "dm/games" / GAME / "game.tsv").read_text()
    signing = (repo / "dm/ios/tools/build_ipa.sh").read_text()
    matches = re.findall(r"^mem_reserve\s+(0x[0-9a-fA-F]+|\d+)\s*$", profile, re.M)
    if len(matches) != 1 or int(matches[0], 0) != PROFILE_MAX:
        raise ValueError("MCD2 must keep the verified 5 GiB guest reservation")
    if "com.apple.developer.kernel.increased-memory-limit" not in signing:
        raise ValueError("Increased Memory Limit request is absent")
    if "com.apple.developer.kernel.extended-virtual-addressing" in signing:
        raise ValueError("Extended Virtual Addressing cannot be required")


def private_error(path, redact):
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    chosen = [s.strip() for s in lines if re.search(
        r"\[RED\]|Traceback|BuildError|Error:|failed|exception|abort|unhandled",
        s, re.I)]
    if not chosen:
        return ""
    last = chosen[-1].replace(str(redact), "<private-runner>")
    return re.sub(r"[^\x20-\x7e]", "?", last)[:220].replace("|", "/")


def stage(name, cmd, timeout, env, log, repo, redact):
    print(f"start {name}", flush=True)
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as out:
        try:
            result = subprocess.run(cmd, cwd=repo, env=env, stdin=subprocess.DEVNULL,
                                    stdout=out, stderr=subprocess.STDOUT,
                                    timeout=timeout, check=False)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
        except OSError as exc:
            out.write(f"command failed: {type(exc).__name__}\n")
            code = 127
    seconds = round(time.monotonic() - started)
    error = private_error(log, redact) if code else ""
    print(f"finished {name}: exit={code}, seconds={seconds}", flush=True)
    return dict(name=name, code=code, seconds=seconds, error=error)


def boot_smoke(work, repo, env, log, redact):
    host = work / "tools/dmhost"
    patched = work / f"patched_{VERSION}"
    romfs = work / f"extract/{VERSION}/RomFS.img"
    library = work / "shader/cache"
    shader_map = work / "profile" / GAME / "shader_map.tsv"
    if not shader_map.is_file():
        shader_map = repo / "dm/games" / GAME / "shader_map.tsv"
    if not (host.is_file() and romfs.is_file() and (patched / "main.patched.bin").is_file()):
        return dict(name="headless-mac-boot", code=125, seconds=0,
                    error="The Mac host, rewritten main, or merged RomFS image was not built")
    for folder in ("run/save", "run/temp"):
        (work / folder).mkdir(parents=True, exist_ok=True)
    benv = dict(env,
                DM_ROOT=str(work), DM_ROMFS=str(romfs),
                DM_SAVE=str(work / "run/save"), DM_TEMP=str(work / "run/temp"),
                DM_MSL_DIR=str(library), DM_FONT_DIR=str(work / "font"),
                DM_NVN_SHMAP=str(shader_map), DM_LANG="en-US",
                DM_WINDOW="0", DM_AUDIO="0", DM_SVC="1",
                DM_WATCH="20", DM_WATCH_STALL="15")
    started = time.monotonic()
    print("start headless-mac-boot", flush=True)
    with log.open("w", encoding="utf-8") as out:
        try:
            proc = subprocess.Popen([str(host), str(patched)], cwd=work / "run",
                                    env=benv, stdin=subprocess.DEVNULL,
                                    stdout=out, stderr=subprocess.STDOUT)
            try:
                code = proc.wait(timeout=BOOT_LIMIT)
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                code = 124
        except OSError:
            code, timed_out = 127, False
    t = log.read_text(errors="replace") if log.exists() else ""
    evidence = {
        "reached_nnosInitialize": ">>> nnosInitialize" in t,
        "reached_nnMain": ">>> nnMain" in t,
        "reported_frames": bool(re.search(r"\[nvn\] frame \d+", t)),
        "crash_marker": "CRASH sig=" in t,
        "timed_out": timed_out,
    }
    sec = round(time.monotonic() - started)
    print(f"finished headless-mac-boot: exit={code}, seconds={sec}", flush=True)
    return dict(name="headless-mac-boot", code=code, seconds=sec,
                error=private_error(log, redact) if code != 124 else "",
                evidence=evidence)


def make_report(results, output, ipa_path):
    state = {row["name"]: row for row in results}
    lines = ["# Minecraft Dungeons II iOS build", "",
             "Automated preflight only. Does not claim a playable iPad port.",
             "Build files are temporary. The workflow delivers the verified IPA to the owner's dedicated Drive output.",
             "Only this text report may be published in the public GitHub repository.", "",
             "## Results", "", "| Stage | Status | Runtime |",
             "|---|---|---:|"]
    for row in results:
        status = "complete" if row["code"] == 0 else "timed out" if row["code"] == 124 else "blocked/skipped" if row["code"] == 125 else "failed"
        lines.append(f'| {row["name"]} | {status} (exit {row["code"]}) | {row["seconds"]}s |')
    boot = state.get("headless-mac-boot", {})
    lines += ["", "## Mac boot evidence", ""]
    if not boot.get("evidence"):
        lines.append("- Not tested: this run builds the iOS package without a desktop shader pass.")
    for k, value in boot.get("evidence", {}).items():
        lines.append(f"- {k}: {value}")
    lines += ["", "## Last observed build phases", ""]
    for row in results:
        if row.get("phase"):
            lines.append(f'- {row["name"]}: {row["phase"]} (exit {row["code"]})')
    lines += ["", "## First actionable blockers", ""]
    errors = [row for row in results if row.get("error")]
    lines.extend(f'- {row["name"]}: {row["error"]}' for row in errors[:6])
    if not errors:
        lines.append("- No explicit error message was captured; inspect the stage status and boot evidence.")
    ios = state.get("ios-ipa-build", {})
    lines += ["", "## IPA packaging", ""]
    if ios.get("code") == 0 and ipa_path.is_file():
        lines.append(f"- Built an unsigned IPA ({ipa_path.stat().st_size:,} bytes) within the temporary runner.")
        lines.append("- NOT uploaded as a public artifact. The separate Drive delivery step must validate the archive and stored checksum before claiming delivery.")
    else:
        lines.append("- No complete unsigned IPA was produced.")
    lines += ["", "## Device constraints", "",
              "- Increased Memory Limit entitlement requested.",
              "- Extended Virtual Addressing entitlement NOT required or requested.",
              "- Single guest reservation: 5 GiB; Unreal allocator compatibility not assumed.",
              "- Packaging uses --no-heap-check; engine heap/address-region runtime validation is still required.",
              "- GitHub-hosted macOS lacks a usable gameplay GPU; Mac boot results do not establish Metal rendering on iPad.",
              ""]
    output.write_text("\n".join(lines), encoding="utf-8")


def run(args):
    repo = Path(__file__).resolve().parents[1]
    folder = Path(args.game_dir).resolve()
    keys = Path(args.keys).resolve()
    output = Path(args.report).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    logdir = folder / "private-logs"
    logdir.mkdir(parents=True, exist_ok=True)
    ipa = folder / "MinecraftDungeonsII.ipa"
    env = dict(os.environ, DM_GAME=GAME)
    steps = []
    try:
        guard(repo)
        if not keys.is_file():
            raise ValueError("Missing private prod.keys in ephemeral runner")
        common = [sys.executable, str(repo / "scripts/build_ipa_from_nsp.py"),
                  str(folder), "-k", str(keys), "--bootstrap-shaders",
                  "--no-heap-check", "--progress", "off"]
        # The iOS path retains the Mac helpers needed for Oodle decoding,
        # but compiles only iOS Metal libraries. A full desktop shader build
        # previously consumed 85 minutes before this target even started.
        steps.append(stage("ios-ipa-build", common + ["--platform", "ios",
                           "--romfs", "bundle", "--gpu", "none"],
                           BUILD_LIMIT, env, logdir / "ios.log", repo, folder))
    except Exception as exc:
        steps.append(dict(name="preflight", code=127, seconds=0, error=str(exc)[:180]))
    finally:
        make_report(steps, output, ipa)
    return 0 if all(row["code"] == 0 for row in steps if row["name"] != "headless-mac-boot") else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game_dir")
    parser.add_argument("--keys", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
