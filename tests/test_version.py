"""The package version is set in two places; a release must not ship with them out of step."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import tne_sdk

_ROOT = Path(__file__).resolve().parent.parent


def test_init_version_matches_pyproject():
    pyproject = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    assert match, "no version in pyproject.toml"
    assert tne_sdk.__version__ == match.group(1)
