#!/usr/bin/env python3
"""Download two privately shared MCD2 NSPs using Google's authenticated Drive API.

Only the two expected file IDs are read. Credentials come from an Actions
secret; no signed URLs, keys or game bytes are written into the repository.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlsplit

BASE_BYTES = 1_662_600_274
UPDATE_BYTES = 2_943_659_595
BASE_FILENAME = "Minecraft Dungeons 2 [0100A7C01B792000][v0].nsp"
UPDATE_FILENAME = "Minecraft Dungeons 2 [0100A7C01B792800][v65536].nsp"

def drive_file_id(link):
    """Accept the user's existing Google Drive share links or a plain file ID."""
    if re.fullmatch(r"[a-zA-Z0-9_-]{20,}", link):
        return link
    parsed = urlsplit(link)
    if parsed.scheme != "https" or parsed.hostname not in {"drive.google.com", "www.drive.google.com"}:
        raise ValueError("Expected a Google Drive HTTPS file link")
    match = re.fullmatch(r"/file/d/([a-zA-Z0-9_-]+)/?(?:view|edit|preview)?/?", parsed.path)
    if match:
        return match.group(1)
    if parsed.path in {"/uc", "/open"}:
        values = parse_qs(parsed.query).get("id", [])
        if len(values) == 1 and re.fullmatch(r"[a-zA-Z0-9_-]{20,}", values[0]):
            return values[0]
    raise ValueError("Unsupported Drive link; use a /file/d/FILE_ID/view link")


def run(folder, base_link, update_link, json_env):
    # Imports here allow URL validation and synthetic unit tests without API deps.
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload

    raw = os.environ.get(json_env, "")
    if not raw:
        raise ValueError("Missing the service account JSON repository secret")
    info = json.loads(raw)
    if info.get("type") != "service_account" or not info.get("private_key"):
        raise ValueError("Invalid Google service account JSON credential")
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive.readonly"])
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    files = [(base_link, BASE_FILENAME, BASE_BYTES), (update_link, UPDATE_FILENAME, UPDATE_BYTES)]
    for ix, (link, filename, size) in enumerate(files, 1):
        file_id = drive_file_id(link)
        # Fail early on broken sharing or the wrong file. No credentials,
        # private URLs or original filenames are printed.
        meta = drive.files().get(
            fileId=file_id, fields="size,mimeType,capabilities/canDownload").execute(num_retries=5)
        if int(meta.get("size", -1)) != size or meta.get("mimeType", "").startswith("application/vnd.google-apps"):
            raise ValueError(f"NSP {ix}: Drive metadata does not match the expected binary size/type")
        if meta.get("capabilities", {}).get("canDownload") is False:
            raise ValueError(f"NSP {ix}: service account cannot download the shared file")
        dest = folder / filename
        if dest.is_file() and dest.stat().st_size == size:
            print(f"[DRIVE API] NSP {ix}/2 already verified ({size:,} bytes)", flush=True)
            continue
        partial = dest.with_suffix(".partial")
        print(f"[DRIVE API] Downloading NSP {ix}/2 using private authorized API", flush=True)
        # Disk backed and chunked; never buffers a 1.6+ GB file into memory.
        request = drive.files().get_media(fileId=file_id)
        try:
            with partial.open("wb") as out:
                downloader = MediaIoBaseDownload(out, request, chunksize=32 * 1024 * 1024)
                done = False
                last_decile = -1
                while not done:
                    status, done = downloader.next_chunk(num_retries=5)
                    fraction = status.progress() if status is not None else 0.0
                    decile = int(fraction * 10)
                    if decile > last_decile:
                        last_decile = decile
                        print(f"[DRIVE API] NSP {ix}/2: {min(decile, 10) * 10}% received", flush=True)
            if partial.stat().st_size != size:
                raise ValueError(f"NSP {ix}: download size verification failed")
            partial.replace(dest)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        print(f"[DRIVE API] NSP {ix}/2 verified ({size:,} bytes)", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder")
    parser.add_argument("--base-link", required=True)
    parser.add_argument("--update-link", required=True)
    parser.add_argument("--credentials-env", default="MCD2_GDRIVE_SA_JSON")
    args = parser.parse_args()
    try:
        run(args.folder, args.base_link, args.update_link, args.credentials_env)
    except Exception as exc:
        # Avoid raw Google API exception strings: sometimes they include URLs,
        # request headers or sensitive provider details.
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status is not None:
            print(f"Drive API download failed (HTTP {status}). Check that both "
                  "files are shared as Viewer with the service account.", file=sys.stderr)
        elif isinstance(exc, ValueError):
            print(f"Drive API input error: {exc}", file=sys.stderr)
        else:
            print(f"Drive API failed ({type(exc).__name__}); see authenticated "
                  "access and Drive API quota settings.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
