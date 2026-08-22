from __future__ import annotations

from pathlib import Path

import yaml

from src.knowledge.canonical.ids import normalize_text, stable_id


def semantic_aliases_for(erp_id=None, *, config_dir="configs"):
    """Load optional aliases from the matching profile; empty for unknown ERPs."""
    result = {}
    for path in sorted(Path(config_dir).glob("*.y*ml")):
        try:
            profile = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        erp = profile.get("erp", {})
        code = str(erp.get("code") or "").strip()
        slug = normalize_text(code or erp.get("name") or "erp").replace(" ", "-")
        identifiers = {
            erp.get("id"),
            erp.get("code"),
            erp.get("name"),
            stable_id("erp", slug),
        }
        suffix_match = bool(code) and str(erp_id or "").casefold().endswith(code.casefold())
        if erp_id and erp_id not in identifiers and not suffix_match:
            continue
        result = profile.get("semantic_aliases", {}) or {}
        if erp_id:
            return result
    return result
