from __future__ import annotations

from pathlib import Path

import yaml


def semantic_aliases_for(erp_id=None, *, config_dir="configs"):
    """Load optional aliases from the matching profile; empty for unknown ERPs."""
    result = {}
    for path in sorted(Path(config_dir).glob("*.y*ml")):
        try:
            profile = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        erp = profile.get("erp", {})
        identifiers = {erp.get("id"), erp.get("code"), erp.get("name")}
        if (
            erp_id
            and erp_id not in identifiers
            and not str(erp_id).casefold().endswith(str(erp.get("code", "")).casefold())
        ):
            continue
        result = profile.get("semantic_aliases", {}) or {}
        if erp_id:
            return result
    return result
