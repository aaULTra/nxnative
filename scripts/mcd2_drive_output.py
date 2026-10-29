#!/usr/bin/env python3
"""Replace a dedicated, user-owned Drive output file with a verified IPA.

The owner creates the placeholder and grants the existing runner service
account editor access. Updating it preserves ownership and uses the owner's
storage; the service account never creates a file or changes permissions.
"""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import sys
import zipfile

from mcd2_drive_api_download import drive_file_id
from mcd2_drive_transport import drive_client, safe_failure

OUTPUT_NAME = "MinecraftDungeonsII.ipa"
PENDING_NAME = OUTPUT_NAME + ".pending"


def validate_destination(meta):
    if (meta.get("name") not in (OUTPUT_NAME, PENDING_NAME)
            or meta.get("trashed")
            or meta.get("mimeType", "").startswith("application/vnd.google-apps")
            or not meta.get("capabilities", {}).get("canEdit")):
        raise ValueError("Output must be the dedicated editable MCD2 binary placeholder")


def validate_ipa(path):
    """Check every ZIP CRC and the expected single iOS application payload."""
    path = Path(path)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("IPA contains duplicate archive entries")
        if any(n.startswith("/") or ".." in n.split("/") for n in names):
            raise ValueError("IPA contains unsafe archive paths")
        info_name = "Payload/DmIOS.app/Info.plist"
        apps = {n.split("/")[1] for n in names
                if n.startswith("Payload/") and len(n.split("/")) > 2}
        if apps != {"DmIOS.app"} or info_name not in names:
            raise ValueError("IPA must contain only the expected DmIOS app")
        info = plistlib.loads(archive.read(info_name))
        exe = info.get("CFBundleExecutable", "")
        if not isinstance(exe, str) or not exe or "/" in exe or exe in (".", ".."):
            raise ValueError("IPA executable metadata is invalid")
        prefix = "Payload/DmIOS.app/"
        if prefix + exe not in names or archive.getinfo(prefix + exe).file_size == 0:
            raise ValueError("IPA executable is missing or empty")
        if not any(n.startswith(prefix + "romfs/") or n == prefix + "romfs.img" for n in names):
            raise ValueError("Bundled IPA has no game resources")
        if not any(n.startswith(prefix + "msl/") and n.endswith(".metallib") for n in names):
            raise ValueError("IPA has no Metal shader libraries")
        if archive.testzip() is not None:
            raise ValueError("IPA ZIP integrity check failed")
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return path.stat().st_size, digest.hexdigest()


def verify_uploaded(meta, size, digest):
    if int(meta.get("size", -1)) != size or meta.get("md5Checksum") != digest:
        raise ValueError("Drive output size/checksum does not match the verified IPA")


def deliver(drive, ident, ipa, media_factory):
    """Update only the supplied, validated destination and verify stored bytes."""
    meta = drive.files().get(fileId=ident, supportsAllDrives=True,
                             fields="name,mimeType,trashed,capabilities/canEdit").execute(num_retries=5)
    validate_destination(meta)
    print("[DRIVE OUTPUT] Dedicated output file is editable", flush=True)
    if ipa is None:
        return
    print(json.dumps({'ev': 'stage', 'name': 'verify IPA archive'}), flush=True)
    size, digest = validate_ipa(ipa)
    print(f"[DRIVE OUTPUT] IPA archive verified ({size:,} bytes)", flush=True)
    request = drive.files().update(
        fileId=ident, supportsAllDrives=True, body={"name": OUTPUT_NAME},
        media_body=media_factory(str(ipa), mimetype="application/octet-stream",
                                chunksize=32 * 1024 * 1024, resumable=True),
        fields="id,size,md5Checksum")
    result = None
    print(json.dumps({'ev': 'stage', 'name': 'upload IPA'}), flush=True)
    while result is None:
        status, result = request.next_chunk(num_retries=5)
        if status is not None:
            print(f"[DRIVE OUTPUT] {int(status.progress() * 100)}% uploaded", flush=True)
            print(json.dumps({'ev': 'progress', 'name': 'upload IPA', 'done': int(status.progress() * size),
                              'total': size, 'unit': 'B'}), flush=True)
    # Read back stored metadata independently of the upload response.
    stored = drive.files().get(fileId=ident, supportsAllDrives=True,
                               fields="size,md5Checksum").execute(num_retries=5)
    verify_uploaded(stored, size, digest)
    print("[DRIVE OUTPUT] Stored IPA checksum + size verified; download from your output file", flush=True)


def run(output_link, credentials_env, ipa=None):
    from googleapiclient.http import MediaFileUpload  # type: ignore[reportMissingImports]

    ident = drive_file_id(output_link)
    if credentials_env != "MCD2_GDRIVE_SA_JSON":
        raise ValueError("Use the configured runner service account credential")
    drive = drive_client()
    deliver(drive, ident, ipa, MediaFileUpload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--credentials-env", default="MCD2_GDRIVE_SA_JSON")
    parser.add_argument("--ipa", type=Path, help="Omit to check access before a long build")
    args = parser.parse_args()
    try:
        run(args.output, args.credentials_env, args.ipa)
    except Exception as exc:
        # Google errors can contain request/session URLs. Keep public logs bounded.
        print("[RED] Drive output check/transfer failed; no verified delivery. "
              f"Diagnostic category: {safe_failure(exc)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
