"""No NSP needed: exercise the integrated MCD2 driver and safety constraints."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mcd2_integrated_build as integrated  # noqa: E402


class TestMcd2Integrated(unittest.TestCase):
    def test_real_profile_requires_increased_memory_not_extended_addressing(self):
        repo = Path(__file__).resolve().parents[2]
        integrated.guard(repo)
        self.assertEqual(integrated.PROFILE_MAX, 5 * 1024**3)

    def test_guard_rejects_wrong_memory_and_signing(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            profile = repo / "dm/games" / integrated.GAME
            sign = repo / "dm/ios/tools"
            profile.mkdir(parents=True)
            sign.mkdir(parents=True)
            (profile / "game.tsv").write_text("mem_reserve\t0x140000000\n")
            script = sign / "build_ipa.sh"
            script.write_text("com.apple.developer.kernel.increased-memory-limit\n")
            integrated.guard(repo)
            script.write_text("com.apple.developer.kernel.extended-virtual-addressing\n"
                              "com.apple.developer.kernel.increased-memory-limit\n")
            with self.assertRaises(ValueError):
                integrated.guard(repo)
            script.write_text("com.apple.developer.kernel.increased-memory-limit\n")
            (profile / "game.tsv").write_text("mem_reserve\t0x1028000000\n")
            with self.assertRaises(ValueError):
                integrated.guard(repo)

    def test_report_never_embeds_ipa_or_nsp(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            private_ipa = base / "MinecraftDungeonsII.ipa"
            private_ipa.write_bytes(b"not a real IPA")
            output = base / "report.md"
            results = [
                dict(name="mac-shaders-and-host", code=0, seconds=2, error=""),
                dict(name="headless-mac-boot", code=124, seconds=35,
                     error="", evidence={"reached_nnMain": True, "reported_frames": False}),
                dict(name="ios-ipa-build", code=0, seconds=3, error=""),
            ]
            integrated.make_report(results, output, private_ipa)
            data = output.read_text()
            self.assertIn("Built an unsigned IPA", data)
            self.assertIn("NOT uploaded", data)
            self.assertIn("Extended Virtual Addressing entitlement NOT required", data)
            self.assertNotIn("not a real IPA", data)
            self.assertIn("reported_frames: False", data)

    def test_process_stage_records_failures_without_uploading_logs(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            result = integrated.stage("synthetic-failure",
                                      [sys.executable, "-c",
                                       "print('[RED] simulated error'); raise SystemExit(2)"],
                                      5, os.environ.copy(), base / "private.log",
                                      base, base)
            self.assertEqual(result["code"], 2)
            self.assertIn("simulated error", str(result["error"]))


if __name__ == "__main__":
    unittest.main()
