#!/usr/bin/env python3
"""Live Drive status and rotating, verified backups of the Metal compile cache.

Only atomically published, content-keyed metallibs are saved. This is partial
shader-cache recovery, not a completed-stage stamp or a backup of game assets.
The builder still validates compiler/source keys before taking a cache hit.
"""
import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from mcd2_drive_api_download import drive_file_id

MAX_BYTES = 512 * 1024**2
MAX_FILES = 100000
LIB = re.compile(r"(?:ios|mac)/([0-9a-f]{2})/([0-9a-f]{64})\.metallib")
PHASES = {"Starting build", "Prepare extractor", "Extract merged base/update",
          "Rewrite executable modules", "Decode Unreal shaders", "Split and inventory shaders",
          "Translate and compile Metal shaders", "Compile shader pipelines",
          "Build Apple Silicon host", "Build/package iOS IPA", "Prepare game interface font",
          "Other build stage"}
NUMBERS = {"elapsed_s", "phase_s", "done", "total", "log_idle_s", "counter_idle_s", "cpu_percent"}


def utc():
    return datetime.now(timezone.utc).isoformat()


def digest(path, algorithm="sha256"):
    h = hashlib.new(algorithm, usedforsecurity=False)
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def lib_name(name):
    match = LIB.fullmatch(name)
    return bool(match and match[2].startswith(match[1]))


def pack_cache(root, target, identity):
    """Snapshot completed atomic files; no source game, key, log or temporary file."""
    root = Path(root)
    records, total = {}, 0
    with tarfile.open(target, "w") as archive:
        for path in sorted(root.rglob("*.metallib")):
            name = path.relative_to(root).as_posix()
            if not lib_name(name) or path.is_symlink() or root.resolve() not in path.resolve().parents:
                continue
            before = path.stat()
            if not 0 < before.st_size <= 256 * 1024**2:
                continue
            if len(records) >= MAX_FILES:
                break
            if total + before.st_size > MAX_BYTES:
                continue  # a bounded partial cache; omitted libraries compile normally
            data = path.read_bytes()
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                continue
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
            records[name] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            total += len(data)
        manifest = {"schema": 1, "kind": "partial-metal-cache", "identity": identity,
                    "limit_bytes": MAX_BYTES, "files": records}
        data = json.dumps(manifest, sort_keys=True).encode()
        entry = tarfile.TarInfo("manifest.json")
        entry.size = len(data)
        archive.addfile(entry, io.BytesIO(data))
    return len(records), total


def restore_cache(source, root, identity):
    """Validate all archive members and hashes before publishing any cache entry."""
    root = Path(root)
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root.parent) as tmp, tarfile.open(source) as archive:
        members = {}
        for entry in archive:
            if (len(members) >= MAX_FILES + 1 or entry.name in members or not entry.isfile()
                    or (entry.name != "manifest.json" and not lib_name(entry.name))):
                raise ValueError("Invalid shader cache archive member")
            members[entry.name] = entry
        item = members.pop("manifest.json", None)
        if item is None or item.size > 32 * 1024**2:
            raise ValueError("Missing or oversized cache manifest")
        stream = archive.extractfile(item)
        if stream is None:
            raise ValueError("Missing cache manifest data")
        manifest = json.load(stream)
        if (manifest.get("schema") != 1 or manifest.get("kind") != "partial-metal-cache"
                or manifest.get("identity") != identity):
            raise ValueError("Source, game input or toolchain identity changed")
        files = manifest.get("files", {})
        if not isinstance(files, dict) or set(files) != set(members):
            raise ValueError("Cache manifest does not match its payload")
        total = 0
        for name, entry in members.items():
            record = files[name]
            if not 0 < entry.size <= 256 * 1024**2 or record.get("size") != entry.size:
                raise ValueError("Invalid shader cache size")
            total += entry.size
            if total > MAX_BYTES:
                raise ValueError("Cache restore exceeds its bounded storage budget")
            path = Path(tmp) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.extractfile(entry)
            if stream is None:
                raise ValueError("Missing shader cache data")
            h = hashlib.sha256()
            with path.open("wb") as out:
                while block := stream.read(8 * 1024 * 1024):
                    h.update(block)
                    out.write(block)
            if h.hexdigest() != record.get("sha256"):
                raise ValueError("Shader cache content checksum mismatch")
        for name in files:
            dst = root / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(Path(tmp) / name, dst)
    return len(files)


class DriveStore:
    def __init__(self):
        from google.oauth2 import service_account  # type: ignore[reportMissingImports]
        from googleapiclient.discovery import build  # type: ignore[reportMissingImports]
        import google_auth_httplib2  # type: ignore[reportMissingImports]
        import httplib2  # type: ignore[reportMissingImports]
        info = json.loads(os.environ["MCD2_GDRIVE_SA_JSON"])
        cred = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/drive"])
        http = google_auth_httplib2.AuthorizedHttp(cred, http=httplib2.Http(timeout=20))
        self.drive = build("drive", "v3", http=http, cache_discovery=False)

    def check(self, ident, names):
        meta = self.drive.files().get(fileId=ident, supportsAllDrives=True,
                                     fields="name,trashed,capabilities/canEdit").execute(num_retries=2)
        if meta.get("name") not in names or meta.get("trashed") or not meta.get("capabilities", {}).get("canEdit"):
            raise ValueError("Cloud state file is not its dedicated editable placeholder")

    def get_json(self, ident):
        data = self.drive.files().get_media(fileId=ident).execute(num_retries=2)
        if len(data) > 1024 * 1024:
            raise ValueError("Cloud state JSON exceeds size limit")
        return json.loads(data)

    def put_json(self, ident, payload):
        from googleapiclient.http import MediaIoBaseUpload  # type: ignore[reportMissingImports]
        data = io.BytesIO(json.dumps(payload, sort_keys=True).encode())
        self.drive.files().update(fileId=ident, supportsAllDrives=True, body={"mimeType": "text/plain"},
                                  media_body=MediaIoBaseUpload(data, mimetype="text/plain"),
                                  fields="id").execute(num_retries=2)

    def upload(self, ident, path):
        from googleapiclient.http import MediaFileUpload  # type: ignore[reportMissingImports]
        size, md5 = Path(path).stat().st_size, digest(path, "md5")
        request = self.drive.files().update(fileId=ident, supportsAllDrives=True,
                    media_body=MediaFileUpload(str(path), mimetype="application/octet-stream",
                                              chunksize=32 * 1024**2, resumable=True), fields="id")
        result, deadline = None, time.monotonic() + 600
        while result is None:
            if time.monotonic() >= deadline:
                raise TimeoutError("Bounded shader-cache upload expired")
            _, result = request.next_chunk(num_retries=2)
        meta = self.drive.files().get(fileId=ident, fields="size,md5Checksum").execute(num_retries=2)
        if int(meta.get("size", -1)) != size or meta.get("md5Checksum") != md5:
            raise ValueError("Stored shader cache size/checksum mismatch")
        return {"size": size, "md5": md5}

    def download(self, ident, target, record):
        from googleapiclient.http import MediaIoBaseDownload  # type: ignore[reportMissingImports]
        meta = self.drive.files().get(fileId=ident, fields="size,md5Checksum").execute(num_retries=2)
        if (int(meta.get("size", -1)) != record["size"] or meta.get("md5Checksum") != record["md5"]
                or not 0 < record["size"] <= MAX_BYTES + 128 * 1024**2):
            raise ValueError("Stored checkpoint differs from the committed checkpoint index")
        with Path(target).open("wb") as stream:
            transfer = MediaIoBaseDownload(stream, self.drive.files().get_media(fileId=ident),
                                          chunksize=32 * 1024**2)
            done, deadline = False, time.monotonic() + 600
            while not done:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Bounded shader-cache download expired")
                _, done = transfer.next_chunk(num_retries=2)
        if Path(target).stat().st_size != record["size"] or digest(target, "md5") != record["md5"]:
            raise ValueError("Downloaded shader cache failed its checksum")


def environment_identity(repo, folder):
    def command(args):
        return subprocess.check_output(args, cwd=repo, timeout=30, stderr=subprocess.DEVNULL).decode()
    inputs = json.loads((Path(folder) / "mcd2-input-identity.json").read_text())
    if len(inputs) != 2 or any(not re.fullmatch(r"[0-9a-f]{32}", f.get("md5", "")) for f in inputs):
        raise ValueError("Verified input identity is missing")
    tools = command(["xcodebuild", "-version"])
    for sdk in ("macosx", "iphoneos"):
        for args in (("metal", "--version"), ("metallib", "--version"), ("--show-sdk-version",), ("--show-sdk-build-version",)):
            tools += command(["xcrun", "-sdk", sdk, *args])
    tools = re.sub(r"(?m)^InstalledDir:.*\n?", "", tools)
    return {"source": command(["git", "rev-parse", "HEAD"]).strip(), "inputs": inputs,
            "toolchain": hashlib.sha256(tools.encode()).hexdigest(),
            "chain": digest(Path(repo) / "dm/gfx/nvn/dmsh_chain.h")}


class CloudSession:
    """Independent status and cache workers; neither blocks the build watcher."""
    def __init__(self, folder, identity, ids, factory: Callable[[], Any] = DriveStore,
                 interval: float = 20, cache_interval: float = 600):
        self.folder, self.identity, self.ids, self.factory = Path(folder), identity, ids, factory
        self.root = self.folder / "0100A7C01B792000_build/shader/cache/cas/metallib"
        self.interval, self.cache_interval = interval, cache_interval
        self.lock, self.stop, self.capture = threading.Lock(), threading.Event(), threading.Event()
        self.finished = False
        run_id = os.environ.get("GITHUB_RUN_ID", "local")
        self.state = {"schema": 1, "run_id": run_id, "state": "starting", "sampled_at": utc(),
                      "phase": "Starting build", "cache_state": "not_saved", "restored_files": 0}
        self.last = None
        self.status_thread = threading.Thread(target=self._publish, daemon=True)
        self.cache_thread = threading.Thread(target=self._backup, daemon=True)

    def observe(self, phase, **metrics):
        with self.lock:
            self.state.update(phase=phase if phase in PHASES else "Other build stage",
                              sampled_at=utc(), state="building")
            for key in NUMBERS:
                value = metrics.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                    self.state[key] = value
                else:
                    self.state.pop(key, None)

    def note(self, **values):
        with self.lock:
            self.state.update(values)

    def snapshot(self):
        with self.lock:
            return dict(self.state, published_at=utc())

    def start(self):
        store = self.factory()
        for key, names in (("status", {"MCD2-live-status.json"}), ("index", {"MCD2-cache-index.json"}),
                           ("a", {"MCD2-cache-A.tar.pending"}), ("b", {"MCD2-cache-B.tar.pending"})):
            store.check(self.ids[key], names)
        store.put_json(self.ids["status"], self.snapshot())
        # Index is separate from live status and changes only after verified uploads.
        try:
            index = store.get_json(self.ids["index"])
            row = index.get("latest") if index.get("schema") == 1 else None
            if row and row.get("identity") == self.identity and row.get("slot") in ("a", "b"):
                with tempfile.TemporaryDirectory(dir=self.folder) as td:
                    archive = Path(td) / "cache.tar"
                    store.download(self.ids[row["slot"]], archive, row)
                    count = restore_cache(archive, self.root, self.identity)
                self.last = row
                self.note(restored_files=count, cache_state="restored")
            elif row:
                self.note(cache_state="incompatible_cache_skipped")
        except Exception:
            self.note(cache_state="restore_failed_building_fresh")
        self.status_thread.start()
        self.cache_thread.start()

    def _publish(self):
        store = self.factory()
        while not self.stop.is_set():
            try:
                store.put_json(self.ids["status"], self.snapshot())
            except Exception:
                print("[CLOUD] Status publication failed; local watchdog remains active", flush=True)
            self.stop.wait(self.interval)

    def _backup(self):
        store = self.factory()
        while True:
            self.capture.wait(self.cache_interval)
            self.capture.clear()
            try:
                with tempfile.TemporaryDirectory(dir=self.folder) as td:
                    archive = Path(td) / "cache.tar"
                    self.note(cache_state="snapshotting")
                    count, size = pack_cache(self.root, archive, self.identity)
                    if count:
                        # Re-read after any prior network error: an index write
                        # may have reached Drive even if its response was lost.
                        index = store.get_json(self.ids["index"])
                        if index.get("schema") != 1:
                            raise ValueError("Unknown checkpoint index schema")
                        previous = index.get("latest")
                        slot = "b" if previous and previous.get("slot") == "a" else "a"
                        self.note(cache_state="uploading", cache_files=count, cache_bytes=size)
                        record = store.upload(self.ids[slot], archive)
                        row = dict(record, slot=slot, identity=self.identity, files=count, saved_at=utc())
                        store.put_json(self.ids["index"], {"schema": 1, "latest": row})
                        # Read the committed index back before calling the generation saved.
                        if store.get_json(self.ids["index"]).get("latest") != row:
                            raise ValueError("Checkpoint index readback mismatch")
                        self.last = row
                        self.note(cache_state="saved", cache_files=count, cache_saved_at=row["saved_at"])
                    else:
                        self.note(cache_state="no_compiled_cache_yet")
            except Exception:
                self.note(cache_state="backup_failed_previous_generation_retained")
            if self.finished:
                return

    def close(self, code):
        self.finished = True
        self.capture.set()
        self.cache_thread.join(timeout=60)
        self.note(state="packaged" if code == 0 else "failed", exit_code=code, sampled_at=utc(),
                  cache_worker_active=self.cache_thread.is_alive())
        self.stop.set()
        self.status_thread.join(timeout=45)
        try:
            self.factory().put_json(self.ids["status"], self.snapshot())
        except Exception:
            print("[CLOUD] Final status publication failed", flush=True)


def configured_ids():
    return {key: drive_file_id(os.environ[name]) for key, name in
            (("status", "MCD2_STATUS_FILE"), ("index", "MCD2_CACHE_INDEX"),
             ("a", "MCD2_CACHE_A"), ("b", "MCD2_CACHE_B"))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--begin", action="store_true")
    mode.add_argument("--final-state", choices=("success", "failure", "cancelled"))
    args = parser.parse_args()
    try:
        ids = configured_ids()
        store = DriveStore()
        if args.begin:
            for key, name in (("status", "MCD2-live-status.json"), ("index", "MCD2-cache-index.json"),
                              ("a", "MCD2-cache-A.tar.pending"), ("b", "MCD2-cache-B.tar.pending")):
                store.check(ids[key], {name})
            status = {"schema": 1, "run_id": os.environ.get("GITHUB_RUN_ID"),
                      "state": "preparing_inputs", "published_at": utc(), "sampled_at": utc()}
        else:
            status = store.get_json(ids["status"])
            if status.get("run_id") != os.environ.get("GITHUB_RUN_ID"):
                raise ValueError("Refusing to finalize another run's status")
            status.update(state="delivered" if args.final_state == "success" else args.final_state,
                          published_at=utc(), sampled_at=utc())
        store.put_json(ids["status"], status)
    except Exception:
        print("[CLOUD] Status setup/finalization failed; inspect the last available status timestamp", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
