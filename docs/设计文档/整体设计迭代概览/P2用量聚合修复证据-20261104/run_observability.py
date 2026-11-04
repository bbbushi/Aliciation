"""Focused, explicitly authorized P2 diagnostics; default prepares without executing."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
HISTORICAL = HERE.parent / '隔离检测证据-20261104'


def strict_source(repo, relative):
    rel = Path(relative)
    allowed = (rel.parts and (rel.parts[0] in ('alice', 'web')
               or rel.parts[:2] == ('addons', 'claude_agent')
               or rel.as_posix() == 'config.example.toml'))
    if (rel.is_absolute() or '..' in rel.parts or not allowed
            or any(part.lower() == 'user' for part in rel.parts)):
        raise ValueError('Rejected source manifest path')
    target = repo / rel
    # Reject links before reading: repository containment alone would permit a
    # whitelisted name redirected into this checkout's real user directory.
    for part in [repo, *repo.parents]:
        if part.is_symlink() or getattr(part.stat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
            raise ValueError('Repository links/reparse points are not supported')
    probe = repo
    for component in rel.parts:
        probe = probe / component
        meta = probe.lstat()
        if probe.is_symlink() or getattr(meta, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
            raise ValueError('Source links/reparse points are not allowed')
    if target.resolve() != target.absolute() or repo not in target.resolve().parents:
        raise ValueError('Source canonical path differs from the whitelist')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--allow-changed-source', action='store_true')
    parser.add_argument('--only-case', choices=['zero_invalid_usage_matrix'])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--run', action='store_true')
    mode.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if os.name != 'nt' or sys.version_info < (3, 11):
        parser.error('Windows/Python 3.11+ required by the inherited evidence bundle')
    repo = args.repo.absolute()
    if repo.resolve() != repo:
        parser.error('Repository must be its canonical path')
    old = json.loads((HISTORICAL / 'source-manifest.json').read_text(encoding='utf-8'))
    data, files, differences = {}, [], []
    for item in old['files']:
        target = strict_source(repo, item['path'])
        raw = target.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        data[item['path']] = raw
        files.append({'path': item['path'], 'sha256': digest})
        if digest != item['sha256']:
            differences.append({'path': item['path'], 'historical_sha256': item['sha256'], 'actual_sha256': digest})
    if differences and not args.allow_changed_source:
        parser.error('Source changed; explicit --allow-changed-source required')
    run = Path(tempfile.mkdtemp(prefix='alice-p2-usage-'))
    for rel, raw in data.items():
        target = run / 'source' / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    scripts = [('check_support.py', HISTORICAL / 'check_support.py'),
               ('observability/check_observability.py', HISTORICAL / 'observability/check_observability.py'),
               ('usage_aggregation/check_usage_aggregation.py', HERE / 'check_usage_aggregation.py')]
    script_files = []
    for rel, origin in scripts:
        raw = origin.read_bytes()
        target = run / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        script_files.append({'path': rel, 'origin': str(origin), 'sha256': hashlib.sha256(raw).hexdigest()})
    guard_path = run / 'check_support.py'
    guard = guard_path.read_text(encoding='utf-8')
    marker = "LIVE_REPO = Path('D:/alice').resolve()"
    assert guard.count(marker) == 1
    guard = guard.replace(marker, 'LIVE_REPO = Path(' + repr(str(repo)) + ').resolve()')
    old_link_check = "            check(args[0]); check(args[1], True)"
    assert guard.count(old_link_check) == 1
    guard = guard.replace(old_link_check, "            check(args[0], True); check(args[1], True)")
    guard_path.write_text(guard, encoding='utf-8')
    manifest = {'source_root': str(repo), 'files': files,
                'snapshot_sha256': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}
    (run / 'source-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    (run / 'source-differences.json').write_text(json.dumps(differences, indent=2), encoding='utf-8')
    (run / 'script-manifest.json').write_text(json.dumps({'files': script_files,
        'runtime_guard_sha256': hashlib.sha256(guard_path.read_bytes()).hexdigest(),
        'guard_adjustments': ['bind actual live repository', 'rename/link/symlink source must also be writable inside probe']},
        indent=2), encoding='utf-8')
    (run / 'preparation.json').write_text(json.dumps({'run': str(run), 'source_files': len(files),
        'suites': ['usage_aggregation'] if args.only_case else ['observability', 'usage_aggregation'],
        'only_case': args.only_case, 'no_real_user_copied': True,
        'historical_evidence_unchanged': True}, indent=2), encoding='utf-8')
    print(json.dumps({'run': str(run), 'source_differences': differences, 'prepared_only': not args.run}, ensure_ascii=False), flush=True)
    if not args.run:
        return 0
    commands, cases, guards = [], [], []
    selected = ('usage_aggregation/check_usage_aggregation.py',) if args.only_case else (
        'observability/check_observability.py', 'usage_aggregation/check_usage_aggregation.py')
    for rel in selected:
        extra = ['--only-case', args.only_case] if args.only_case else []
        process = subprocess.run([sys.executable, '-I', '-S', '-B', str(run / rel), *extra], cwd=run,
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW)
        suite = rel.split('/')[0]
        (run / suite / 'stdout.txt').write_text(process.stdout, encoding='utf-8')
        (run / suite / 'stderr.txt').write_text(process.stderr, encoding='utf-8')
        commands.append({'script': rel, 'arguments': extra, 'exit_code': process.returncode})
        result_file = run / suite / 'results.json'
        if result_file.exists():
            result = json.loads(result_file.read_text(encoding='utf-8'))
            cases.extend({'suite': suite, **case} for case in result['cases'])
            guards.extend({'suite': suite, 'event': event} for event in result.get('guard_attempts', []))
        else:
            cases.append({'suite': suite, 'name': 'suite_result_missing', 'status': 'failed'})
    summary = {'run': str(run), 'commands': commands, 'cases': cases, 'source_snapshot_sha256': manifest['snapshot_sha256'],
               'passed': sum(c['status'] == 'passed' for c in cases), 'failed': sum(c['status'] == 'failed' for c in cases),
               'guard_attempts': guards, 'real_provider_calls': 0, 'real_application_services': 0}
    (run / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: summary[k] for k in ('run','passed','failed','guard_attempts','source_snapshot_sha256')}, ensure_ascii=False))
    return int(summary['failed'] > 0 or bool(guards) or any(c['exit_code'] for c in commands))


if __name__ == '__main__':
    raise SystemExit(main())
