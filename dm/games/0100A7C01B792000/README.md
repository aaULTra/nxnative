# Minecraft Dungeons II — experimental bring-up (`0100A7C01B792000`)

**Status: not booted. No IPA or gameplay tested.** This is a source-level profile derived from the first successful Linux NSP survey, not a validated game port.

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
| Engine/API | Unreal Engine 4; NVN; Havok; Wwise |
| NPDM system resource | 20 MiB |
| RomFS | 61 files, 3.87 GiB; 2 `.ucas`, 2 `.utoc`, 1 `.pak`, 44 `.mp4` |
| Instruction census | 30,284,432 main instructions; 1,674,226 sdk; 875,249 subsdk0 |

The public synthetic tests and the Linux extraction ran successfully with the base+update pair. The full survey is available from GitHub Actions run `40787097883` as a short-lived `mcd2-survey-report` artifact. The game binaries, keys and decrypted content do **not** belong in this public repository.

## Bounded memory configuration: a hypothesis, not a patch

The survey's auto-guess reserves roughly 64 GiB of guest address space, because modern Unreal's `nninitStartup` asks for a 62 GiB address region and FMallocBinned3 is expected to reserve ~37 GiB of it. Merely changing `mem_reserve` in `game.tsv` does **not** fix either request. A boot attempt with the current profile could still fail during engine memory setup.

This profile instead uses the 5 GiB host layout from successful nxnative Unreal ports: `mem_reserve 0x140000000`, `mem_alias 0x118000000`, 256 MiB stacks, 128 MiB reserved heap and the Switch app pool `0xCD500000`. It sets `-SwitchVirtualAddressSpaceSize=4064` as a **tentative** engine argument, which works for FINAL FANTASY RESONANCE (SDK 20.5.17, Unreal 5.6) and DRAGON QUEST VII Reimagined (Unreal 4.27), but was not effective for Pikmin 4 (Unreal 4.26). If that argument skips the engine's region allocation in this game, engine-specific verified code cuts are required instead.

The game's exact `main` build is not the build from any other profile. **Do not copy another game's `patches.py` offsets or Nintendo SDK patches.** Once an actual module image is accessible to a private diagnostic run, inspect `nninitStartup` and `GCreateMalloc` and assert instruction bytes before patching. SDK 20.5.15 has its own build ID and address-region manager; it needs the same careful verification.

## Specific next checkpoints

1. **Assets:** use the existing private runner with the owner's original base and `v65536` update. Extract a *text-only* RomFS manifest to choose the real, stable `.ucas` path as `romfs_marker`; `Dungeons/Content` is only a directory known to exist from the survey.
2. **Code bring-up:** inspect and bound Unreal's 62 GiB address-region request, identify whether `-SwitchVirtualAddressSpaceSize=4064` works, then identify FMallocBinned3's allocator-constructor site and vtable layout for this exact build. Verify instruction bytes before registering cuts or an observation gate.
3. **SDK:** inspect `sdk` 20.5.15's node-area reservation and virtual-address alignment. Existing SDK 20.5.17 work is a reference, not proof the offsets match.
4. **Shader libraries:** inspect `.pak` and both IoStore `.utoc`/`.ucas` pairs. The survey found Unreal and NVN, but did not extract or count shader archives. Existing `uepak.py` supports IoStore and may require Oodle decoding through the Mac host.
5. **Mac bring-up:** rewrite the modules, run the Mac host with `DM_SVC=1 DM_WATCH=20` and keep the first crash, `[RED]` lines and memory reservation trace. Mac compilation alone cannot verify on-device graphics.
6. **iPad testing:** only after the host reaches frames should an unsigned IPA be prepared and signed for the owner's device. Test controls, audio, shaders, memory headroom and heat. Do not claim a 60 fps target before measured gameplay.

### Profile choices not inferred from the survey

- No FPS override is applied. For Unreal games, forcing 60 can alter speed if their frame timing is fixed.
- No `tex_dyn_slots`, texture flush, render conditional or SDK-specific overrides are enabled without a graphics trace.
- A `romfs_marker` directory is provisional. The manifest's exact largest / game `.ucas` path is preferable.
- Shader groups are an Unreal source hypothesis; a real split must locate and count shaders.

## Testing from the fork (without NSPs)

```sh
python3 dm/games/game.py 0100A7C01B792000 name
python3 dm/games/game.py 0100A7C01B792000 package
python3 -m unittest discover -s scripts/tests -p 'test_mcd2_*.py' -v
```

Do **not** upload game NSPs or `prod.keys` to the public fork or public Actions artifacts. The repository-scoped secrets already configured for the private survey remain separate.
