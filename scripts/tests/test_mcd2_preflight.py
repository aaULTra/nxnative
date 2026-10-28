"""Synthetic package tests: no game files or prod.keys are needed."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mcd2_preflight import PairingError, inspect_folder

BASE = '0100A7C01B792000'
UPDATE = '0100A7C01B792800'


def synthetic_nsp(folder, name):
    path = folder / name
    # A zero-entry PFS0 header and invented title IDs exercise nxnative's
    # actual keyless package scanner. Not a decryptable NSP.
    path.write_bytes(struct.pack('<4sIII', b'PFS0', 0, 0, 0))
    return path


class TestMcd2Preflight(unittest.TestCase):
    def test_pairs_base_and_newest_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            synthetic_nsp(folder, f'MCD2 [{BASE}][v0].nsp')
            synthetic_nsp(folder, f'MCD2 [{UPDATE}][v65536].nsp')
            synthetic_nsp(folder, f'MCD2 [{UPDATE}][v131072].nsp')
            title, base, version, update, family = inspect_folder(folder)
            self.assertEqual(title, int(BASE, 16))
            self.assertIn('v0', base.name)
            self.assertEqual(version, 'v131072')
            self.assertIn('[v131072]', update.name)
            self.assertEqual(len(family['updates']), 2)

    def test_unexpected_game_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            synthetic_nsp(folder, 'Other [0100ABCDEF120000][v0].nsp')
            synthetic_nsp(folder, 'Other [0100ABCDEF120800][v65536].nsp')
            with self.assertRaisesRegex(PairingError, 'Unexpected base title ID'):
                inspect_folder(folder)

    def test_base_without_update_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            synthetic_nsp(folder, f'MCD2 [{BASE}][v0].nsp')
            with self.assertRaisesRegex(PairingError, 'No update'):
                inspect_folder(folder)

    def test_update_without_version_tag_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            synthetic_nsp(folder, f'MCD2 [{BASE}][v0].nsp')
            synthetic_nsp(folder, f'MCD2 [{UPDATE}].nsp')
            with self.assertRaisesRegex(PairingError, 'not selectable'):
                inspect_folder(folder)

    def test_update_for_another_game_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            synthetic_nsp(folder, f'MCD2 [{BASE}][v0].nsp')
            synthetic_nsp(folder, 'Wrong game [0100DEAD12340800][v65536].nsp')
            with self.assertRaisesRegex(PairingError, 'more than one game'):
                inspect_folder(folder)


if __name__ == '__main__':
    unittest.main()
