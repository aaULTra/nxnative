"""Synthetic tests for safe public monitoring and local MCD2 recovery."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mcd2_observed_build as monitor  # noqa: E402


class TestMcd2Monitor(unittest.TestCase):
    def test_phases_never_repeat_private_names(self):
        self.assertEqual(monitor.phase_name("UE shader decode"), "Decode Unreal shaders")
        self.assertEqual(monitor.phase_name("shader split"), "Split and inventory shaders")
        self.assertEqual(monitor.phase_name("/private/secret.nsp"), "Other build stage")

    def test_local_checkpoint_does_not_stream_raw_logs(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            script = (
                "import json,time;"
                "print(json.dumps({'ev':'stage','name':'UE shader decode'}),flush=True);"
                "print('PRIVATE-CONTENT-NOT-FOR-PUBLIC-LOGS',flush=True);"
                "time.sleep(2.3);"
                "print(json.dumps({'ev':'stage_end','name':'UE shader decode',"
                "'ok':True}),flush=True)"
            )
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                result = monitor.stage_observed(
                    "synthetic", [sys.executable, "-c", script], 15,
                    {}, folder / "private.log", folder, folder)
            self.assertEqual(result["code"], 0)
            self.assertIn("[STAGE] completed Decode Unreal shaders; no reusable checkpoint verified",
                          captured.getvalue())
            self.assertEqual(result["phase"], "Decode Unreal shaders")
            self.assertNotIn("PRIVATE-CONTENT-NOT-FOR-PUBLIC-LOGS", captured.getvalue())
            self.assertFalse((folder / "mcd2-progress-checkpoints.json").exists())
            self.assertIn("PRIVATE-CONTENT-NOT-FOR-PUBLIC-LOGS",
                          (folder / "private.log").read_text())

    def test_verified_rewrite_checkpoint_when_stamp_and_outputs_exist(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            work = folder / f"{monitor.base.GAME}_build"
            patched = work / "patched_v65536"
            patched.mkdir(parents=True)
            (work / "patched_v65536.key").write_text("a" * 64 + "\\n")
            for module in ("main", "sdk", "subsdk0"):
                (patched / f"{module}.patched.bin").write_bytes(b"test")
            script = (
                "import json;"
                "print(json.dumps({'ev':'stage','name':'rewrite'}),flush=True);"
                "print(json.dumps({'ev':'stage_end','name':'rewrite','ok':True}),flush=True)"
            )
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                result = monitor.stage_observed(
                    "synthetic", [sys.executable, "-c", script], 15,
                    {}, folder / "private.log", folder, folder)
            self.assertEqual(result["code"], 0)
            self.assertIn("[CHECKPOINT] verified rewritten-modules", captured.getvalue())
            rows = json.loads((folder / "mcd2-progress-checkpoints.json").read_text())
            self.assertEqual(rows[0]["verified_output"], "rewritten-modules")

    def test_idle_watchdog_stops_a_true_hang(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            with (patch.object(monitor, "descendants_cpu", return_value=0.0),
                  patch.object(monitor, "STALL_LOG_SECONDS", 0.1),
                  patch.object(monitor, "STALL_IDLE_SECONDS", 0.1),
                  patch.object(monitor, "HEARTBEAT_SECONDS", 0.1)):
                captured = io.StringIO()
                with contextlib.redirect_stdout(captured):
                    result = monitor.stage_observed(
                        "synthetic", [sys.executable, "-c", "import time;time.sleep(20)"],
                        15, {}, folder / "private.log", folder, folder)
            self.assertEqual(result["code"], 124)
            self.assertIn("[WATCHDOG]", captured.getvalue())
            self.assertIn("Low CPU", str(result["error"]))

    def test_long_shader_stage_budget_and_short_ordinary_budget(self):
        with patch.object(monitor, "run_stage_once", return_value={"code": 0}) as call:
            monitor.stage_observed("mac-shaders-and-host", [], 3300, {}, None, None, None)
            self.assertEqual(call.call_args.args[2], 4500)
            monitor.stage_observed("ios-ipa-build", [], 3300, {}, None, None, None)
            self.assertEqual(call.call_args.args[2], 3300)

    def test_guard_remains_enforced_for_observed_driver(self):
        repo = Path(__file__).resolve().parents[2]
        monitor.base.guard(repo)


if __name__ == "__main__":
    unittest.main()
