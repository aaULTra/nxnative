"""Synthetic checks for authenticated large-file Drive API inputs."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mcd2_drive_api_download as auth  # noqa: E402


class TestAuthenticatedDriveDownload(unittest.TestCase):
    def test_existing_share_links_and_download_urls(self):
        ident = "1fj-nuKclpFZrJC2rmL0oVYLvqCfQAfXD"
        self.assertEqual(auth.drive_file_id(f"https://drive.google.com/file/d/{ident}/view?usp=drivesdk"), ident)
        self.assertEqual(auth.drive_file_id(f"https://drive.google.com/uc?export=download&id={ident}"), ident)
        self.assertEqual(auth.drive_file_id(f"https://drive.google.com/open?id={ident}"), ident)
        self.assertEqual(auth.drive_file_id(ident), ident)

    def test_reject_non_google_hosts_and_missing_id(self):
        for url in ("https://evil.example.com/file/d/12345678901234567890/view",
                    "http://drive.google.com/file/d/12345678901234567890/view",
                    "https://drive.google.com/uc?export=download", "bad-link"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                auth.drive_file_id(url)

    def test_sizes_are_original_private_file_sizes(self):
        self.assertEqual(auth.BASE_BYTES, 1662600274)
        self.assertEqual(auth.UPDATE_BYTES, 2943659595)

    def test_workflow_downloads_authenticated_and_only_manually(self):
        repo = Path(__file__).resolve().parents[2]
        workflow = (repo / ".github/workflows/mcd2-observed.yml").read_text()
        self.assertIn("MCD2_GDRIVE_SA_JSON: ${{ secrets.MCD2_GDRIVE_SA_JSON }}", workflow)
        self.assertIn("python -u scripts/mcd2_drive_api_download.py", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("  push:\n", workflow)
        self.assertNotIn("gdown --quiet", workflow)


if __name__ == "__main__":
    unittest.main()
