from __future__ import annotations

import argparse
from pathlib import Path

from src.knowledge.canonical import CanonicalKnowledgeRepository, CanonicalKnowledgeValidator

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument("--knowledge", default="data/processed/canonical/knowledge.json"); args=parser.parse_args(argv)
    try: repository=CanonicalKnowledgeRepository(ROOT/args.knowledge)
    except Exception as exc: print(f"ERROR load: {type(exc).__name__}: {exc}"); return 1
    issues=CanonicalKnowledgeValidator().validate(repository.knowledge)
    for issue in issues: print(f"{issue.severity.value.upper()} {issue.code}: {issue.message}")
    errors=sum(item.severity == "error" for item in issues)
    print(f"Validación: errors={errors}, warnings={sum(item.severity == 'warning' for item in issues)}")
    return 1 if errors else 0


if __name__ == "__main__": raise SystemExit(main())
