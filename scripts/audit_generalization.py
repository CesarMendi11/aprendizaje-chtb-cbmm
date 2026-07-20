from __future__ import annotations

import argparse
import ast
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = ("src", "scripts", "tests", "docs", "configs", "data")
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".html", ".txt"}
SPECIFIC_TERMS = (
    "reten" + "ciones",
    "cuentas" + "xcobrar",
    "cuentas " + "por cobrar",
    "cb" + "mm",
    "bom" + "beros",
    "ma" + "chala",
    "/admin/cuentas" + "xcobrar/reten" + "ciones",
    "erp:f02521b69878" + "eec36e024200",
    "screen:4c3175a29f8" + "bb4dcf439da9c",
    "ae2e518e" + "3fb1ef57",
)
UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def build_parser():
    parser = argparse.ArgumentParser(description="Audita dependencias específicas de un ERP")
    parser.add_argument("--pretty", action="store_true")
    return parser


def category(path: Path) -> str:
    top = path.parts[0]
    if top == "tests":
        return "B_test"
    if top == "docs":
        return "C_documentation"
    if top == "data":
        return "D_generated_data"
    if top == "configs":
        return "E_instance_configuration"
    return "A_productive_logic"


def audit(root: Path = ROOT) -> dict:
    matches = []
    violations = []
    for top in SCAN_ROOTS:
        base = root / top
        if not base.exists():
            continue
        for path in sorted(item for item in base.rglob("*") if item.is_file()):
            relative = path.relative_to(root)
            if (
                any(part.startswith(".") for part in relative.parts)
                or path.suffix not in TEXT_SUFFIXES
            ):
                continue
            source = relative.as_posix() if top == "data" else _safe_read(path)
            lowered = source.casefold()
            found = sorted({term for term in SPECIFIC_TERMS if term in lowered})
            if not found:
                continue
            item_category = category(relative)
            if item_category == "A_productive_logic" and _is_instance_profile_reference(source):
                item_category = "E_instance_configuration"
            elif (
                item_category == "A_productive_logic"
                and path.suffix == ".py"
                and not _productive_violations(path, root)
            ):
                item_category = "C_documentation"
            matches.append(
                {"category": item_category, "path": relative.as_posix(), "match_count": len(found)}
            )

    for top in ("src", "scripts"):
        for path in sorted((root / top).rglob("*.py")):
            violations.extend(_productive_violations(path, root))

    counts = Counter(item["category"] for item in matches)
    return {
        "status": "failed" if violations else "passed",
        "matches_by_category": dict(sorted(counts.items())),
        "matches": matches,
        "productive_hardcodes": violations,
    }


def _safe_read(path: Path) -> str:
    if path.name.casefold().startswith(".env") or path.stat().st_size > 5_000_000:
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _is_instance_profile_reference(source: str) -> bool:
    lowered = source.casefold()
    return "configs/" in lowered and (".yaml" in lowered or ".yml" in lowered)


def _productive_violations(path: Path, root: Path) -> list[dict[str, str]]:
    source = _safe_read(path)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [{"path": path.relative_to(root).as_posix(), "reason": "invalid_python"}]
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    results = []
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Constant)
            or not isinstance(node.value, str)
            or id(node) in docstrings
        ):
            continue
        lowered = node.value.casefold()
        if _is_instance_profile_reference(node.value):
            continue
        if any(term in lowered for term in SPECIFIC_TERMS) or UUID_RE.search(node.value):
            results.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "reason": "specific_literal",
                }
            )
    return results


def main(argv=None):
    args = build_parser().parse_args(argv)
    report = audit()
    print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 1 if report["productive_hardcodes"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
