"""Run the isolated Windows COM/telemetry test within one SSH logon session."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
from opc_da_bridge.management.deployment import SshLabChannel

parser = argparse.ArgumentParser()
parser.add_argument('--target', type=Path, required=True)
parser.add_argument('--executable', type=Path, required=True)
args = parser.parse_args()
target = json.loads(args.target.read_text())
channel = SshLabChannel(args.target.parent)
channel.validate(target)
root = target['agent_dir']
digest = hashlib.sha256(args.executable.read_bytes()).hexdigest()
script = "& '" + root + "\\test_rust_da_source_runtime.ps1' -LaboratoryDirectory '" + root + "' -ExpectedSha256 '" + digest + "'"
command = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand ' + base64.b64encode(script.encode('utf-16le')).decode()
reply = subprocess.run(['ssh'] + channel.options(target) + [target['username'] + '@' + target['host'], command], capture_output=True, timeout=80)
output = reply.stdout.decode('utf-8-sig')
print(output)
if reply.returncode or 'SOURCE_RUNTIME_WINDOWS_PASS' not in output:
    raise SystemExit('Windows laboratory test failed: ' + reply.stderr.decode(errors='replace')[:1000])
