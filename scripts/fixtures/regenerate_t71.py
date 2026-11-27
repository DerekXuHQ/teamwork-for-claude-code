#!/usr/bin/env python3
"""Compatibility entry point; use regenerate.py for fixture generation."""
from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("regenerate.py")), run_name="__main__")
