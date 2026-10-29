#!/usr/bin/env python3
"""Run separate observed phases around a durable completed-preparation boundary."""
import argparse
import os
from pathlib import Path
import sys

from mcd2_cloud import CloudSession
from mcd2_integrated_build import GAME, guard
from mcd2_observed_build import stage_observed
from mcd2_drive_transport import safe_failure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('restore', 'prepare', 'save', 'rehydrate', 'package', 'deliver'))
    parser.add_argument('folder', type=Path)
    parser.add_argument('--keys', type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    folder = args.folder.resolve(); folder.mkdir(parents=True, exist_ok=True)
    guard(repo)
    cloud = CloudSession(folder, {}, {'status': os.environ['MCD2_STATUS_FILE']}, cache_enabled=False)
    code = 1
    started = False
    try:
        cloud.start()
        started = True
        if args.phase == 'prepare':
            command = [sys.executable, str(repo / 'scripts/build_ipa_from_nsp.py'), str(folder),
                       '-k', str(args.keys), '--bootstrap-shaders', '--no-heap-check', '--platform', 'ios',
                       '--romfs', 'bundle', '--gpu', 'none', '--stop-after-shaders', '--progress', 'json']
            limit, name = 10800, 'ios-ipa-build'
        elif args.phase == 'deliver':
            command = [sys.executable, str(repo / 'scripts/mcd2_drive_output.py'), '--output',
                       os.environ['MCD2_OUTPUT_FILE'], '--ipa', str(folder / 'MinecraftDungeonsII.ipa')]
            limit, name = 1800, 'ios-deliver'
        else:
            command = [sys.executable, str(repo / 'scripts/mcd2_stages.py'), args.phase, str(folder), '--keys', str(args.keys)]
            limit, name = (3600 if args.phase == 'package' else 900), 'ios-' + args.phase
        logs = folder / 'private-logs'; logs.mkdir(exist_ok=True)
        result = stage_observed(name, command, limit, dict(os.environ, DM_GAME=GAME),
                                logs / (args.phase + '.log'), repo, folder, cloud=cloud)
        code = result['code']
        return code
    except Exception as exc:
        print(f'[RED] Observed phase failed: {safe_failure(exc)}', flush=True)
        return 1
    finally:
        if started:
            cloud.close(code, success_state={'prepare': 'prepared', 'save': 'checkpoint_saved',
                        'restore': 'restore_checked', 'rehydrate': 'resources_ready', 'package': 'packaged',
                        'deliver': 'delivered'}[args.phase])


if __name__ == '__main__':
    raise SystemExit(main())
