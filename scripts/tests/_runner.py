"""Parallel runner shared by the full-suite entry point and merge shard convention."""
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parents[1]
FILES = sorted(TESTS.glob("test_*.py"))


def _run(path):
    with tempfile.TemporaryFile() as output:
        result = subprocess.run([sys.executable, str(path)], cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
        output.seek(0)
        text = output.read().decode("utf-8", "replace")
    return path.name, result.returncode, text


def run_all():
    if not FILES:
        print("no test shard files found", file=sys.stderr)
        return 2
    jobs = max(1, int(os.environ.get("TW_TEST_JOBS", "4")))
    started = time.monotonic()
    done = 0
    failures = []
    cases = 0
    print(f"test shards: {len(FILES)} files, {jobs} parallel jobs", flush=True)
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        pending = {pool.submit(_run, path): path for path in FILES}
        for future in as_completed(pending):
            name, code, output = future.result()
            done += 1
            match = re.search(r"Ran (\d+) tests? in", output)
            cases += int(match.group(1)) if match else 0
            print(f"--- {done}/{len(FILES)} done · elapsed {int(time.monotonic()-started)}s · {name} ---")
            sys.stdout.write(output)
            if output and not output.endswith("\n"):
                sys.stdout.write("\n")
            if code:
                failures.append(name)
    if failures:
        print(f"FAILED {len(failures)} shard(s): {', '.join(failures)}; {cases} cases")
        return 1
    print(f"ALL {cases} CASES PASSED")
    print(f"total wall time: {time.monotonic()-started:.2f}s")
    return 0
