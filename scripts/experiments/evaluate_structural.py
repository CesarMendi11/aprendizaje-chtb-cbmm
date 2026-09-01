from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from erp_assistant.structural.canonical.ids import normalize_route, normalize_text
from erp_assistant.structural.canonical.repository import CanonicalKnowledgeRepository


@dataclass(frozen=True)
class ReferenceItem:
    entity_type: str
    parent_module_path: str
    name: str
    route: str
    notes: str

    @property
    def module_path_parts(self) -> tuple[str, ...]:
        parent = _split_module_path(self.parent_module_path)
        if self.entity_type == "module":
            return (*parent, normalize_text(self.name))
        return parent


def _split_module_path(value: str) -> tuple[str, ...]:
    return tuple(
        normalize_text(part) for part in str(value or "").split(">") if normalize_text(part)
    )


def load_reference(path: Path) -> list[ReferenceItem]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        expected = {"entity_type", "parent_module_path", "name", "route", "notes"}
        if set(reader.fieldnames or []) != expected:
            raise ValueError(
                "Cabecera inválida. Se espera exactamente: "
                "entity_type,parent_module_path,name,route,notes"
            )
        result: list[ReferenceItem] = []
        seen: set[tuple[str, tuple[str, ...], str, str]] = set()
        for line_number, row in enumerate(reader, start=2):
            entity_type = str(row.get("entity_type") or "").strip().casefold()
            if entity_type not in {"module", "screen"}:
                raise ValueError(f"Línea {line_number}: entity_type debe ser module o screen")
            name = " ".join(str(row.get("name") or "").split())
            if not name:
                raise ValueError(f"Línea {line_number}: name es obligatorio")
            route_raw = str(row.get("route") or "").strip()
            if entity_type == "screen" and not route_raw:
                raise ValueError(f"Línea {line_number}: route es obligatorio para screen")
            item = ReferenceItem(
                entity_type=entity_type,
                parent_module_path=" > ".join(
                    part.strip()
                    for part in str(row.get("parent_module_path") or "").split(">")
                    if part.strip()
                ),
                name=name,
                route=normalize_route(route_raw) if route_raw else "",
                notes=" ".join(str(row.get("notes") or "").split()),
            )
            key = (
                item.entity_type,
                item.module_path_parts,
                normalize_text(item.name),
                item.route,
            )
            if key in seen:
                raise ValueError(f"Línea {line_number}: referencia duplicada")
            seen.add(key)
            result.append(item)
    if not result:
        raise ValueError("El Gold Standard estructural está vacío")
    return result


def _canonical_module_paths(repository: CanonicalKnowledgeRepository) -> dict[str, tuple[str, ...]]:
    modules = {item.id: item for item in repository.knowledge.modules}
    cache: dict[str, tuple[str, ...]] = {}

    def resolve(module_id: str) -> tuple[str, ...]:
        if module_id in cache:
            return cache[module_id]
        module = modules[module_id]
        prefix: tuple[str, ...] = ()
        if module.parent_module_id:
            prefix = resolve(module.parent_module_id)
        value = (*prefix, normalize_text(module.name))
        cache[module_id] = value
        return value

    for module_id in modules:
        resolve(module_id)
    return cache


def _reference_keys(items: Iterable[ReferenceItem], dimension: str) -> set[tuple]:
    if dimension == "module":
        return {item.module_path_parts for item in items if item.entity_type == "module"}
    if dimension == "screen":
        return {
            (item.route, normalize_text(item.name))
            for item in items
            if item.entity_type == "screen"
        }
    if dimension == "screen_hierarchy":
        return {
            (item.route, item.module_path_parts, normalize_text(item.name))
            for item in items
            if item.entity_type == "screen"
        }
    raise ValueError(f"Dimensión estructural no soportada: {dimension}")


def _detected_keys(repository: CanonicalKnowledgeRepository, dimension: str) -> set[tuple]:
    module_paths = _canonical_module_paths(repository)
    if dimension == "module":
        return set(module_paths.values())
    if dimension == "screen":
        return {
            (normalize_route(screen.route), normalize_text(screen.title))
            for screen in repository.knowledge.screens
        }
    if dimension == "screen_hierarchy":
        return {
            (
                normalize_route(screen.route),
                module_paths.get(screen.module_id, ()),
                normalize_text(screen.title),
            )
            for screen in repository.knowledge.screens
        }
    raise ValueError(f"Dimensión estructural no soportada: {dimension}")


def _metrics(reference: set[tuple], detected: set[tuple]) -> dict[str, object]:
    tp_items = reference & detected
    fp_items = detected - reference
    fn_items = reference - detected
    tp = len(tp_items)
    fp = len(fp_items)
    fn = len(fn_items)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "reference": len(reference),
        "detected": len(detected),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_positives": [_render_key(item) for item in sorted(fp_items)],
        "false_negatives": [_render_key(item) for item in sorted(fn_items)],
    }


def _render_key(key: tuple) -> object:
    if len(key) == 3 and isinstance(key[1], tuple):
        route, module_path, name = key
        return {
            "route": route,
            "module_path": " > ".join(module_path),
            "name": name,
        }
    if len(key) == 2:
        route, name = key
        return {"route": route, "name": name}
    return " > ".join(key)


def evaluate(reference_path: Path, knowledge_path: Path) -> dict[str, object]:
    reference = load_reference(reference_path)
    repository = CanonicalKnowledgeRepository(knowledge_path)
    metrics = {}
    for dimension in ("module", "screen", "screen_hierarchy"):
        metrics[dimension] = _metrics(
            _reference_keys(reference, dimension),
            _detected_keys(repository, dimension),
        )
    return {
        "schema_version": "1.0.0",
        "evaluation_type": "structural_census",
        "reference_path": reference_path.as_posix(),
        "knowledge_path": knowledge_path.as_posix(),
        "knowledge_version": repository.knowledge.knowledge_version,
        "metrics": metrics,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evalúa módulos y pantallas canónicas contra un Gold Standard humano independiente."
    )
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--knowledge", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    payload = evaluate(args.reference, args.knowledge)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(args.output.resolve())
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
