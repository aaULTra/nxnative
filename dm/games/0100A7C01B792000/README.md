# Minecraft Dungeons II — experimental bring-up (`0100A7C01B792000`)

**Status: first iPad launch reaches `nninitStartup`, then crashes before `nnMain`.** The IPA builds and installs; gameplay is not working. A verified allocator interception is implemented below, pending a rebuilt IPA and another device test.

**Cloud build progress (2026-10-29):** Actions run `40888376530` reported
15,826/15,826 shaders processed and entered iOS packaging, but the old
55-minute iOS stage budget terminated it before successful completion.
The pipeline now targets iOS directly (retaining Unreal/Oodle helper builds)
with a bounded three-hour pass and a separate, archive-verified delivery
step into an owner-owned Drive output file. A full Mac shader pass is no
longer a prerequisite for creating the IPA. Backup run `40927615593` subsequently produced and delivered a verified
IPA. Its first M4 iPad launch reached Unreal startup but failed during allocator construction, as detailed below. See the [build/recovery contract](../../../docs/agents/mcd2-build-recovery.md).

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

The first iPad log confirms the engine option works: `nninitStartup`
successfully allocates 4064 MiB (`0xfe000000`) at alias +62 MiB. The
remaining incompatible request comes from the allocator constructor, not
the engine-region option. Increased Memory Limit does not make the
allocator's 38 GiB virtual pool reservation fit.

## First iPad startup failure and verified allocator interception

The first device test (2026-10-30, LiveContainer on the owner's M4 iPad)
loads and relocates all three modules, reports `stubs=0`, displays the
launcher and reaches `nninitStartup`. It then crashes with signal 11 at
`main+0x141619c`, reading address `0x30d8`. The original instruction is a
load from `[x19,#0x30d8]`; the supplied register dump has `x19=0`. The
backtrace includes `main+0x1484c`, `+0x14c28`, `+0x149e0`, `+0xb0654` and
`+0x143b4`. Both `x21` and `x24` contain `0x980000000` (**38 GiB**).
No `nnMain` or rendered game frame was reached.

Private exact-build instruction inspection in Actions run `40977368275`
confirmed the constructor and interface, replacing the earlier unverified
allocator candidate:

- `main+0x143a4/+0x143a8` forms `x0=main+0x9979228`, the fixed BSS object.
  The original call at `+0x143b0` (`6c700294`) targets `+0xb0560`.
- The constructor first stores the base FMalloc vtable `+0x85b6ff0`, then
  the **derived** FMallocBinned3 vtable `+0x85b68a8`. These are different: the
  base has null pure slots 6/8/10 and must not be installed as the derived
  allocator. At `+0xb0650`, it requests `0x980000000` bytes with 4096-byte
  alignment from `+0x14964`, matching the failed device call chain.
- BaseAllocator (`+0x14824`) returns this object. TLS allocation/free
  callbacks load its vtable and dispatch through offsets `0x30`/`0x50`,
  slots 6/10. The derived table matches the existing 32-slot `UE5_6`
  interface: TryMalloc/TryRealloc dispatch through Malloc/Realloc, the two
  zeroed methods call Malloc, QuantizeSize/GetAllocationSize are 13/14,
  thread-safety/heap validation are 24/25, descriptive name is 26, and the
  cached-memory queries are 30/31.

The profile's `patches.py` now omits this constructor call **only for main
build `9e9d887d59f7f7db176b35e3379258b9`**, asserting the original bytes. A
matching row in `hle_uemalloc.c` installs the existing host malloc-zone
allocator in the fixed object before any guest code runs. There is no
change to generic allocator behavior, the shader chain, 5 GiB reservation
or entitlement requirements. The out-of-memory report itself is not
suppressed.

**Validation boundary:** this identifies and implements the first startup
blocker; it does not establish a successful boot, safe engine operation
after the constructor is skipped, or gameplay. The rebuilt IPA must log
the host allocator installation, pass this call site and reach `nnMain`
on the next iPad test. Later startup/runtime failures remain possible.

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
source and rewrite compatibility fix, not a successful boot.** The first iPad test later confirmed the bounded engine region works;
FMallocBinned3 interception is now implemented above but awaits a new
device test. Do not enable
Extended Virtual Addressing or enlarge the 5 GiB guest reservation.

## Bounded memory configuration: engine option confirmed on iPad

The survey's auto-guess reserved roughly 64 GiB of guest address space for Unreal's default 62 GiB engine region. The device test confirms that the engine option reduces that region, while the inspected allocator still independently requests 38 GiB. Changing `mem_reserve` alone does not resolve those allocations.

This profile instead uses the 5 GiB host layout from successful nxnative Unreal ports: `mem_reserve 0x140000000`, `mem_alias 0x118000000`, 256 MiB stacks, 128 MiB reserved heap and the Switch app pool `0xCD500000`. The first iPad test confirms `-SwitchVirtualAddressSpaceSize=4064` successfully allocates the intended 4064 MiB engine region. This does not shrink FMallocBinned3's separate 38 GiB pool request; the verified host allocator interception above addresses that constructor.

The game's exact `main` build is not the build from any other profile. **Do not copy another game's `patches.py` offsets or Nintendo SDK patches.** The registered constructor cut asserts the original instruction bytes, and the host mapping checks this build ID and the rewritten NOP before installation. SDK 20.5.15's separate build-specific node-area cuts keep their own byte assertions.

## Specific next checkpoints

1. **Startup:** rebuild with the verified constructor cut and matching host allocator row. On the next iPad run, require the host-allocator installation log and progress beyond `nninitStartup`; collect the first subsequent failure.
2. **Engine/gameplay:** reach `nnMain`, render the title/menu, load a level and verify controls, audio and save/reload. Measure available memory, sustained frame timing and heat on the installed build.
3. **Shader/runtime validation:** shader extraction, translation, iOS compilation and IPA packing completed in run `40927615593`. Successful packaging does not establish correct runtime shaders or rendering; investigate actual graphics failures when reached.
4. **Recovery:** use the private complete-preparation workflow for the replacement IPA. Its first real-game checkpoint and later fresh-run restore still need observation.

### Profile choices and current limits

- No FPS override is applied. For Unreal games, forcing 60 can alter speed if their frame timing is fixed.
- No `tex_dyn_slots`, texture flush, render conditional or unrelated SDK overrides are enabled without a graphics trace.
- The exact RomFS marker and game resources passed packaging validation. Actual engine asset loading remains untested.
- The SDK cuts passed rewriting and the first iPad test allocated the bounded engine region. Broader SDK behavior and runtime stability remain unverified.
- Mac gameplay regression checks cannot run in the current Linux workspace; only the owner can validate this IPA on their iPad.

## Testing from the fork (without NSPs)

```sh
python3 dm/games/game.py 0100A7C01B792000 name
python3 dm/games/game.py 0100A7C01B792000 package
python3 -m unittest discover -s scripts/tests -p 'test_mcd2_*.py' -v
```

Do **not** upload game NSPs or `prod.keys` to the public fork or public Actions artifacts. The repository-scoped secrets already configured for the private survey remain separate.

### Cloud delivery milestone (2026-10-29)

Backup run [40927615593](https://github.com/aaULTra/nxnative/actions/runs/40927615593)
completed iOS preparation, app compilation and IPA packaging. The delivery
step verified the ZIP CRCs, app resources and Metal payload, then independently
verified the stored Drive size/MD5 for 4,738,770,141 bytes. This is a successful
packaging/delivery milestone, **not** Mac or iPad gameplay validation. The
allocator/bring-up questions above remain open. The previous run's failure
was after archive validation during delivery; see the
[recovery contract](../../../docs/agents/mcd2-build-recovery.md#verified-upload-failure-and-complete-preparation-recovery).
