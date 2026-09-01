from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
SRC_ROOT = PROJECT_ROOT / "src"
EXPECTED_GROUPS = {
    "audit",
    "certification",
    "common",
    "experiments",
    "inspect",
    "operations",
    "pipeline",
    "runtime",
    "status",
    "tools",
}


def test_scripts_are_grouped_by_responsibility():
    top_level_python = {path.name for path in SCRIPTS_ROOT.glob("*.py")}
    assert top_level_python == {"__init__.py"}

    groups = {
        path.name
        for path in SCRIPTS_ROOT.iterdir()
        if path.is_dir() and not path.name.startswith("__")
    }
    assert groups == EXPECTED_GROUPS
    assert all((SCRIPTS_ROOT / group / "__init__.py").is_file() for group in EXPECTED_GROUPS)


def test_productive_src_never_imports_scripts_package():
    violations: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("scripts"):
                violations.append(path.relative_to(PROJECT_ROOT).as_posix())
            if isinstance(node, ast.Import):
                if any(alias.name.startswith("scripts") for alias in node.names):
                    violations.append(path.relative_to(PROJECT_ROOT).as_posix())
    assert violations == []
