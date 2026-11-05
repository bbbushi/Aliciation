"""Recreate this explicitly authorized, isolated diagnostic run in a fresh temp directory.

No live configuration/data is copied. Known failures stay failures; this is not CI.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
SCRIPTS = ['check_support.py', 'run_static.py', 'permissions/check_permissions.py',
           'windows/run_windows.py', 'windows/controlled_adapter.py',
           'recovery/run_recovery.py', 'recovery/crash_helper.py',
           'persona/run_persona_checks.py', 'history/run_history.py',
           'observability/check_observability.py']
STAGES = ['after_prepared', 'after_first_insert', 'after_marker_before_commit',
          'after_db_commit', 'after_file_replace', 'after_file_journal',
          'reset_partial_move', 'reset_after_archive', 'reset_fresh_db_without_epoch',
          'reset_after_install']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=HERE.parents[3])
    parser.add_argument('--allow-changed-source', action='store_true',
                        help='Explicitly test a newer snapshot; retain the differences in the new run.')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if os.name != 'nt' or sys.version_info < (3, 11):
        parser.error('This evidence bundle requires Windows and Python 3.11+.')
    repo = args.repo.resolve()
    original = json.loads((HERE / 'source-manifest.json').read_text(encoding='utf-8'))
    files, differences, data = [], [], {}
    for item in original['files']:
        rel = Path(item['path'])
        allowed = (rel.parts[0] in ('alice', 'web') or rel.parts[:2] == ('addons', 'claude_agent')
                   or rel.as_posix() == 'config.example.toml')
        if rel.is_absolute() or '..' in rel.parts or not allowed:
            raise ValueError('Unexpected manifest path')
        target = (repo / rel).resolve()
        if repo not in target.parents:
            raise ValueError('Manifest source is outside the repository')
        raw = target.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        files.append({'path': item['path'], 'sha256': digest})
        data[item['path']] = raw
        if digest != item['sha256']:
            differences.append({'path': item['path'], 'original': item['sha256'], 'actual': digest})
    if differences and not args.allow_changed_source:
        print(json.dumps({'source_changed': differences}, ensure_ascii=False, indent=2))
        parser.error('Source differs; use --allow-changed-source only when intentionally testing a new revision.')
    run = Path(tempfile.mkdtemp(prefix='alice-checks-replay-'))
    for rel, raw in data.items():
        target = run / 'source' / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    for rel in SCRIPTS:
        target = run / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HERE / rel, target)
    # Protect the explicitly selected live repository in the temporary run.
    helper = run / 'check_support.py'
    guard = helper.read_text(encoding='utf-8')
    guard = guard.replace("LIVE_REPO = Path('D:/alice').resolve()",
                          'LIVE_REPO = Path(' + repr(str(repo)) + ').resolve()')
    helper.write_text(guard, encoding='utf-8')
    (run / 'source-manifest.json').write_text(json.dumps({'source_root': str(repo), 'files': files},
                                                      ensure_ascii=False, indent=2), encoding='utf-8')
    (run / 'source-differences.json').write_text(json.dumps(differences, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Isolated run: ' + str(run), flush=True)
    if args.prepare_only:
        return 0
    commands = []
    def launch(rel, *rest, expected=0):
        process = subprocess.run([sys.executable, '-I', '-S', '-B', str(run / rel), *rest],
                                 cwd=run, creationflags=subprocess.CREATE_NO_WINDOW)
        commands.append({'script': rel, 'arguments': list(rest), 'exit_code': process.returncode,
                         'expected_exit_code': expected})
        return process.returncode == expected
    for rel in ['run_static.py', 'permissions/check_permissions.py', 'windows/run_windows.py',
                'recovery/run_recovery.py', 'persona/run_persona_checks.py', 'history/run_history.py',
                'observability/check_observability.py']:
        launch(rel)
    crash_items = []
    suffix = 'replay_' + str(time.time_ns())
    for stage in STAGES:
        if not launch('recovery/crash_helper.py', 'crash', stage, suffix, expected=86):
            raise RuntimeError('Crash injection did not reach its intended boundary: ' + stage)
        launch('recovery/crash_helper.py', 'verify', stage, suffix)
        result = run / 'recovery' / ('hard_' + suffix + '_' + stage) / 'verification.json'
        crash_items.append(json.loads(result.read_text(encoding='utf-8')))
    (run / 'recovery' / 'crash_results.json').write_text(json.dumps({
        'suite': 'recovery_hard_exit', 'cases': crash_items,
        'passed': sum(x['status'] == 'passed' for x in crash_items),
        'failed': sum(x['status'] == 'failed' for x in crash_items)}, ensure_ascii=False, indent=2), encoding='utf-8')
    node = shutil.which('node')
    if node:
        for rel in ('web/claude-gate.js', 'web/worker-tasks.js'):
            code = subprocess.run([node, '--check', str(run / 'source' / rel)],
                                  cwd=run, creationflags=subprocess.CREATE_NO_WINDOW).returncode
            commands.append({'script': rel, 'exit_code': code, 'expected_exit_code': 0})
    else:
        commands.append({'script': 'JavaScript syntax', 'status': 'not_run', 'reason': 'node unavailable'})
    (run / 'replay-commands.json').write_text(json.dumps(commands, ensure_ascii=False, indent=2), encoding='utf-8')
    failures = []
    for rel in ('permissions', 'windows', 'recovery', 'persona', 'history', 'observability'):
        result_file = run / rel / 'results.json'
        if not result_file.exists():
            failures.append(rel + ': no results')
        else:
            result = json.loads(result_file.read_text(encoding='utf-8-sig'))
            failures.extend(rel + '/' + case['name'] for case in result['cases'] if case['status'] == 'failed')
    bad_exit = any(c.get('exit_code') != c.get('expected_exit_code') for c in commands if 'exit_code' in c)
    print(json.dumps({'run': str(run), 'failed_cases': failures, 'source_differences': len(differences)},
                     ensure_ascii=False, indent=2))
    return int(bool(failures or bad_exit or any(x['status'] == 'failed' for x in crash_items)))


if __name__ == '__main__':
    raise SystemExit(main())
