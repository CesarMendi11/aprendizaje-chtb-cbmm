from __future__ import annotations

from pathlib import Path


TESTS_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_GROUPS = {
    "acquisition",
    "api",
    "architecture",
    "certification",
    "config",
    "fixtures",
    "orchestration",
    "persistence",
    "projections",
    "retrieval",
    "scripts",
    "semantic",
    "structural",
}


def test_tests_are_grouped_by_system_responsibility():
    assert list(TESTS_ROOT.glob("test_*.py")) == []

    groups = {
        path.name
        for path in TESTS_ROOT.iterdir()
        if path.is_dir() and not path.name.startswith("__")
    }
    assert groups == EXPECTED_GROUPS

    package_groups = EXPECTED_GROUPS - {"certification"}
    assert all((TESTS_ROOT / group / "__init__.py").is_file() for group in package_groups)


def test_structural_tests_are_split_by_responsibility():
    structural_root = TESTS_ROOT / "structural"
    groups = {
        path.name
        for path in structural_root.iterdir()
        if path.is_dir() and not path.name.startswith("__")
    }
    assert groups == {"canonical", "governance"}
