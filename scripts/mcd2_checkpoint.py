#!/usr/bin/env python3
"""Local, *verified* MCD2 checkpoints. No game files leave the runner here.

A completed progress event is not a resumable checkpoint. Validate the
underlying, fingerprint-stamped build output first. The NSP builder itself
re-checks its input fingerprints before reusing these outputs.
"""
import re
from pathlib import Path

GAME_VERSION = "v65536"
MODULES = ("main", "sdk", "subsdk0")


def valid_key(path):
    try:
        first = path.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError, UnicodeError):
        return False
    return bool(re.fullmatch(r"[0-9a-f]{64}", first))


def nonempty(path):
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def checkpoint(work, raw_name):
    """Return a safe checkpoint name ONLY if actual stage output is verified.

    Unknown/unverifiable steps return None; never use these markers to
    force-skip work. A valid marker is not proof that inputs are unchanged:
    build_ipa_from_nsp.py still checks its normal fingerprints on resume.
    """
    work = Path(work)
    name = raw_name.strip()
    if re.fullmatch(r"extract (?:v\d+)", name):
        version = name.split()[-1]
        folder = work / "extract" / version
        if (nonempty(folder / "ExeFS/main")
                and nonempty(folder / "RomFS.img")):
            return "extracted-game"
    if name == "rewrite":
        patched = work / f"patched_{GAME_VERSION}"
        if (valid_key(work / f"patched_{GAME_VERSION}.key")
                and all(nonempty(patched / f"{module}.patched.bin") for module in MODULES)):
            return "rewritten-modules"
    if name == "shader split":
        pkg = work / "shader/pkg"
        if (valid_key(work / "shader/pkg.key")
                and pkg.is_dir() and any(nonempty(p) for p in pkg.rglob("*"))):
            return "shader-package"
    if name in ("Mac shaders", "iOS shaders"):
        sh = work / "shader"
        if name == "Mac shaders":
            markers = sorted(sh.glob("mac-b-*.key"))
            roots = sh / "cache"
            label = "mac-metal-libraries"
        else:
            markers = sorted((sh / "ios").glob("b-*.key"))
            roots = sh / "ios"
            label = "ios-metal-libraries"
        for marker in markers:
            chain = marker.name.removeprefix("mac-").removesuffix(".key")
            libdir = roots / chain
            # dmsh_build keeps its reflection/build summary in cache even
            # for an iOS-only target. The iOS directory carries inputs.tsv.
            summary = sh / 'cache' / chain / '_b_build.json'
            if (valid_key(marker) and nonempty(summary)
                    and (name == 'Mac shaders' or nonempty(libdir / 'inputs.tsv'))
                    and any(nonempty(p) for p in libdir.glob("*_b.metallib"))):
                return label
    if name == "Mac host":
        if (nonempty(work / "tools/dmhost")
                and valid_key(work / "tools/dmhost.key")
                and nonempty(work / "font/nintendo_ext.ttf")
                and valid_key(work / "font/nintendo_ext.key")):
            return "mac-host"
    return None
