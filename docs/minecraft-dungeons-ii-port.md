# Minecraft Dungeons II port preparation (fork-specific)

**Target:** The **Nintendo Switch 1** release of Minecraft Dungeons II (2026) on an iPad Pro M4 using nxnative. This is the sequel, not Minecraft Dungeons (2020). The Nintendo Switch 2 release is outside nxnative's original-Switch executable and graphics pipeline.

## What has been implemented without any game files

- `scripts/mcd2_preflight.py` uses nxnative's actual package scanner to check base/update title IDs and select the highest version-tagged update. It needs no keys, and it does not decrypt packages.
- `.github/workflows/mcd2-preflight.yml` runs synthetic tests on GitHub Actions. This checks package-selection logic only, not the game or IPA.
- Existing `tests` workflows compile and exercise nxnative's general host/graphics code but cannot prove Minecraft Dungeons II runs on the iPad.

## Preparing your own base game plus update

On your own trusted machine, keep both original-Switch game files together:

```text
Games/MinecraftDungeonsII/
  Minecraft Dungeons II [BASE_TITLE_ID][v0].nsp
  Minecraft Dungeons II [UPDATE_TITLE_ID][vNUMBER].nsp
```

The example fields above are placeholders, **not the actual title ID or version**. Keep the real original package names where possible. An XCI may be used for the base. nxnative reads ticket title IDs when possible and expects updates to have the matching update title ID (typically base ID + 0x800). Place your own `prod.keys` outside that folder. Do not commit or publish game files, keys, decrypted content or completed IPAs in the public GitHub fork.

On any machine with Python 3.12+ and this repository checked out:

```sh
python3 scripts/mcd2_preflight.py /path/to/Games/MinecraftDungeonsII
```

The check prints title IDs and the candidate update version, then the corresponding Mac commands. It cannot establish that the packages are genuinely Minecraft Dungeons II or distinguish platform editions from their filenames alone.

## Game-specific survey (requires a compatible Mac)

Follow upstream [README](../README.md) to install Xcode, .NET SDK and the required build tools. Run the survey command printed by the preflight script, which is equivalent to:

```sh
python3 scripts/build_ipa_from_nsp.py "/path/to/Games/MinecraftDungeonsII" -k "/private/path/prod.keys" --survey-only
```

This extracts a manifest and module facts from the **base with the selected update applied**. For an unsupported game, the output is under `<TITLE_ID>_survey/survey.md`. Review and sanitise the report before sharing it: remove local filesystem paths, personal identifiers or any sensitive material. Next, use the survey to bring up a provisional game profile, then test startup, memory, shader processing and actual gameplay. A provisional IPA is **not** evidence of a working port.

GitHub's virtual Mac runners cannot execute the Metal-4-dependent native graphics tests and are not a substitute for playing on an actual supported device.

## Status

- [x] Confirm upstream logic merges a base game and matching update.
- [x] Create keyless update-pairing helper and synthetic tests.
- [ ] Examine a genuine Minecraft Dungeons II Switch 1 survey.
- [ ] Create/validate a per-title game profile and resolve game-specific failures.
- [ ] Build, sign, sideload and launch a working IPA.
- [ ] Optimise performance on iPad Pro M4 once gameplay is established.
