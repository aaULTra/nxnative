#!/usr/bin/env python3
"""Exercise the actual multi-chunk upload before spending time on game builds."""
import os
from pathlib import Path
import sys
import tempfile

from mcd2_cloud import DriveStore
from mcd2_drive_transport import safe_failure


def main():
    try:
        store = DriveStore()
        ident = os.environ['MCD2_PROBE_FILE']
        store.check(ident, {'MCD2-storage-probe.bin'})
        with tempfile.TemporaryDirectory(dir=os.environ.get('RUNNER_TEMP')) as td:
            path = Path(td) / 'probe.bin'
            # 32 MiB is one upload chunk: a smaller probe misses the HTTP 308
            # Resume Incomplete response that broke the original IPA delivery.
            with path.open('wb') as stream:
                for _ in range(40): stream.write(b'\x42' * 1024**2)
                stream.write(b'!')
            store.upload(ident, path)
        print('[DRIVE PROBE] Multi-chunk write + stored size/MD5 verified', flush=True)
        return 0
    except Exception as exc:
        print(f'[RED] Drive multi-chunk probe failed: {safe_failure(exc)}', flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
