# Minecraft Dungeons II — experimental bring-up (`0100A7C01B792000`)

**Status: not booted. No IPA or gameplay tested.** This is a source-level profile derived from the first successful Linux NSP survey, not a validated game port.

**Cloud build progress (2026-10-29):** Actions run `40888376530` reported
15,826/15,826 shaders processed and entered iOS packaging, but the old
55-minute iOS stage budget terminated it before successful completion.
The pipeline now targets iOS directly (retaining Unreal/Oodle helper builds)
with a bounded three-hour pass and a separate, archive-verified delivery
step into an owner-owned Drive output file. A full Mac shader pass is no
longer a prerequisite for creating the IPA. This is workflow progress only:
the new pipeline has not yet produced a verified IPA or run this game on
the owner's M4 iPad. See the [build/recovery contract](../../../docs/agents/mcd2-build-recovery.md).

An optional backup entry point now adds readable Drive status and rotating,
hash-verified backups of completed Metal compile-cache files. It uses a
separate IPA destination and does not interrupt the first run. Synthetic
fresh-session restore is tested; real-game cross-run cache reuse and device
gameplay still require validation. It is partial shader-cache recovery,
not a completed-stage or whole-build checkpoint.

## Evidence (2026-10-28 survey)

| Detail | Observed |
|---|---|
| Base title | `0100A7C01B792000` |
| Update title | `0100A7C01B792800` |
| Selected package / display | `v65536` / `1.1.1.0` |
| `main` build ID | `9e9d887d59f7f7db176b35e3379258b9` |
| `sdk` build ID | `190030518b5d64e5d87b84bc54661af9` |
| `subsdk0` build ID | `3904a8dee6801472d56bd4921378df27` |
| SDK | NintendoSDK_nnSdk-20_5_15 (SDK 20.5.15) |
| Engine/API | **Unreal Engine 5.6.1** (`UnrealEngine-5_6_1` in updated `main`); NVN; Havok; Wwise |
| NPDM system resource | 20 MiB |
| RomFS | 61 files, 3.87 GiB; 2 `.ucas`, 2 `.utoc`, 1 `.pak`, 44 `.mp4` |
| Instruction census | 30,284,432 main instructions; 1,674,226 sdk; 875,249 subsdk0 |

A second targeted survey (GitHub Actions run `40789063074`) verified the Unreal 5.6.1 version string, the exact `Dungeons-Switch.ucas` and `global.ucas` paths, and six possible 62 GiB constant instructions at `main+0x14380`, `+0x14a18`, `+0x14a24`, `+0x67916a0`, `+0x6e68f90`, and `+0x6e8d990`. These offsets are **candidates only**: none has been examined in context or verified as a safe patch. The three low offsets are nearer to other Unreal 5.6 startup regions than the three high offsets, but proximity alone is not proof of their role.

The observed archives are `Dungeons/Content/Paks/Dungeons-Switch.ucas` (3,800,288,256 bytes), `Dungeons-Switch.utoc` (7,878,448 bytes), `Dungeons-Switch.pak` (243,619,477 bytes), and `global.ucas`/`global.utoc` (3,797,488/890 bytes). These are game-file metadata, not proof of any shader library's layout.

The public synthetic tests and the Linux extraction ran successfully with the base+update pair. The full survey is available from GitHub Actions run `40787097883` as a short-lived `mcd2-survey-report` artifact. The game binaries, keys and decrypted content do **not** belong in this public repository.

## iPadOS signing target: Increased Memory Limit without Extended Virtual Addressing

The owner can use `com.apple.developer.kernel.increased-memory-limit` but **cannot**
use `com.apple.developer.kernel.extended-virtual-addressing`. These are
distinct entitlements: the first can increase the permitted resident memory
on supported devices; it does not promise a larger virtual address space.
The iOS signing script (`dm/ios/tools/build_ipa.sh`) currently requests
**Increased Memory Limit only** by default, both for the ad-hoc-signed IPA
and the prepared Xcode project. Do not add Extended Virtual Addressing
to this title's profile, build or signing requirements.

This title therefore keeps a **single 5 GiB guest address reservation** and
the 4.375 GiB alias partition already chosen. The nxnative host documents
a real-device 6 GiB successful single reservation without Extended Virtual
Addressing, but larger reservations and fragmented overall process address
space cannot be assumed safe. Do not enlarge `mem_reserve` or set
`DM_ALIAS_SIZE` to create a larger guest reservation as a workaround.

The launcher already displays **Memory available** using Apple's
`os_proc_available_memory()`, refreshed every two seconds. Use that
value on the signed iPad build to check the *actual available physical-memory
headroom*, rather than inferring it from a 5 GiB `mmap` reservation.
Signing/ad-hoc entitlement requests are **not proof** the final installation
retains the entitlement: verify the installed app's signature/provisioning
and the measured headroom on device.

The primary unresolved memory issues are still Unreal's approximately
62 GiB address-region reservation and FMallocBinned3's approximately
37 GiB pool reservation. The test profile's `game_args` is an
unverified hypothesis. Those engine allocations must be bounded or
replaced **without** disabling integrity checks, without silently
truncating them, and with opcode-verified patches of this game's exact
build ID. Even an increased resident-memory allowance cannot make the
original giant virtual reservations work.

## Unreal 5.6.1 allocator candidate (not yet safe to patch)

The text-only private diagnostic in Actions run `40797926825` verified the
exact updated `main` build ID `9e9d887d59f7f7db176b35e3379258b9`.
The startup call `main+0x143b0 → main+0xb0560` is present. Immediately
before the call, the address calculation at `main+0x143a4/+0x143a8`
prepares `x0 = main+0x9979228`, a BSS address. This is a *candidate*
allocator object, not yet a confirmed FMallocBinned3 instance.

In the first 768 bytes of the called function, the diagnostic found data
references `main+0x85b6ff0` and `main+0x85b68a8`, a BSS reference
`main+0x9dc7884`, and calls to multiple engine functions. The identities
of those globals, their relocations and the FMalloc vtable **have not**
been confirmed. The closest source reference, FINAL FANTASY RESONANCE
(also Unreal Engine 5.6.1), cuts its own constructor and installs the
host malloc-zone vtable, but that game's offsets are not portable.

**Do not add a CUT at `0x143b0` or a `hle_uemalloc.c` row yet.** First
confirm the function's construction semantics, object shape, vtable
address and the original instruction bytes in the exact MCD2 NSO.
The current SDK node-area patch is independently verified; this
outstanding Unreal host allocator work is separate and remains a boot
blocker. It must be completed within the existing **5 GiB** guest
reservation, using only Increased Memory Limit, **without** Extended
Virtual Addressing.

## SDK 20.5.15 compact node-area patch

The 2026-10-29 private diagnostic (Actions run `40793486912`) found
exactly one pair of SDK MOVZ instructions at `sdk+0x145cbc` and
`sdk+0x145d28`. Both have the same original bytes, offsets, and 0x6c
separation as the tested SDK 20.5.17 branch used by FINAL FANTASY
RESONANCE. A **build-ID-specific pair of cuts** has therefore been added
to `dm/tools/sdk_patches.py`: the node-area limit and its occupied
region bookkeeping both change from approximately 1 GiB to 62 MiB.

The NSO rewriter asserts the original instruction bytes and aborts on
any mismatch. The private Mac rewriter workflow verifies that precisely
two SDK cuts are actually emitted before reporting success. **This is a
source and rewrite compatibility fix, not a successful boot.** The game
still requires engine-specific FMallocBinned3 interception, correct
`nninitStartup` region handling and shader translation. Do not enable
Extended Virtual Addressing or enlarge the 5 GiB guest reservation.

## Bounded memory configuration: a hypothesis, not a patch

The survey's auto-guess reserves roughly 64 GiB of guest address space, because modern Unreal's `nninitStartup` asks for a 62 GiB address region and FMallocBinned3 is expected to reserve ~37 GiB of it. Merely changing `mem_reserve` in `game.tsv` does **not** fix either request. A boot attempt with the current profile could still fail during engine memory setup.

This profile instead uses the 5 GiB host layout from successful nxnative Unreal ports: `mem_reserve 0x140000000`, `mem_alias 0x118000000`, 256 MiB stacks, 128 MiB reserved heap and the Switch app pool `0xCD500000`. It sets `-SwitchVirtualAddressSpaceSize=4064` as a **tentative** engine argument, which works for FINAL FANTASY RESONANCE (SDK 20.5.17, Unreal 5.6) and DRAGON QUEST VII Reimagined (Unreal 4.27), but was not effective for Pikmin 4 (Unreal 4.26). If that argument skips the engine's region allocation in this game, engine-specific verified code cuts are required instead.

The game's exact `main` build is not the build from any other profile. **Do not copy another game's `patches.py` offsets or Nintendo SDK patches.** Once an actual module image is accessible to a private diagnostic run, inspect `nninitStartup` and `GCreateMalloc` and assert instruction bytes before patching. SDK 20.5.15 has its own build ID and address-region manager; it needs the same careful verification.

## Specific next checkpoints

1. **Assets:** done. The merged RomFS manifest verified `Dungeons/Content/Paks/Dungeons-Switch.ucas` and its matching `.utoc`; the profile now has this exact marker. Next extract the shader archive layout privately.
2. **Code bring-up:** inspect and bound Unreal's 62 GiB address-region request, identify whether `-SwitchVirtualAddressSpaceSize=4064` works, then identify FMallocBinned3's allocator-constructor site and vtable layout for this exact build. Verify instruction bytes before registering cuts or an observation gate.
3. **SDK:** the original node-area instructions and offsets match SDK 20.5.17 and now have narrowly scoped, byte-asserted cuts in this exact SDK build. The changes still require validation through the Mac rewrite and subsequent real boot; alignment and other SDK behaviour remain unverified.
4. **Shader libraries:** inspect `.pak` and both IoStore `.utoc`/`.ucas` pairs. The survey found Unreal 5.6.1 and NVN, but did not extract or count shader archives. Existing `uepak.py` supports IoStore and may require Oodle decoding through the Mac host.
5. **Mac bring-up:** rewrite the modules, run the Mac host with `DM_SVC=1 DM_WATCH=20` and keep the first crash, `[RED]` lines and memory reservation trace. Mac compilation alone cannot verify on-device graphics.
6. **iPad testing:** only after the host reaches frames should an unsigned IPA be prepared and signed for the owner's device. Test controls, audio, shaders, memory headroom and heat. Do not claim a 60 fps target before measured gameplay.

### Profile choices not inferred from the survey

- No FPS override is applied. For Unreal games, forcing 60 can alter speed if their frame timing is fixed.
- No `tex_dyn_slots`, texture flush, render conditional or SDK-specific overrides are enabled without a graphics trace.
- The `romfs_marker` is an observed file path, not guessed. It has not been tested in a packed IPA.
- Shader groups are an Unreal source hypothesis; a real split must locate and count shaders.

## Testing from the fork (without NSPs)

```sh
python3 dm/games/game.py 0100A7C01B792000 name
python3 dm/games/game.py 0100A7C01B792000 package
python3 -m unittest discover -s scripts/tests -p 'test_mcd2_*.py' -v
```

Do **not** upload game NSPs or `prod.keys` to the public fork or public Actions artifacts. The repository-scoped secrets already configured for the private survey remain separate.
