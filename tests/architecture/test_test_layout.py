from __future__ import annotations

from pathlib import Path


TESTS_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_GROUPS = {
    "api",
    "architecture",
    "canonical",
    "config",
    "crawler",
    "database",
    "fixtures",
    "governance",
    "hybrid",
    "pipeline",
    "projections",
    "scripts",
    "semantic",
}


def test_tests_are_grouped_by_system_responsibility():
    assert list(TESTS_ROOT.glob("test_*.py")) == []

    groups = {
        path.name
        for path in TESTS_ROOT.iterdir()
        if path.is_dir() and not path.name.startswith("__")
    }
    assert groups == EXPECTED_GROUPS

    assert all((TESTS_ROOT / group / "__init__.py").is_file() for group in EXPECTED_GROUPS)
