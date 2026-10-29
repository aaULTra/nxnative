#!/usr/bin/env python3
"""Text-only diagnostics from an ephemeral Minecraft Dungeons II survey.

Does not upload decrypted modules, keys, shaders, or other game content.
"""
import argparse
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dm" / "tools"))
import nso  # noqa: E402


def manifest_rows(path):
    """Read nx_extract's text-only RomFS manifest."""
    rows = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) != 3:
            raise ValueError("invalid RomFS manifest row")
        name, size_str, _ = cols
        size = int(size_str)
        if size < 0:
            raise ValueError("negative RomFS file size")
        rows.append((name, size))
    return rows


def io_container_pairs(rows):
    """Find actual IoStore containers without guessing their filenames."""
    by_name = {name.casefold(): (name, size) for name, size in rows}
    result = []
    for name, size in rows:
        if name.lower().endswith(".ucas"):
            match = by_name.get((name[:-5] + ".utoc").casefold())
            result.append((name, size, match[0] if match else None))
    return sorted(result, key=lambda x: -x[1])


def unreal_versions(image):
    return sorted({v.decode("ascii") for v in
                   re.findall(rb"UnrealEngine-([45]_[0-9_]{2,12})", image)})


def possible_62g_immediates(code):
    """Possible ORR Xd, XZR, #0xf80000000 instructions.

    Only recognises one encoding, seen in an earlier Unreal nxnative port.
    No match does NOT mean this game does not reserve 62 GiB.
    """
    result = []
    for offset in range(0, len(code) - 3, 4):
        if struct.unpack_from("<I", code, offset)[0] & 0xFFFFFFE0 == 0xB26113E0:
            result.append(offset)
    return result



def near_direct_calls(code, site, radius=96, limit=8):
    """ARM64 BL targets near a 62 GiB immediate, no game code emitted.

    Offsets are relative to the loaded main NSO text segment. A nearby call
    does NOT establish that it allocates memory; disassembly and validation
    of the exact target function are still required before any patch.
    """
    calls = []
    start = max(0, site - radius) & ~3
    stop = min(len(code) - 3, site + radius)
    for off in range(start, stop, 4):
        word = struct.unpack_from("<I", code, off)[0]
        if word & 0xFC000000 != 0x94000000:
            continue
        imm = word & 0x03FFFFFF
        if imm & 0x02000000:
            imm -= 0x04000000
        calls.append((off, off + imm * 4))
    return sorted(calls, key=lambda call: (abs(call[0] - site), call[0]))[:limit]




def nearby_adrp_add_refs(code, site, radius=96, limit=24):
    """Infer PC-relative addresses, not function semantics or patch points.

    AArch64 ADRP Xn followed by ADD Xm,Xn,#imm (up to five instructions).
    The output only contains offsets, target address and registers; no
    guest code bytes. Pairing is exploratory, not validated data flow.
    """
    refs = []
    start = max(0, site - radius) & ~3
    stop = min(len(code) - 3, site + radius)
    for at in range(start, stop, 4):
        word = struct.unpack_from("<I", code, at)[0]
        if word & 0x9F000000 != 0x90000000:
            continue
        reg = word & 31
        imm = (((word >> 5) & 0x7FFFF) << 2) | ((word >> 29) & 3)
        if imm & (1 << 20):
            imm -= 1 << 21
        page = (at & ~0xFFF) + (imm << 12)
        for follow in range(at + 4, min(at + 24, len(code) - 3), 4):
            add = struct.unpack_from("<I", code, follow)[0]
            if add & 0xFF000000 != 0x91000000 or (add >> 5) & 31 != reg:
                continue
            if (add >> 22) & 3 not in (0, 1):
                continue
            shift = 12 if (add >> 22) & 1 else 0
            target = page + (((add >> 10) & 0xFFF) << shift)
            refs.append((at, follow, target))
    refs.sort(key=lambda r: (abs(r[0] - site), r[0], r[1]))
    return refs[:limit]


def segment_for_address(module, address):
    """Coarse NSO segment classification, not proof of a pointed-to object."""
    for name, (base, length) in zip(("text", "ro", "data"), module.segments):
        if base <= address < base + length:
            return name
    if len(module.image) <= address < len(module.image) + module.bss:
        return "bss"
    return "outside"




EXPECTED_MCD2_MAIN_BID = "9e9d887d59f7f7db176b35e3379258b9"
MCD2_UE_STARTUP_CALL = 0x143B0
MCD2_UE_STARTUP_TARGET = 0xB0560


def candidate_constructor_refs(module, call_site=MCD2_UE_STARTUP_CALL,
                               expected_target=MCD2_UE_STARTUP_TARGET):
    """Report bounded cross-references near one verified same-build BL.

    Extracted NSO memory is examined privately; return addresses/register
    identifiers only, never an executable image or disassembled instructions.
    This is NOT a proof the target is an FMallocBinned3 constructor.
    """
    code = memoryview(module.image)[:module.text_end]
    if not (0 <= call_site < len(code) - 4):
        return None
    matches = near_direct_calls(code, call_site, radius=4, limit=1)
    if (call_site, expected_target) not in matches:
        return None

    caller = nearby_adrp_add_refs(code, call_site - 32, radius=128, limit=64)
    # Keep only addresses set up before the actual call, and only locations
    # that could hold global allocator state. There is no data-flow proof.
    before = []
    for at, add_at, target in caller:
        if add_at >= call_site:
            continue
        kind = segment_for_address(module, target)
        if kind not in ("ro", "data", "bss"):
            continue
        source_reg = struct.unpack_from("<I", code, at)[0] & 31
        dest_reg = struct.unpack_from("<I", code, add_at)[0] & 31
        before.append((at, add_at, source_reg, dest_reg, target, kind))
    before.sort(key=lambda item: (call_site - item[1], item[0]))

    # Constructor candidate references: inspect a bounded first 768 bytes.
    callee = nearby_adrp_add_refs(
        code, expected_target + 0x180, radius=0x180, limit=128
    )
    refs = []
    for at, add_at, target in callee:
        kind = segment_for_address(module, target)
        if kind not in ("ro", "data", "bss"):
            continue
        from_reg = struct.unpack_from("<I", code, at)[0] & 31
        to_reg = struct.unpack_from("<I", code, add_at)[0] & 31
        refs.append((at, add_at, from_reg, to_reg, target, kind))
    refs.sort(key=lambda item: (item[0], item[1]))
    calls = near_direct_calls(
        code, expected_target + 0x180, radius=0x180, limit=24
    )
    return before[:16], refs[:30], sorted(calls)[:18]



# Exact original ARM64 MOVZ encodings used by SDK 20.5.17's address manager.
# These encode mov w8,#0x3fe00000 and mov w9,#0x3fe00000.
# The two sites are 0x6c bytes apart in that SDK. This does NOT make
# the corresponding sites safe to patch in SDK 20.5.15.
SDK_NODE_LIMIT_MOV = 0x52A7FC08
SDK_NODE_OCCUPIED_MOV = 0x52A7FC09
EXPECTED_MCD2_SDK_BID = "190030518b5d64e5d87b84bc54661af9"


def sdk_node_area_sites(code, min_gap=0x40, max_gap=0xC0):
    """Find a limit/occupied MOVZ pair in the *exact* SDK code.

    Report offsets and distances only. Byte equality and adjacency are
    preliminary clues, not proof of AddressRegionManager semantics.
    """
    a, b = [], []
    for offset in range(0, len(code) - 3, 4):
        word = struct.unpack_from("<I", code, offset)[0]
        if word == SDK_NODE_LIMIT_MOV:
            a.append(offset)
        elif word == SDK_NODE_OCCUPIED_MOV:
            b.append(offset)
    pairs = [(start, end, end - start) for start in a for end in b
             if min_gap <= end - start <= max_gap]
    return sorted(pairs, key=lambda x: (x[0], x[1])), len(a), len(b)


def report(survey_dir):
    ext = Path(survey_dir) / "extract"
    rows = manifest_rows(ext / "romfs_manifest.tsv")
    main_file = ext / "ExeFS" / "main"
    if not main_file.is_file():
        raise FileNotFoundError("missing survey ExeFS/main")
    module = nso.load(main_file.read_bytes(), str(main_file))
    image = bytes(module.image)
    main_bid = module.build_id[:16].hex()
    if main_bid != EXPECTED_MCD2_MAIN_BID:
        raise ValueError(f"unexpected MCD2 main build {main_bid}; refusing to infer allocator sites")
    code = memoryview(image)[:module.segments[0][1]]
    versions = unreal_versions(image)
    possibles = possible_62g_immediates(code)
    sdk_file = ext / "ExeFS" / "sdk"
    if not sdk_file.is_file():
        raise FileNotFoundError("missing survey ExeFS/sdk")
    sdk = nso.load(sdk_file.read_bytes(), str(sdk_file))
    sdk_bid = sdk.build_id[:16].hex()
    if sdk_bid != EXPECTED_MCD2_SDK_BID:
        raise ValueError(f"unexpected MCD2 sdk build ID {sdk_bid}; refusing to infer patch sites")
    pairs, node_limit_count, node_occupied_count = sdk_node_area_sites(
        memoryview(sdk.image)[:sdk.text_end])
    groups = io_container_pairs(rows)
    assets = [(p, n) for p, n in rows if p.lower().endswith((".pak", ".utoc", ".ucas"))]
    clean = lambda p: p.replace("\r", "").replace("\n", "_").replace("|", "_")
    out = [
        "# Minecraft Dungeons II: metadata-only diagnostics", "",
        "This is not a boot test, executable patch, IPA or claim of playability.",
        "No decrypted game data, keys, instruction bytes or shaders are included.", "",
        f"- RomFS manifest entries: {len(rows)}",
        f"- Unreal engine version markers: {', '.join(versions) if versions else 'none found'}",
        f"- SwitchVirtualAddressSpaceSize string in main: {b'SwitchVirtualAddressSpaceSize' in image}",
        f"- 62 GiB ORR-immediate candidates: {len(possibles)} (one encoding only)", "",
        "## Memory reservation candidate offsets", "",
    ]
    if possibles:
        for p in possibles[:100]:
            # Only direct-call offsets/targets, not ARM64 disassembly or bytes.
            # This helps narrow the candidate function before private analysis.
            out.append(f"- main+0x{p:x} (candidate, not verified for patching)")
            calls = near_direct_calls(code, p)
            if calls:
                out.append("  - Nearby BL calls (offset -> target; unverified): " +
                           "; ".join(f"0x{at:x}->0x{target:x}" for at, target in calls))
            else:
                out.append("  - No BL within 96 bytes (not evidence of no memory calls)")
            if p < 0x100000:
                refs = nearby_adrp_add_refs(code, p)
                if refs:
                    out.append("  - Nearby ADRP+ADD address references (exploratory):")
                    for at, follow, target in refs:
                        out.append(f"    - main+0x{at:x}/+0x{follow:x} -> 0x{target:x}"
                                   f" ({segment_for_address(module, target)})")
                else:
                    out.append("  - No nearby ADRP+ADD references detected")
        if len(possibles) > 100:
            out.append("- More candidates omitted.")
    else:
        out.append("No match to this one instruction encoding. Other forms remain possible.")
    out += ["", "## UE5.6 allocator startup cross-reference probe", "",
            f"- Exact main build ID checked: {main_bid}",
            f"- Hypothetical caller: main+0x{MCD2_UE_STARTUP_CALL:x}",
            f"- Observed direct-call target: main+0x{MCD2_UE_STARTUP_TARGET:x}",
            "This may be an Unreal allocator constructor but is NOT proven. No patch is applied.", ""]
    probe = candidate_constructor_refs(module)
    if probe is None:
        out.append("The exact caller/target relation is absent; stop investigating this candidate.")
    else:
        before, references, calls = probe
        out.append("### Global references before the candidate call (closest first)")
        for at, follow, source_reg, dest_reg, target, kind in before:
            out.append(f"- main+0x{at:x}/+0x{follow:x}: x{source_reg}->x{dest_reg}, "
                       f"global 0x{target:x} ({kind}, possible state only)")
        if not before:
            out.append("No relevant global references detected in the bounded window.")
        out += ["", "### Early target-function global references", ""]
        for at, follow, source_reg, dest_reg, target, kind in references:
            out.append(f"- main+0x{at:x}/+0x{follow:x}: x{source_reg}->x{dest_reg}, "
                       f"global 0x{target:x} ({kind})")
        if not references:
            out.append("No global references in the first 768 bytes.")
        out += ["", "### Early target-function direct calls", ""]
        for at, dest in calls:
            out.append(f"- main+0x{at:x} -> main+0x{dest:x} (unverified purpose)")
        if not calls:
            out.append("No direct calls in the first 768 bytes.")
    out.append("Next: verify the FMallocBinned3 object, its vtable and the constructor "
               "with offline function analysis before any host-heap interception.")

    out += ["", "## SDK 20.5.15 address-region node-area candidates", "",
            "Signature: two original SDK 20.5.17 MOVZ forms for the ~1 GiB node area;",
            "the second occupied-length instruction follows the limit instruction.",
            f"- Exact sdk build ID checked: {sdk_bid}",
            f"- Raw instruction matches: limit={node_limit_count}, occupied={node_occupied_count}",
            f"- Pairs with 0x40..0xC0-byte separation: {len(pairs)}", ""]
    for start, end, gap in pairs[:30]:
        out.append(f"- sdk+0x{start:x} -> sdk+0x{end:x} (gap 0x{gap:x}; candidate only)")
    if not pairs:
        out.append("No matching pair; do not copy other SDKs' hard-coded offsets.")
    if len(pairs) > 30:
        out.append("- Additional pairs omitted pending further validation.")
    out.append("No SDK cut is proposed until the functions and original bytes are verified.")
    out += ["", "## IoStore archive pairs", ""]
    for path, size, toc in groups:
        out.append(f"- {clean(path)} ({size:,} bytes); TOC: {clean(toc) if toc else 'MISSING'}")
    if not groups:
        out.append("No .ucas files found.")
    out += ["", "## Unreal archive inventory", ""]
    for path, size in sorted(assets, key=lambda x: -x[1]):
        out.append(f"- {clean(path)} ({size:,} bytes)")
    out += ["", "## Follow-up evidence needed", "",
            "1. Use the observed largest .ucas as the game's exact RomFS marker.",
            "2. Disassemble the main-build candidates and validate original instruction bytes.",
            "3. Identify the FMallocBinned3 constructor and vtable layout; do not copy another game's offsets.",
            "4. Inspect SDK node-area candidates in their function context before any build-specific cut.",
            "5. Compile and boot using a Mac and test on iPad before any playability claim."]
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("survey_dir", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    text = report(args.survey_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    print(f"Wrote text-only diagnostics ({len(text)} bytes)")


if __name__ == "__main__":
    main()
