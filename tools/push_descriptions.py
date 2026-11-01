#!/usr/bin/env python3
"""Upload one exact-key descriptions JSON batch to the bot server; preview by default."""
import argparse
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bulk_prices as bp
from bulk_descriptions import description

ROOT = '/home/ubuntu/ctv-bot'
OPS = ROOT + '/description-ops'


def run(command):
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=300)
    if result.returncode:
        # Remote utilities redact secrets; SSH errors identify connectivity problems.
        raise RuntimeError(result.stderr.strip() or 'Remote operation failed; inspect batch receipt before retrying')
    return result.stdout.strip()


def validate_batch(value):
    if not re.fullmatch(re.escape(OPS) + r'/batch-[A-Za-z0-9]+', value):
        raise ValueError('Unexpected remote batch directory')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--changes', type=Path, required=True)
    parser.add_argument('--identity', type=Path, default=Path.home() / '.ssh/ssh.pem')
    parser.add_argument('--host', default='ubuntu@54.169.17.170')
    parser.add_argument('--execute', action='store_true', help='Apply the explicit submitted content after automatic preview')
    args = parser.parse_args(argv)
    try:
        if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*', args.host):
            raise ValueError('Use user@hostname for --host')
        changes = bp.read_json(args.changes)
        if not isinstance(changes, list) or not changes:
            raise ValueError('Changes must be a nonempty JSON array')
        seen = set()
        fields = {'description_vi', 'description_en'}
        for change in changes:
            bp.mapping(change)
            key = bp.product_key(change.get('key'))
            if key in seen or set(change) - (fields | {'key', 'expected_name'}) or not set(change) & fields:
                raise ValueError('Invalid or duplicate description change')
            seen.add(key)
            if 'expected_name' in change:
                bp.text(change['expected_name'])
            for field in fields & set(change):
                value = change[field]
                description(value)
        common = ['-i', str(args.identity.resolve()), '-o', 'BatchMode=yes', '-o',
                  'ConnectTimeout=12', '-o', 'StrictHostKeyChecking=yes']
        ssh = ['ssh', *common, args.host]
        batch = validate_batch(run([*ssh, f'umask 077 && mkdir -p {OPS} && chmod 700 {OPS} && mktemp -d {OPS}/batch-XXXXXXXX']))
        print('Batch: ' + batch)
        # Freeze validated input, rather than uploading a file that could change after validation.
        with tempfile.TemporaryDirectory(prefix='bot-descriptions-') as directory:
            payload = Path(directory) / 'changes.json'
            payload.write_bytes(bp.json_bytes(changes))
            run(['scp', *common, str(payload), args.host + ':' + batch + '/changes.json'])
        q = shlex.quote
        cli = f'python3 {ROOT}/tools/bulk_descriptions.py'
        base = f'--config {ROOT}/config.env --database /home/ubuntu/ctv-bot-data/bot_data.json'
        commands = [f'umask 077', f'chmod 600 {q(batch)}/changes.json',
                    f'{cli} export {base} --output {q(batch)}/catalog.json',
                    f'{cli} plan --snapshot {q(batch)}/catalog.json --changes {q(batch)}/changes.json --output {q(batch)}/plan.json > {q(batch)}/plan-output.json',
                    f'{cli} apply {base} --plan {q(batch)}/plan.json > {q(batch)}/preview.json']
        if args.execute:
            commands.append(f'{cli} apply {base} --plan {q(batch)}/plan.json --service ctv-bot --execute --backup {q(batch)}/before.json --receipt {q(batch)}/receipt.json > {q(batch)}/execute-output.json')
            commands.append('systemctl is-active ctv-bot')
        commands.append(f'cat {q(batch)}/' + ('receipt.json' if args.execute else 'preview.json'))
        print(run([*ssh, ' && '.join(commands)]))
        return 0
    except (bp.PriceError, ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print('Error: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
