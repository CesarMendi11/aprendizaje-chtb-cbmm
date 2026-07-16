from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.knowledge.canonical import ArtifactLoadError, CanonicalKnowledgeBuilder, CanonicalKnowledgeExporter, CanonicalKnowledgeValidator

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Construye el conocimiento canónico del ERP")
    parser.add_argument("--profile", default="configs/cbmm.yaml")
    parser.add_argument("--output-dir", default="data/processed/canonical")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--pretty", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args(argv)
    builder = CanonicalKnowledgeBuilder(ROOT)
    try:
        knowledge = builder.build_from_paths(args.profile)
        issues = CanonicalKnowledgeValidator().validate(knowledge)
        report = builder.build_report(knowledge, issues)
        if args.strict and knowledge.build_warnings:
            print(f"Error estricto: {len(knowledge.build_warnings)} advertencias", file=sys.stderr); return 2
        CanonicalKnowledgeExporter().export(knowledge, ROOT / args.output_dir, pretty=args.pretty, build_report=report)
    except (ArtifactLoadError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr); return 1
    print("Artefactos fuente: " + ", ".join(knowledge.source_artifacts))
    print(f"knowledge_version: {knowledge.knowledge_version}")
    print("Conteos: " + ", ".join(f"{key}={value}" for key, value in knowledge.statistics.items()))
    print(f"Advertencias: {len(knowledge.build_warnings)}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
