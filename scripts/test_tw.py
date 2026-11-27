#!/usr/bin/env python3
"""Parallel test-suite entry point; named unittest selectors remain supported."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def _modules():
    from scripts.tests import test_guard, test_hardening, test_merge, test_quota, test_rebuild
    from scripts.tests import test_requests, test_root, test_screen, test_seat, test_setup, test_tasks, test_views
    return (test_setup, test_tasks, test_requests, test_root, test_merge, test_rebuild, test_views,
            test_guard, test_seat, test_screen, test_quota, test_hardening)


def _suite_for_selectors(selectors):
    modules = _modules()
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for selector in selectors:
        parts = selector.split(".")
        for module in modules:
            names = parts
            if parts[0].startswith("test_"):  # optional shard prefix: test_setup.Class[.method]
                if not module.__name__.endswith(parts[0]):
                    continue
                names = parts[1:]
            cls = getattr(module, names[0], None) if names else None
            if not isinstance(cls, type):
                continue
            owner = sys.modules.get(cls.__module__)
            if owner is None or getattr(owner, names[0], None) is not cls:
                continue
            selected = loader.loadTestsFromName(".".join(names), owner)
            if selected.countTestCases():
                suite.addTests(selected)
    return suite


def load_tests(loader, standard_tests, pattern):
    """Make unittest's module entry point execute the same cases as the shard runner."""
    suite = unittest.TestSuite()
    for module in _modules():
        suite.addTests(loader.loadTestsFromModule(module))
    return suite


if __name__ == "__main__" and sys.argv[1:]:
    suite = _suite_for_selectors(sys.argv[1:])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
elif __name__ == "__main__":
    from scripts.tests._runner import run_all
    raise SystemExit(run_all())
