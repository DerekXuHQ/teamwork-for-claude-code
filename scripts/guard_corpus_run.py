#!/usr/bin/env python3
"""Run bash-guard against the checked-in adversarial command corpus (T51).

Each case goes to `tw.py bash-guard` as the hook's stdin. A case passes only when the decision matches, the hook
exits 0 with valid output and nothing on stderr, and the guard logged no internal error for it (a crash prints
nothing, which would otherwise look like "allow")."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

SCRUB = ("TEAMWORK_DIR", "TW_GUARD_LOG", "TW_TEST_GUARD_FAULT")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tw', type=Path, default=Path(__file__).with_name('tw.py'))
    parser.add_argument('--corpus', type=Path, default=Path(__file__).with_name('guard_corpus.json'))
    parser.add_argument('--json', action='store_true', help='print the complete result as JSON')
    args = parser.parse_args()
    args.tw = args.tw.resolve()
    args.corpus = args.corpus.resolve()

    corpus = json.loads(args.corpus.read_text(encoding='utf-8'))
    known = set(corpus.get('policies', []))
    results = []
    with tempfile.TemporaryDirectory(prefix='tw-guard-corpus-') as root:
        home = Path(root).resolve() / 'home'
        home.mkdir()
        log = Path(root).resolve() / 'guard-errors.jsonl'
        env = {k: v for k, v in os.environ.items() if k not in SCRUB}
        env['HOME'] = str(home)
        env['TW_GUARD_LOG'] = str(log)
        if os.environ.get('TW_TEST_GUARD_FAULT') and os.environ.get('TW_CORPUS_KEEP_FAULT'):
            env['TW_TEST_GUARD_FAULT'] = os.environ['TW_TEST_GUARD_FAULT']  # self-test: a crashing guard must fail
        projects = {}
        for policy in sorted(known):
            proj = Path(root).resolve() / ('project-' + policy)
            (proj / '.teamwork').mkdir(parents=True)
            (proj / '.teamwork' / 'state.json').write_text(json.dumps({'prefs': {'perm_policy': policy}}),
                                                          encoding='utf-8')
            projects[policy] = str(proj)
        plain = Path(root).resolve() / 'project-none'  # no .teamwork here; the temp root has none above it either
        plain.mkdir()
        for case in corpus['cases']:
            policy = case.get('policy')
            cmd = case['cmd'].replace('@HOME@', str(home))
            payload = json.dumps({'tool_name': 'Bash', 'tool_input': {'command': cmd}})
            before = log.stat().st_size if log.exists() else 0
            error = None
            got = None
            if policy and policy not in known:
                error = f'unknown policy {policy!r} (known: {sorted(known)})'
            else:
                run = subprocess.run([sys.executable, str(args.tw), 'bash-guard'], input=payload, capture_output=True,
                                     text=True, env=env, cwd=projects.get(policy, str(plain)))
                if run.returncode != 0:
                    error = f'exit {run.returncode}: {run.stderr.strip() or run.stdout.strip()}'
                elif run.stderr.strip():
                    error = 'stderr: ' + run.stderr.strip()[:300]
                elif run.stdout.strip():
                    try:
                        got = json.loads(run.stdout)['hookSpecificOutput']['permissionDecision']
                    except (ValueError, KeyError, TypeError):
                        error = 'invalid hook output: ' + run.stdout.strip()[:300]
                if error is None and log.exists() and log.stat().st_size > before:
                    error = 'guard error logged: ' + log.read_text(encoding='utf-8')[before:].strip()[:300]
            decision = got if got is not None else 'allow'
            result = {'id': case['id'], 'expect': case['expect'], 'got': decision, 'policy': policy,
                      'pass': error is None and decision == case['expect']}
            if error:
                result['error'] = error
            results.append(result)

    failed = sum(not item['pass'] for item in results)
    tested = {'tw': args.tw.name, 'tw_sha256': hashlib.sha256(args.tw.read_bytes()).hexdigest(),
              'corpus_sha256': hashlib.sha256(args.corpus.read_bytes()).hexdigest()}
    if args.json:
        print(json.dumps({'version': 1, 'tested': tested, 'results': results, 'cases': len(results), 'failed': failed},
                         ensure_ascii=False, indent=2))
    else:
        for item in results:
            print(f"{'PASS' if item['pass'] else 'FAIL'} {item['id']} {item['expect']} {item['got']}"
                  + (f"  ({item['error']})" if item.get('error') else ''))
        print(f"tw.py sha256 {tested['tw_sha256'][:12]}, corpus sha256 {tested['corpus_sha256'][:12]}")
        print(f'corpus: {len(results)} cases, {failed} failed')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
