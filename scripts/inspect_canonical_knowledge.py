from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.knowledge.canonical import CanonicalKnowledgeRepository

ROOT=Path(__file__).resolve().parents[1]


def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument("--knowledge", default="data/processed/canonical/knowledge.json"); args=parser.parse_args(argv)
    try: repo=CanonicalKnowledgeRepository(ROOT/args.knowledge)
    except Exception as exc: print(f"Error: {type(exc).__name__}: {exc}"); return 1
    kb=repo.knowledge
    print(f"schema_version: {kb.schema_version}\nknowledge_version: {kb.knowledge_version}\nERP: {kb.erp_system.name} ({kb.erp_system.slug})")
    for key, value in kb.statistics.items(): print(f"{key}: {value}")
    pending=sum(getattr(item, "review_status", None) == "pending_review" for collection in (kb.modules,kb.screens,kb.ui_states,kb.fields,kb.controls,kb.tables,kb.table_columns,kb.links,kb.events,kb.transitions) for item in collection)
    print(f"pending_review: {pending}\nadvertencias: {len(kb.build_warnings)}")
    print(f"rutas_sin_modulo: {sum(item.module_id is None for item in kb.screens)}")
    print(f"transiciones_incompletas: {sum(item.event_id is None for item in kb.transitions)}")
    for module in kb.modules:
        print(f"\n[{module.name}]")
        for screen in repo.get_module_screens(module.id): print(f"  {screen.title} — {screen.route}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
