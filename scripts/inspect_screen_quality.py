from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspecciona títulos y regiones del screen_index más reciente."
    )
    parser.add_argument(
        "--screen-index",
        default="data/processed/structural/screen_index.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = Path(args.screen_index)
    if not path.exists():
        raise FileNotFoundError(
            f"No existe {path}. Ejecuta primero el crawler estructural."
        )

    payload = json.loads(path.read_text(encoding="utf-8"))
    screens = payload.get("screens", [])
    title_sources = Counter(
        screen.get("title_source") or "legacy_or_unknown" for screen in screens
    )
    document_titles = Counter(
        screen.get("document_title") or "sin_document_title" for screen in screens
    )

    print("Calidad estructural de pantallas")
    print(f"Archivo: {path}")
    print(f"Pantallas: {len(screens)}")
    print(f"Fuentes de título: {dict(sorted(title_sources.items()))}")
    print(f"Títulos HTML repetidos: {dict(document_titles.most_common(5))}")
    print("-" * 80)

    for screen in screens:
        regions = screen.get("regions", {})
        main_text = screen.get("main_visible_text") or ""
        global_links = screen.get("global_links", [])
        local_links = screen.get("local_links", [])
        print(
            f"{screen.get('route')}: {screen.get('functional_title') or screen.get('title')} "
            f"[{screen.get('title_source') or 'legacy'}] | "
            f"main={len(main_text)} chars | "
            f"links global/local={len(global_links)}/{len(local_links)} | "
            f"regiones={','.join(sorted(regions)) or 'legacy'}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
