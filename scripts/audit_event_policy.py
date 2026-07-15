from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.config.profile_loader import ProfileLoader
from src.review.event_policy_auditor import build_event_policy_audit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audita la clasificación y seguridad de eventos usando un "
            "screen_index existente, sin abrir el ERP."
        )
    )
    parser.add_argument("--profile", default="configs/cbmm.yaml")
    parser.add_argument(
        "--screen-index",
        default="data/processed/structural/screen_index.json",
    )
    parser.add_argument(
        "--output",
        default="data/processed/structural/event_policy_audit.json",
    )
    return parser.parse_args()


def build_audit(
    profile: dict[str, Any],
    screen_index: dict[str, Any],
) -> dict[str, Any]:
    # Alias conservado para compatibilidad con pruebas y usos anteriores.
    return build_event_policy_audit(profile, screen_index)


def main() -> int:
    args = parse_args()
    profile = ProfileLoader(args.profile).load()

    screen_index_path = Path(args.screen_index)
    if not screen_index_path.exists():
        raise FileNotFoundError(f"No existe screen_index: {screen_index_path}")

    screen_index = json.loads(screen_index_path.read_text(encoding="utf-8"))
    audit = build_audit(profile, screen_index)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("Auditoría de política de eventos completada.")
    print(f"Pantallas: {audit['screens_count']}")
    print(f"Decisiones: {audit['decision_totals']}")
    print(f"Categorías: {audit['category_totals']}")
    print(f"Pipeline: {audit.get('pipeline_totals', {})}")
    print(
        "Exclusiones de ejecución: "
        f"{audit.get('selection_exclusion_totals', {})}"
    )
    print(f"Resultado: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
