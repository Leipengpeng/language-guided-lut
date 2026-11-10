"""Packaged fitting adapters for the six-method comparison."""
from pathlib import Path
import os


def backend_root() -> Path:
    return Path(os.environ["COLOR_MATCH_LUT_BACKEND_ROOT"])
