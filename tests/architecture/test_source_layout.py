from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
PACKAGE_ROOT = SRC_ROOT / "erp_assistant"

EXPECTED_TOP_LEVEL = {
    "acquisition",
    "api",
    "config",
    "integrations",
    "orchestration",
    "persistence",
    "projections",
    "retrieval",
    "semantic",
    "structural",
}


def test_src_uses_standard_package_layout():
    assert (PACKAGE_ROOT / "__init__.py").is_file()

    unexpected_python = [
        path.relative_to(SRC_ROOT).as_posix()
        for path in SRC_ROOT.iterdir()
        if path.is_file() and path.suffix == ".py"
    ]
    assert unexpected_python == []


def test_productive_package_is_grouped_by_architectural_responsibility():
    groups = {
        path.name
        for path in PACKAGE_ROOT.iterdir()
        if path.is_dir() and not path.name.startswith("__")
    }
    assert groups == EXPECTED_TOP_LEVEL


def test_obsolete_empty_scaffolds_do_not_return():
    forbidden = (
        SRC_ROOT / "semantic",
        SRC_ROOT / "tickets",
        SRC_ROOT / "review",
        SRC_ROOT / "database",
        SRC_ROOT / "pipeline",
        SRC_ROOT / "hybrid",
        SRC_ROOT / "analysis",
        SRC_ROOT / "knowledge",
    )
    assert all(not path.exists() for path in forbidden)
