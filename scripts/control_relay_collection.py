#!/usr/bin/env python3
"""Control an existing, explicitly identified relay collection permission.

Only changes the private relay control, with a backup and atomic replacement.
Never changes the published DA catalog or performs an OPC operation.
"""
# SPDX-License-Identifier: MIT
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--machine-id', required=True)
    parser.add_argument('--module-id', required=True)
    parser.add_argument('--interval-s', type=int, default=1,
                        help='minimum interval for continuous collection (default: 1)')
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--continuous', action='store_true')
    action.add_argument('--pause', action='store_true')
    action.add_argument('--duration-s', type=int)
    args = parser.parse_args()
    if not 1 <= args.interval_s <= 3600:
        parser.error('--interval-s must be between 1 and 3600')
    if args.duration_s is not None and not 1 <= args.duration_s <= 300:
        parser.error('--duration-s must be between 1 and 300')
    path = args.data_dir / 'relay-read-windows.json'
    original = path.read_bytes()
    control = json.loads(original)
    if control.get('schema_version') != 1 or not isinstance(control.get('windows'), dict):
        parser.error('invalid existing collection control; preserved')
    entry = control['windows'].get(args.module_id)
    if not isinstance(entry, dict) or entry.get('machine_id') != args.machine_id:
        parser.error('existing module/machine permission does not match; preserved')
    replacement = {'machine_id': args.machine_id}
    if args.duration_s is not None:
        now = int(time.time() * 1000)
        replacement.update(mode='bounded', not_before_unix_ms=now,
                           until_unix_ms=now + args.duration_s * 1000)
    else:
        replacement.update(mode='continuous', enabled=args.continuous, interval_s=args.interval_s)
    control['windows'][args.module_id] = replacement
    backup_dir = args.data_dir / 'backups'
    backup_dir.mkdir(exist_ok=True, mode=0o700)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = backup_dir / ('relay-collection-' + stamp + '-' + uuid.uuid4().hex + '.json')
    with backup.open('xb') as file:
        os.chmod(backup, 0o600)
        file.write(original)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=args.data_dir,
                                         prefix='.relay-control-', delete=False) as file:
            temp = Path(file.name)
            json.dump(control, file, indent=2)
            file.write('\n')
            file.flush()
            os.fsync(file.fileno())
        if path.read_bytes() != original:
            parser.error('control changed concurrently; retry after inspection')
        os.replace(temp, path)
        temp = None
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    print(json.dumps({'backup': str(backup), 'module_id': args.module_id,
                      'permission': replacement}))


if __name__ == '__main__':
    main()
