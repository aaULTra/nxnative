#!/usr/bin/env python3
"""Keyless pairing check for the original-Switch Minecraft Dungeons II game and update.

Checks package headers, title IDs and version tags. Does not decrypt packages,
identify game contents, or establish that a port will run.
"""
import argparse
from pathlib import Path
import shlex
import sys

# Reuse nxnative's actual title/ticket scanner instead of duplicating its rules.
import build_ipa_from_nsp as nx


# Verified filename metadata supplied for the Switch 1 target; not a cryptographic
# proof of game identity. Update title ID is the base title ID + 0x800.
EXPECTED_BASE_TITLE = 0x0100A7C01B792000


class PairingError(ValueError):
    pass


def inspect_folder(folder: Path):
    if not folder.is_dir():
        raise PairingError(f"Not a directory: {folder}")
    try:
        title_id, family = nx.scan_nsps(folder)
    except (nx.BuildError, OSError, ValueError) as exc:
        raise PairingError(str(exc)) from exc
    if title_id != EXPECTED_BASE_TITLE:
        raise PairingError(
            f"Unexpected base title ID {title_id:016X}; expected "
            f"{EXPECTED_BASE_TITLE:016X} for the supplied Minecraft Dungeons II files."
        )
    if not family['updates']:
        raise PairingError("No update detected. Verify the matching update really has an .nsp or .nsz extension; nxnative ignores extensionless files. Keep base and update together.")
    # Unknown games have no registered version table, so the update needs its
    # real [vNUMBER] file-name tag to be selected instead of silently using v0.
    available = nx.nsp_versions(family, {})
    tagged = [(int(version[1:]), version, path)
              for version, path in available.items() if path is not None]
    if not tagged:
        raise PairingError("Update found but not selectable. Use its real [vNUMBER] filename tag; do not invent a version.")
    _, latest_version, latest_path = max(tagged)
    return title_id, family['base'], latest_version, latest_path, family


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('game_folder', type=Path, help='folder with base and update dumps')
    parser.add_argument('--keys', type=Path, default=Path.home() / 'keys/prod.keys',
                        help='local keys path to include in suggested commands; the check does not read it')
    args = parser.parse_args()
    try:
        title, base, version, update, family = inspect_folder(args.game_folder.expanduser().resolve())
    except PairingError as exc:
        print(f'Preflight failed: {exc}', file=sys.stderr)
        return 1
    print(f'Base title ID: {title:016X}')
    print(f'Base package:  {base.name}')
    print(f'Update chosen: {update.name} ({version})')
    if len(family['updates']) > 1:
        print(f'Other updates detected: {len(family["updates"]) - 1}; highest tagged version selected.')
    print('Package pairing: PASS (header/title/version check only)')
    print('This does not prove the files are Minecraft Dungeons II or that the port will boot.')
    keys = args.keys.expanduser()
    if not keys.is_file():
        print(f'Note: keys not present at {keys}; supply your own for a build.')
    quoted_folder = shlex.quote(str(args.game_folder.expanduser().resolve()))
    quoted_keys = shlex.quote(str(keys))
    command = f'python3 scripts/build_ipa_from_nsp.py {quoted_folder} -k {quoted_keys}'
    print('\nOn a compatible Mac, first run:')
    print(command + ' --survey-only')
    print('Once the survey has been examined:')
    print(command + f' -p {version} --show-fps')
    print('Never push the game files, keys, extracted files, or the IPA to a public repo.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
