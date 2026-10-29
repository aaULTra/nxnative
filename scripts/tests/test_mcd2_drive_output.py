"""Synthetic IPA validation and dedicated-output checks; no game data or keys."""
import hashlib
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import Mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mcd2_drive_output as output  # noqa: E402


class TestDriveOutput(unittest.TestCase):
    def test_delivery_updates_existing_file_and_checks_independent_readback(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "test.ipa"
            self.make_ipa(path)
            size, digest = output.validate_ipa(path)
            drive = Mock()
            files = drive.files.return_value
            files.get.return_value.execute.side_effect = [
                {"name": output.PENDING_NAME, "capabilities": {"canEdit": True}},
                {"size": str(size), "md5Checksum": digest},
            ]
            # Upload response alone is deliberately insufficient for verification.
            files.update.return_value.next_chunk.side_effect = [(None, {"id": "destination"})]
            media = Mock()
            output.deliver(drive, "destination", path, media)
            self.assertEqual(files.update.call_args.kwargs["fileId"], "destination")
            self.assertEqual(files.get.call_count, 2)
            files.create.assert_not_called()
            self.assertTrue(media.call_args.kwargs["resumable"])

    def test_access_preflight_does_not_overwrite_previous_ipa(self):
        drive = Mock()
        files = drive.files.return_value
        files.get.return_value.execute.return_value = {
            "name": output.OUTPUT_NAME, "capabilities": {"canEdit": True}}
        output.deliver(drive, "destination", None, Mock())
        files.update.assert_not_called()

    def test_only_dedicated_editable_binary_destination_is_accepted(self):
        meta = {"name": output.PENDING_NAME, "mimeType": "application/octet-stream",
                "capabilities": {"canEdit": True}}
        output.validate_destination(meta)
        for changed in ({"name": "prod.keys"}, {"name": "game.nsp"},
                        {"trashed": True}, {"capabilities": {"canEdit": False}},
                        {"mimeType": "application/vnd.google-apps.document"}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                output.validate_destination(dict(meta, **changed))

    def make_ipa(self, path, omit="", extra=None):
        files = {
            "Payload/DmIOS.app/Info.plist": plistlib.dumps({"CFBundleExecutable": "DmIOS"}),
            "Payload/DmIOS.app/DmIOS": b"synthetic executable",
            "Payload/DmIOS.app/romfs.img": b"synthetic resources",
            "Payload/DmIOS.app/msl/test.metallib": b"synthetic shader",
        }
        if extra:
            files.update(extra)
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in files.items():
                if name != omit:
                    archive.writestr(name, data)

    def test_complete_archive_and_transfer_checksum(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "test.ipa"
            self.make_ipa(path)
            size, digest = output.validate_ipa(path)
            self.assertEqual(size, path.stat().st_size)
            self.assertEqual(digest, hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest())
            output.verify_uploaded({"size": str(size), "md5Checksum": digest}, size, digest)
            with self.assertRaises(ValueError):
                output.verify_uploaded({"size": str(size), "md5Checksum": "0" * 32}, size, digest)

    def test_missing_payload_parts_and_unsafe_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "test.ipa"
            for missing in ("Info.plist", "DmIOS", "romfs.img", "msl/test.metallib"):
                with self.subTest(missing=missing):
                    self.make_ipa(path, omit="Payload/DmIOS.app/" + missing)
                    with self.assertRaises((ValueError, KeyError)):
                        output.validate_ipa(path)
            self.make_ipa(path, extra={"../escape": b"invalid"})
            with self.assertRaisesRegex(ValueError, "unsafe"):
                output.validate_ipa(path)

    def test_crc_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "test.ipa"
            self.make_ipa(path)
            data = path.read_bytes().replace(b"synthetic resources", b"corrupted resources")
            path.write_bytes(data)
            with self.assertRaises((ValueError, zipfile.BadZipFile)):
                output.validate_ipa(path)


if __name__ == "__main__":
    unittest.main()
