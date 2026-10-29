"""Private cloud visibility and partial-cache recovery with synthetic bytes."""
import copy
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mcd2_cloud as cloud  # noqa: E402

NAME = "ios/ab/" + "ab" * 32 + ".metallib"
IDENTITY = {"source": "test-source", "inputs": ["test-base", "test-update"], "toolchain": "test-compiler"}
IDS = {"status": "status", "index": "index", "a": "a", "b": "b"}


class MemoryStore:
    def __init__(self):
        self.data = {"index": {"schema": 1, "latest": None}}
        self.blobs = {}
        self.lose_index_response = False
        self.fail_upload = False

    def check(self, ident, names):
        pass

    def get_json(self, ident):
        return copy.deepcopy(self.data[ident])

    def put_json(self, ident, value):
        self.data[ident] = copy.deepcopy(value)
        if ident == "index" and self.lose_index_response:
            self.lose_index_response = False
            raise ConnectionError("synthetic lost response after committed index")

    def upload(self, ident, path):
        if self.fail_upload:
            raise ConnectionError("synthetic interrupted upload")
        self.blobs[ident] = Path(path).read_bytes()
        return {"size": Path(path).stat().st_size, "md5": cloud.digest(path, "md5")}

    def download(self, ident, target, record):
        Path(target).write_bytes(self.blobs[ident])


class TestCloudCache(unittest.TestCase):
    def seed(self, root, name=NAME):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic completed Metal compile")
        return path

    def test_only_atomic_content_keyed_libraries_are_saved_and_restored(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "cache"
            self.seed(root)
            (root / "prod.keys").write_bytes(b"private-not-a-library")
            (root / (NAME + ".tmp-worker")).write_bytes(b"unfinished")
            archive = Path(td) / "cache.tar"
            count, _ = cloud.pack_cache(root, archive, IDENTITY)
            self.assertEqual(count, 1)
            restored = Path(td) / "restored"
            self.assertEqual(cloud.restore_cache(archive, restored, IDENTITY), 1)
            self.assertEqual((root / NAME).read_bytes(), (restored / NAME).read_bytes())
            self.assertFalse((restored / "prod.keys").exists())
            self.assertEqual(list(restored.rglob("*.key")), [])

    def test_source_or_toolchain_mismatch_restores_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root, archive, restored = Path(td) / "cache", Path(td) / "cache.tar", Path(td) / "restored"
            self.seed(root)
            cloud.pack_cache(root, archive, IDENTITY)
            with self.assertRaisesRegex(ValueError, "identity"):
                cloud.restore_cache(archive, restored, dict(IDENTITY, toolchain="changed"))
            self.assertFalse((restored / NAME).exists())

    def test_storage_budget_saves_a_subset_without_claiming_a_full_stage(self):
        with tempfile.TemporaryDirectory() as td:
            root, archive = Path(td) / "cache", Path(td) / "cache.tar"
            self.seed(root)
            self.seed(root, "ios/cd/" + "cd" * 32 + ".metallib")
            with patch.object(cloud, "MAX_BYTES", 40):
                count, total = cloud.pack_cache(root, archive, IDENTITY)
                self.assertEqual(count, 1)
                self.assertLessEqual(total, 40)
                self.assertEqual(cloud.restore_cache(archive, Path(td) / "restored", IDENTITY), 1)

    def test_corrupt_content_is_rejected_before_publishing_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root, archive, restored = Path(td) / "cache", Path(td) / "cache.tar", Path(td) / "restored"
            self.seed(root)
            cloud.pack_cache(root, archive, IDENTITY)
            archive.write_bytes(archive.read_bytes().replace(b"synthetic completed", b"corrupted completed"))
            with self.assertRaisesRegex(ValueError, "checksum"):
                cloud.restore_cache(archive, restored, IDENTITY)
            self.assertFalse((restored / NAME).exists())

    def test_paths_links_and_duplicate_members_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            archive, restored = Path(td) / "bad.tar", Path(td) / "restored"
            for name, kind in (("../escape", tarfile.REGTYPE), (NAME, tarfile.SYMTYPE)):
                with self.subTest(name=name), tarfile.open(archive, "w") as out:
                    entry = tarfile.TarInfo(name)
                    entry.type = kind
                    entry.linkname = "../escape"
                    out.addfile(entry, io.BytesIO(b""))
                with self.assertRaises(ValueError):
                    cloud.restore_cache(archive, restored, IDENTITY)
            with tarfile.open(archive, "w") as out:
                for _ in range(2):
                    out.addfile(tarfile.TarInfo(NAME), io.BytesIO(b""))
            with self.assertRaises(ValueError):
                cloud.restore_cache(archive, restored, IDENTITY)

    def test_failed_upload_and_lost_index_response_preserve_previous_generation(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore()
            session = cloud.CloudSession(td, IDENTITY, IDS, factory=lambda: store, cache_interval=0)
            session.finished = True
            self.seed(session.root)
            store.lose_index_response = True
            session._backup()
            self.assertEqual(store.data["index"]["latest"]["slot"], "a")
            original_a = store.blobs["a"]
            session._backup()
            self.assertEqual(store.data["index"]["latest"]["slot"], "b")
            self.assertEqual(store.blobs["a"], original_a)
            previous = copy.deepcopy(store.data["index"])
            store.fail_upload = True
            session._backup()
            self.assertEqual(store.data["index"], previous)

    def test_fresh_session_restores_saved_cache_without_stage_completion_claim(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as fresh:
            store = MemoryStore()
            session = cloud.CloudSession(td, IDENTITY, IDS, factory=lambda: store, cache_interval=0)
            self.seed(session.root)
            session.finished = True
            session._backup()
            resumed = cloud.CloudSession(fresh, IDENTITY, IDS, factory=lambda: store,
                                         interval=0.02, cache_interval=100)
            resumed.start()
            try:
                self.assertEqual(resumed.snapshot()["restored_files"], 1)
                self.assertTrue((resumed.root / NAME).is_file())
                self.assertNotIn("completed_stage", resumed.snapshot())
            finally:
                resumed.close(1)

    def test_status_is_fresh_whitelisted_and_readable_while_process_runs(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore()
            session = cloud.CloudSession(td, IDENTITY, IDS, factory=lambda: store,
                                         interval=0.02, cache_interval=100)
            session.start()
            try:
                session.observe("private-file-or-content", cpu_percent=float("nan"), done=True, total=50)
                session.observe("Translate and compile Metal shaders", done=12, total=50, elapsed_s=31)
                deadline = time.monotonic() + 2
                while store.data.get("status", {}).get("done") != 12 and time.monotonic() < deadline:
                    time.sleep(0.02)
                status = store.data["status"]
                self.assertEqual(status["done"], 12)
                self.assertEqual(status["total"], 50)
                self.assertIn("sampled_at", status)
                self.assertIn("published_at", status)
                self.assertNotIn("private-file-or-content", json.dumps(status))
                self.assertNotIn("cpu_percent", status)
            finally:
                session.close(1)

    def test_backup_has_distinct_output_and_does_not_cancel_original(self):
        root = Path(__file__).resolve().parents[2]
        text = (root / ".github/workflows/mcd2-backup.yml").read_text()
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("mcd2-durable-backup", text)
        self.assertNotIn("1lu_Myzes9nPWvIR5Mb5Et7QJp7Kw1G6b", text)
        self.assertIn("MCD2_STATUS_FILE", text)
        self.assertIn("MCD2_CACHE_INDEX", text)


if __name__ == "__main__":
    unittest.main()
