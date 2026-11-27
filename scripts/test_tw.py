#!/usr/bin/env python3
"""Parallel test-suite entry point; named unittest selectors remain supported."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if __name__ == "__main__" and sys.argv[1:]:
    from scripts.tests import test_guard, test_hardening, test_merge, test_quota, test_rebuild
    from scripts.tests import test_requests, test_root, test_screen, test_seat, test_setup, test_tasks, test_views
    modules = (test_setup, test_tasks, test_requests, test_root, test_merge, test_rebuild, test_views,
               test_guard, test_seat, test_screen, test_quota, test_hardening)
    suite = unittest.TestSuite()
    for selector in sys.argv[1:]:
        parts = selector.split(".")
        for module in modules:
            if len(parts) > 1 and not hasattr(module, parts[0]):
                continue
            selected = unittest.defaultTestLoader.loadTestsFromName(selector, module)
            if selected.countTestCases():
                suite.addTests(selected)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
elif __name__ == "__main__":
    from scripts.tests._runner import run_all
    raise SystemExit(run_all())
