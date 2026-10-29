#!/usr/bin/env python3
"""Verified complete preparation checkpoints and packaging-only recovery for MCD2."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile

from mcd2_cloud import DriveStore, digest, environment_identity, utc
from mcd2_drive_api_download import BASE_FILENAME, UPDATE_FILENAME
from mcd2_drive_transport import safe_failure
from mcd2_integrated_build import GAME, guard
from mcd2_checkpoint import valid_key

MAX_STAGE_BYTES = 12 * 1024**3
MAX_STAGE_FILES = 200000
ROOTS = ('patched_v65536', 'prepared_profile', 'shader/ios')
VALUES = {'GAME', 'GFXAPI', 'DM_DISPLAY_NAME', 'DM_BUNDLE_ID', 'DM_TEAM_ID', 'DM_LANG',
          'DM_COPY_ROMFS', 'DM_APP_VERSION', 'DM_SHOW_FPS', 'DM_PSO_GPUS', 'DM_GAMES_FIRST'}
PATHS = {'DM_GAMES_DIR', 'W', 'PATCHED', 'ROMFS', 'MSL_IOS', 'AOC', 'DM_XCODE_DERIVED_DATA',
         'DM_ICON', 'DM_ENV_FILE', 'DM_ROOT', 'WORK'}


def safe_path(name):
    p = PurePosixPath(name)
    return bool(name and p.as_posix() == name and not p.is_absolute() and '..' not in p.parts and '\\' not in name)


def allowed(name):
    return safe_path(name) and (name in ('package-context.json', 'patched_v65536.key')
            or any(name.startswith(p + '/') for p in ROOTS)
            or (name.startswith('shader/cache/b-') and name.endswith('/_b_build.json')))


def validate_stage(work):
    work = Path(work)
    context = json.loads((work / 'package-context.json').read_text())
    if context.get('schema') != 1 or context.get('game') != GAME:
        raise ValueError('Wrong preparation context')
    values, paths = context.get('values', {}), context.get('paths', {})
    if (not isinstance(values, dict) or set(values) - VALUES or values.get('GAME') != GAME
            or not isinstance(paths, dict) or set(paths) - PATHS
            or any(not isinstance(v, str) for v in values.values())
            or any(not isinstance(v, str) or not safe_path(v) for v in paths.values())):
        raise ValueError('Unsafe or unsupported packaging context')
    if paths.get('PATCHED') != 'patched_v65536' or paths.get('MSL_IOS') != 'shader/ios':
        raise ValueError('Unsupported preparation paths')
    if (paths.get('DM_GAMES_DIR') != 'prepared_profile' or paths.get('W') != 'ipa'
            or paths.get('ROMFS') != 'extract/v65536/RomFS.img'
            or not valid_key(work / 'patched_v65536.key')):
        raise ValueError('Missing preparation fingerprint or required packaging paths')
    for module in ('main', 'sdk', 'subsdk0'):
        for suffix in ('.patched.bin', '.fixups.tsv'):
            p = work / 'patched_v65536' / (module + suffix)
            if not p.is_file() or p.stat().st_size == 0:
                raise ValueError('Incomplete rewritten modules')
    if not (work / 'prepared_profile' / GAME / 'game.tsv').is_file():
        raise ValueError('Missing portable game profile')
    if not (work / 'prepared_profile' / GAME / 'c1_full.tsv').is_file():
        raise ValueError('Missing generated shader reflection table')
    complete = False
    for stamp in (work / 'shader/ios').glob('b-*.key'):
        lib = stamp.with_suffix('')
        if not any(lib.glob('*.metallib')) or not (lib / 'inputs.tsv').is_file():
            continue
        summary = work / 'shader/cache' / lib.name / '_b_build.json'
        if not summary.is_file(): continue
        rows = json.loads(summary.read_text())
        if (valid_key(stamp) and isinstance(rows, list) and rows
                and f'game\t{GAME}' in (lib / 'inputs.tsv').read_text()):
            complete = True
    if not complete:
        raise ValueError('Full shader completion stamp or build summary is missing')
    return context


def preparation_identity(repo, folder):
    identity = dict(environment_identity(repo, folder))
    identity.pop('source')  # packaging/upload/source commit changes alone are not shader dependencies
    files = subprocess.check_output(['git', 'ls-files', '-z', '--', 'dm/host', 'dm/gfx', 'dm/tools',
                 'dm/games', 'dm/ios/tools/ios_metal.py', 'scripts/build_ipa_from_nsp.py'], cwd=repo).decode().split('\0')
    h = hashlib.sha256()
    for name in sorted(f for f in files if f and '/tests/' not in f and not f.endswith('.md')):
        p = Path(repo) / name
        if p.is_file():
            h.update(name.encode() + b'\0' + bytes.fromhex(digest(p)))
    identity['preparation_dependencies'] = h.hexdigest()
    return identity


def save_stage(work, archive_path, identity):
    work = Path(work)
    validate_stage(work)
    files = [work / 'package-context.json', work / 'patched_v65536.key']
    for root in ROOTS:
        files += sorted((work / root).rglob('*'))
    files += sorted((work / 'shader/cache').glob('b-*/_b_build.json'))
    records, total = {}, 0
    with tarfile.open(archive_path, 'w') as archive:
        for path in files:
            if path.is_symlink(): raise ValueError('Prepared inputs must not contain symlinks')
            if not path.is_file(): continue
            name = path.relative_to(work).as_posix()
            if not allowed(name): raise ValueError('Unexpected preparation file')
            size = path.stat().st_size
            total += size
            if len(records) >= MAX_STAGE_FILES or total > MAX_STAGE_BYTES:
                raise ValueError('Prepared checkpoint exceeds its storage budget')
            if size > 512 * 1024**2: raise ValueError('Prepared file exceeds individual file budget')
            data = path.read_bytes()
            info = tarfile.TarInfo(name); info.size = size
            if len(data) != size: raise ValueError('Prepared file changed while snapshotting')
            archive.addfile(info, io.BytesIO(data))
            records[name] = {'size': size, 'sha256': hashlib.sha256(data).hexdigest()}
        manifest = {'schema': 1, 'kind': 'complete-ios-preparation', 'identity': identity, 'files': records}
        data = json.dumps(manifest, sort_keys=True).encode()
        info = tarfile.TarInfo('manifest.json'); info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    return len(records), total


def restore_stage(source, work, identity):
    work = Path(work); work.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work.parent) as td, tarfile.open(source) as archive:
        members = {}
        for item in archive:
            if (len(members) >= MAX_STAGE_FILES + 1 or item.name in members or not item.isfile()
                    or (item.name != 'manifest.json' and not allowed(item.name))):
                raise ValueError('Unsafe preparation archive')
            members[item.name] = item
        item = members.pop('manifest.json', None)
        if item is None or item.size > 64 * 1024**2:
            raise ValueError('Missing bounded preparation manifest')
        stream = archive.extractfile(item)
        if stream is None: raise ValueError('Missing preparation manifest data')
        manifest = json.load(stream)
        if (manifest.get('schema') != 1 or manifest.get('kind') != 'complete-ios-preparation'
                or manifest.get('identity') != identity or set(manifest.get('files', {})) != set(members)):
            raise ValueError('Preparation dependencies, inputs or toolchain changed')
        total = 0
        for name, item in members.items():
            row = manifest['files'][name]; total += item.size
            if row.get('size') != item.size or total > MAX_STAGE_BYTES:
                raise ValueError('Preparation size mismatch')
            path = Path(td) / name; path.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.extractfile(item)
            if stream is None: raise ValueError('Missing prepared file data')
            h = hashlib.sha256()
            with path.open('wb') as out:
                while block := stream.read(8 * 1024**2): h.update(block); out.write(block)
            if h.hexdigest() != row.get('sha256'): raise ValueError('Preparation checksum mismatch')
        validate_stage(td)  # nothing reaches the work folder until every file and stage check passes
        work.mkdir(exist_ok=True)
        for name in manifest['files']:
            target = work / name; target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(Path(td) / name, target)
    return len(members)


def package_environment(work):
    work = Path(work).resolve()
    context = validate_stage(work)
    env = dict(os.environ, **context['values'], FONT_PY=sys.executable)
    for key, rel in context['paths'].items(): env[key] = str(work / rel)
    env['DM_PSO_GPUS'] = 'none'
    return env


def stage_ids():
    return {key: os.environ[name] for key, name in
            (('index', 'MCD2_READY_INDEX'), ('a', 'MCD2_READY_A'), ('b', 'MCD2_READY_B'))}


def rehydrate(repo, folder, keys):
    import build_ipa_from_nsp as builder
    folder, repo = Path(folder), Path(repo)
    sys.path.insert(0, str(repo / 'dm/games'))
    import game
    work = folder / f'{GAME}_build'
    env = package_environment(work)
    (work / 'tmp').mkdir(parents=True, exist_ok=True)
    logs = work / 'logs'; logs.mkdir(exist_ok=True)
    nx = builder.build_nx_extract(repo, work, logs, env)
    target = work / 'extract/v65536'
    if not (target / 'RomFS.img').is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        builder.run([nx, keys, folder / BASE_FILENAME, folder / UPDATE_FILENAME, target, '--romfs-image'],
                    logs / 'rehydrate.log', env=env)
    if builder.build_mismatch(target, 'v65536', game.load(GAME)):
        raise ValueError('Restored game module build IDs differ from the registered update')
    print('[READY] Game resources extracted; rewritten code and shaders were retained', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('restore', 'save', 'rehydrate', 'package'))
    parser.add_argument('folder', type=Path)
    parser.add_argument('--keys', type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    folder = args.folder.resolve(); work = folder / f'{GAME}_build'
    guard(repo)
    try:
        if args.mode == 'package':
            print(json.dumps({'ev': 'stage', 'name': 'app build'}), flush=True)
            env = package_environment(work)
            subprocess.run(['bash', str(repo / 'dm/ios/tools/build_ipa.sh'), str(folder / 'MinecraftDungeonsII.ipa')],
                           env=env, check=True)
            return 0
        if args.mode == 'rehydrate':
            print(json.dumps({'ev': 'stage', 'name': 'extract resources'}), flush=True)
            if args.keys is None: raise ValueError('Keys required to restore game resources')
            rehydrate(repo, folder, args.keys); return 0
        ids, store = stage_ids(), DriveStore()
        store.check(ids['index'], {'MCD2-ready-index.json'})
        identity = preparation_identity(repo, folder)
        index = store.get_json(ids['index'])
        if index.get('schema') != 1: raise ValueError('Unknown preparation index schema')
        row = index.get('latest')
        print(json.dumps({'ev': 'stage', 'name': 'checkpoint restore' if args.mode == 'restore' else 'checkpoint upload'}), flush=True)
        if args.mode == 'restore':
            restored = False
            if row and row.get('identity') == identity and row.get('slot') in ('a', 'b'):
                store.check(ids[row['slot']], {f"MCD2-ready-{row['slot'].upper()}.tar.pending"})
                with tempfile.TemporaryDirectory(dir=folder) as td:
                    archive = Path(td) / 'ready.tar'
                    store.download(ids[row['slot']], archive, row, max_bytes=MAX_STAGE_BYTES + 256 * 1024**2)
                    restore_stage(archive, work, identity)
                restored = True
            if os.environ.get('GITHUB_OUTPUT'):
                with open(os.environ['GITHUB_OUTPUT'], 'a') as out: out.write(f'restored={str(restored).lower()}\n')
            print(f'[READY] Compatible complete preparation restored: {restored}', flush=True)
        else:
            slot = 'b' if row and row.get('slot') == 'a' else 'a'
            store.check(ids[slot], {f'MCD2-ready-{slot.upper()}.tar.pending'})
            with tempfile.TemporaryDirectory(dir=folder) as td:
                archive = Path(td) / 'ready.tar'
                count, size = save_stage(work, archive, identity)
                print(f'[READY] Full prepared checkpoint: {count} files, {size:,} bytes', flush=True)
                total = archive.stat().st_size
                uploaded = store.upload(ids[slot], archive, progress=lambda p: print(json.dumps(
                    {'ev': 'progress', 'name': 'checkpoint upload', 'done': int(p * total), 'total': total, 'unit': 'B'}), flush=True))
            row = dict(uploaded, slot=slot, identity=identity, saved_at=utc(), files=count)
            store.put_json(ids['index'], {'schema': 1, 'latest': row})
            if store.get_json(ids['index']).get('latest') != row: raise ValueError('Preparation index readback failed')
            print('[READY] Full preparation durably verified before packaging', flush=True)
        return 0
    except Exception as exc:
        print(f'[RED] Preparation/recovery failed: {safe_failure(exc)}', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
