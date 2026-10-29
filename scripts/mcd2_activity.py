"""Sanitized process/output activity for long build phases; no raw arguments."""
import collections
import math
from pathlib import Path
import subprocess
import time

ROLES = ('Packing IPA', 'Compiling Swift', 'Linking app', 'Compiling app',
         'Linking Metal library', 'Compiling Metal', 'Building iOS app',
         'Rewriting modules', 'Decoding shaders', 'Shader builder')


def role(args):
    exe = Path(args.split()[0]).name if args.split() else ''
    if 'pack_ipa.py' in args: return 'Packing IPA'
    if exe.startswith('swift'): return 'Compiling Swift'
    if exe in ('ld', 'ld64'): return 'Linking app'
    if exe.startswith('clang'): return 'Compiling app'
    if exe == 'metallib': return 'Linking Metal library'
    if exe == 'metal': return 'Compiling Metal'
    if exe == 'xcodebuild': return 'Building iOS app'
    if 'patch_nso.py' in args: return 'Rewriting modules'
    if 'uepak.py' in args: return 'Decoding shaders'
    if 'dmsh_build.py' in args: return 'Shader builder'
    return None


def elapsed(value):
    days, _, clock = value.rpartition('-')
    parts = [int(v) for v in clock.split(':')]
    seconds = 0
    for part in parts: seconds = seconds * 60 + part
    return int(days or 0) * 86400 + seconds


def processes(output, pid):
    rows, edges = {}, collections.defaultdict(list)
    for line in output.splitlines():
        cols = line.split(None, 4)
        if len(cols) != 5: continue
        try:
            child, parent, age, cpu = int(cols[0]), int(cols[1]), elapsed(cols[2]), float(cols[3])
        except ValueError: continue
        if not math.isfinite(cpu): continue
        rows[child] = (age, max(cpu, 0), role(cols[4]))
        edges[parent].append(child)
    pending, seen = [pid], set()
    while pending:
        current = pending.pop()
        if current in seen: continue
        seen.add(current); pending.extend(edges[current])
    stats = {}
    for child in seen:
        age, cpu, kind = rows.get(child, (0, 0, None))
        if kind is None: continue
        row = stats.setdefault(kind, {'role': kind, 'count': 0, 'oldest_s': 0, 'cpu_percent': 0})
        row['count'] += 1; row['oldest_s'] = max(row['oldest_s'], age); row['cpu_percent'] += cpu
    return [stats[k] for k in ROLES if k in stats]


def snapshot(pid, work, folder):
    try:
        output = subprocess.check_output(['ps', '-axo', 'pid=,ppid=,etime=,%cpu=,args='],
                                        text=True, timeout=5, stderr=subprocess.DEVNULL)
        tools = processes(output, pid)
    except (OSError, subprocess.SubprocessError):
        tools = []
    count, latest = 0, 0.0
    for path in (Path(work) / 'shader/ios').glob('b-*/*.metallib'):
        try:
            count += 1; latest = max(latest, path.stat().st_mtime)
        except OSError: pass
    sizes = {}
    for key, name in (('ipa_partial_bytes', 'MinecraftDungeonsII.ipa.partial'), ('ipa_bytes', 'MinecraftDungeonsII.ipa')):
        try:
            stat = (Path(folder) / name).stat(); sizes[key] = stat.st_size; latest = max(latest, stat.st_mtime)
        except OSError: sizes[key] = 0
    return {'active_tools': tools, 'activity': tools[0]['role'] if tools else 'No recognized child tool',
            'ios_library_files': count, 'latest_output_age_s': round(max(0, time.time() - latest)) if latest else None,
            **sizes}
