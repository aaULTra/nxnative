"""Output-based checkpoints: a stage-end message alone is never enough."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mcd2_checkpoint as cp  # noqa: E402


class TestVerifiedCheckpoint(unittest.TestCase):
    def test_rewrite_requires_all_outputs_and_fingerprint_stamp(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            patched = work / "patched_v65536"
            patched.mkdir()
            (work / "patched_v65536.key").write_text("a" * 64 + "\n")
            for module in ("main", "sdk"):
                (patched / f"{module}.patched.bin").write_bytes(b"xyz")
            self.assertIsNone(cp.checkpoint(work, "rewrite"))
            (patched / "subsdk0.patched.bin").write_bytes(b"xyz")
            self.assertEqual(cp.checkpoint(work, "rewrite"), "rewritten-modules")
            (work / "patched_v65536.key").write_text("invalid\n")
            self.assertIsNone(cp.checkpoint(work, "rewrite"))

    def test_shader_stamps_and_libraries_are_required(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            sh = work / "shader"
            libdir = sh / "cache" / "b-abc"
            libdir.mkdir(parents=True)
            (sh / "mac-b-abc.key").write_text("a" * 64 + "\n")
            (libdir / "example_b.metallib").write_bytes(b"metal")
            self.assertIsNone(cp.checkpoint(work, "Mac shaders"))
            (libdir / "_b_build.json").write_text('{"complete":true}')
            self.assertEqual(cp.checkpoint(work, "Mac shaders"), "mac-metal-libraries")

    def test_mac_host_requires_its_own_fingerprint_not_just_font(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            (work / "tools").mkdir()
            (work / "font").mkdir()
            (work / "tools/dmhost").write_bytes(b"host")
            (work / "font/nintendo_ext.ttf").write_bytes(b"font")
            (work / "font/nintendo_ext.key").write_text("b" * 64 + "\n")
            self.assertIsNone(cp.checkpoint(work, "Mac host"))
            (work / "tools/dmhost.key").write_text("c" * 64 + "\n")
            self.assertEqual(cp.checkpoint(work, "Mac host"), "mac-host")

    def test_ios_summary_is_in_reflection_cache_and_inputs_stamp_is_required(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            lib = work / 'shader/ios/b-abc'; lib.mkdir(parents=True)
            summary = work / 'shader/cache/b-abc'; summary.mkdir(parents=True)
            (work / 'shader/ios/b-abc.key').write_text('a' * 64)
            (lib / 'example_b.metallib').write_bytes(b'metal')
            (summary / '_b_build.json').write_text('[{"key":"example"}]')
            self.assertIsNone(cp.checkpoint(work, 'iOS shaders'))
            (lib / 'inputs.tsv').write_text('game\t0100A7C01B792000\ninputs\tabc\n')
            self.assertEqual(cp.checkpoint(work, 'iOS shaders'), 'ios-metal-libraries')

    def test_unstamped_stage_and_unknown_name_not_reusable(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            self.assertIsNone(cp.checkpoint(work, "UE shader decode"))
            self.assertIsNone(cp.checkpoint(work, "shader split"))
            pkg = work / "shader/pkg"
            pkg.mkdir(parents=True)
            (pkg / "pkg.bin").write_bytes(b"contents")
            self.assertIsNone(cp.checkpoint(work, "shader split"))
            (work / "shader/pkg.key").write_text("c" * 64 + "\n")
            self.assertEqual(cp.checkpoint(work, "shader split"), "shader-package")


if __name__ == "__main__":
    unittest.main()
