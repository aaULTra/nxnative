"""Synthetic tests for text-only MCD2 diagnostics, no NSPs or secrets."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mcd2_deep_diagnostics as diag  # noqa: E402


class TestMcd2Diagnostics(unittest.TestCase):
    def test_manifest_and_pairs(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = Path(td) / "romfs_manifest.tsv"
            manifest.write_text(
                "# path\tbytes\tfirst 32 bytes (hex)\n"
                "/Dungeons/Content/Paks/A-Switch.ucas\t4000\t000102\n"
                "/Dungeons/Content/Paks/A-Switch.utoc\t512\t000102\n"
                "/Dungeons/Content/Paks/global.ucas\t100\t000102\n"
                "/Dungeons/Content/Paks/global.utoc\t128\t000102\n",
                encoding="utf-8",
            )
            rows = diag.manifest_rows(manifest)
            self.assertEqual(len(rows), 4)
            pairs = diag.io_container_pairs(rows)
            self.assertEqual(pairs[0], (
                "/Dungeons/Content/Paks/A-Switch.ucas", 4000,
                "/Dungeons/Content/Paks/A-Switch.utoc",
            ))
            self.assertEqual(len(pairs), 2)

    def test_62_gib_one_encoding_only(self):
        # Pikmin 4's observed ORR x8,xzr,#0xf80000000 encoding.
        code = struct.pack("<III", 0xD503201F, 0xB26113E8, 0xD503201F)
        self.assertEqual(diag.possible_62g_immediates(code), [4])
        self.assertEqual(diag.possible_62g_immediates(b"no candidate"), [])

    def test_near_direct_calls_sign_extend_and_sort_by_distance(self):
        # AArch64 BL #+8 at byte offset 4; BL #-8 at byte offset 16.
        code = struct.pack("<IIIIII",
                           0xD503201F, 0x94000002, 0xD503201F,
                           0xD503201F, 0x97FFFFFE, 0xD503201F)
        self.assertEqual(diag.near_direct_calls(code, 12, radius=20),
                         [(16, 8), (4, 12)])
        self.assertEqual(diag.near_direct_calls(code, 12, radius=20, limit=1),
                         [(16, 8)])

    def test_sdk_node_area_signature_pair(self):
        # Synthetic reproduction of the 0x6c-byte distance in SDK 20.5.17.
        code = bytearray(0x150)
        struct.pack_into("<I", code, 0x40, diag.SDK_NODE_LIMIT_MOV)
        struct.pack_into("<I", code, 0xAC, diag.SDK_NODE_OCCUPIED_MOV)
        pairs, a, b = diag.sdk_node_area_sites(code)
        self.assertEqual((a, b), (1, 1))
        self.assertEqual(pairs, [(0x40, 0xAC, 0x6C)])

    def test_sdk_node_area_wrong_order_or_gap_rejected(self):
        code = bytearray(0x300)
        struct.pack_into("<I", code, 0x80, diag.SDK_NODE_OCCUPIED_MOV)
        struct.pack_into("<I", code, 0x120, diag.SDK_NODE_LIMIT_MOV)
        struct.pack_into("<I", code, 0x2C0, diag.SDK_NODE_OCCUPIED_MOV)
        pairs, a, b = diag.sdk_node_area_sites(code)
        self.assertEqual((a, b), (1, 2))
        self.assertEqual(pairs, [])

    def test_unreal_version_string(self):
        image = b"SDK MW+EpicGames+UnrealEngine-4_27_2-Release\x00"
        self.assertEqual(diag.unreal_versions(image), ["4_27_2"])

    def test_manifest_rejects_malformed_row(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "manifest.tsv"
            path.write_text("not tab separated\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                diag.manifest_rows(path)


if __name__ == "__main__":
    unittest.main()
