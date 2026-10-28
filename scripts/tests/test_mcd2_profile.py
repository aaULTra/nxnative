"""Evidence and sanity checks for the experimental Minecraft Dungeons II profile.

Runs without NSPs or console keys. A passing result is NOT a gameplay test.
"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dm" / "games"))
import game  # noqa: E402

GAME_ID = "0100A7C01B792000"
EXPECTED_MODULES = {
    "main": "9e9d887d59f7f7db176b35e3379258b9",
    "sdk": "190030518b5d64e5d87b84bc54661af9",
    "subsdk0": "3904a8dee6801472d56bd4921378df27",
}


class TestMcd2GameProfile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = game.load(GAME_ID)

    def test_identifiers_and_update_version(self):
        g = self.profile
        self.assertEqual(g.title_id, int(GAME_ID, 16))
        self.assertEqual(g.get("program_id"), GAME_ID)
        self.assertEqual(g.get("package"), "v65536")
        self.assertEqual(g.get("shader_versions"), "v65536")
        self.assertEqual(set(g.builds()), {"v65536"})
        build = g.builds()["v65536"]
        self.assertEqual(build["display"], "1.1.1.0")
        for module, build_id in EXPECTED_MODULES.items():
            with self.subTest(module=module):
                self.assertEqual(build[module], build_id)

    def test_module_order_and_assets(self):
        g = self.profile
        self.assertEqual(g.words("modules"), ["main", "subsdk0", "sdk"])
        # This directory was actually seen in the survey; an exact file marker
        # remains a later checkpoint. Do not invent one here.
        self.assertEqual(g.get("romfs_marker"), "Dungeons/Content")
        self.assertIn(".ucas", g.get("zip_store"))
        self.assertIn(".utoc", g.get("zip_store"))

    def test_host_memory_reservation_is_bounded(self):
        g = self.profile
        reserve = g.int("mem_reserve")
        alias = g.int("mem_alias")
        stack = g.int("mem_stack")
        front = 0x10000000  # nxnative's 256 MiB module/front region
        self.assertEqual(reserve, 5 * 1024**3)
        self.assertEqual(alias, 0x118000000)
        self.assertEqual(stack, front)
        self.assertEqual(reserve - front - alias - stack, 0x8000000)
        self.assertEqual(g.int("sys_resource"), 20 * 1024**2)
        self.assertEqual(g.int("app_pool"), 0xCD500000)

    def test_no_unverified_fps_override(self):
        g = self.profile
        self.assertEqual(g.get("fps_target", "0"), "0")
        self.assertEqual(g.get("pad_layout"), "full")
        self.assertIn("SwitchVirtualAddressSpaceSize=4064", g.get("game_args"))

    def test_ipa_signing_requests_increased_memory_only(self):
        # This title must work without the extended-virtual-addressing
        # entitlement. Its default IPA build must carry Increased Memory
        # Limit only. Installation signing must still grant that request.
        build = (ROOT / "dm" / "ios" / "tools" / "build_ipa.sh").read_text()
        self.assertIn("com.apple.developer.kernel.increased-memory-limit", build)
        self.assertNotIn("com.apple.developer.kernel.extended-virtual-addressing", build)
        self.assertLessEqual(self.profile.int("mem_reserve"), 0x140000000)

    def test_launcher_reports_actual_headroom(self):
        # Never infer physical-memory availability from virtual reservations.
        launcher = (ROOT / "dm" / "ios" / "Sources" / "Launcher.swift").read_text()
        self.assertIn("os_proc_available_memory()", launcher)

    def test_host_profile_header_renders(self):
        header = game.header(self.profile)
        self.assertIn("DM_GAME_PROGRAM_ID 0x0100A7C01B792000ull", header)
        self.assertIn("DM_GAME_SYS_RESOURCE 0x1400000ull", header)
        self.assertIn("DM_GAME_MODULES", header)
        self.assertIn('Minecraft Dungeons II', header)


if __name__ == "__main__":
    unittest.main()
