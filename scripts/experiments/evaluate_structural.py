from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from erp_assistant.structural.canonical.ids import normalize_route, normalize_text
from erp_assistant.structural.canonical.repository import CanonicalKnowledgeRepository
from scripts.experiments.policy_scope import PolicyScope, load_policy_scope


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
            notes = " ".join(str(row.get("notes") or "").split())
            absent_title_marker = "title_status: absent" in notes.casefold()

            if entity_type == "module" and not name:
                raise ValueError(
                    f"Línea {line_number}: name es obligatorio para module"
                )

            if entity_type == "screen" and not name and not absent_title_marker:
                raise ValueError(
                    f"Línea {line_number}: screen sin name requiere "
                    "'title_status: absent' en notes"
                )

            if entity_type == "screen" and name and absent_title_marker:
                raise ValueError(
                    f"Línea {line_number}: title_status: absent es incompatible "
                    "con un screen.name no vacío"
                )

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
                notes=notes,
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
    if dimension == "screen_route":
        return {
            (item.route,)
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
    if dimension == "screen_route":
        return {
            (normalize_route(screen.route),)
            for screen in repository.knowledge.screens
        }
    raise ValueError(f"Dimensión estructural no soportada: {dimension}")


def _path_is_within(path: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return len(path) >= len(prefix) and path[: len(prefix)] == prefix


def _policy_partition_reference(
    items: list[ReferenceItem],
    policy_scope: PolicyScope,
) -> tuple[list[ReferenceItem], set[tuple[str, ...]], list[ReferenceItem]]:
    screens = [item for item in items if item.entity_type == "screen"]
    blocked_screens = [
        item for item in screens if policy_scope.is_reference_blocked(item.route)
    ]

    blocked_module_paths: set[tuple[str, ...]] = set()
    for item in items:
        if item.entity_type != "module":
            continue
        module_path = item.module_path_parts
        descendants = [
            screen
            for screen in screens
            if _path_is_within(screen.module_path_parts, module_path)
        ]
        if descendants and all(
            policy_scope.is_reference_blocked(screen.route)
            for screen in descendants
        ):
            blocked_module_paths.add(module_path)

    eligible = [
        item
        for item in items
        if not (
            (
                item.entity_type == "screen"
                and policy_scope.is_reference_blocked(item.route)
            )
            or (
                item.entity_type == "module"
                and item.module_path_parts in blocked_module_paths
            )
        )
    ]
    return eligible, blocked_module_paths, blocked_screens


def _filter_detected_for_policy(
    detected: set[tuple],
    dimension: str,
    policy_scope: PolicyScope,
    blocked_module_paths: set[tuple[str, ...]],
) -> set[tuple]:
    if dimension == "module":
        return {
            key
            for key in detected
            if not any(
                _path_is_within(key, blocked_path)
                for blocked_path in blocked_module_paths
            )
        }

    if dimension in {"screen", "screen_hierarchy", "screen_route"}:
        return {
            key
            for key in detected
            if not policy_scope.is_under_blocked_prefix(key[0])
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


def _screen_identity_diagnostics(
    items: Iterable[ReferenceItem],
    repository: CanonicalKnowledgeRepository,
    policy_scope: PolicyScope | None = None,
) -> dict[str, object]:
    reference_screens = [
        item
        for item in items
        if item.entity_type == "screen"
    ]

    detected_screens = [
        screen
        for screen in repository.knowledge.screens
        if not (
            policy_scope
            and policy_scope.is_under_blocked_prefix(screen.route)
        )
    ]

    module_paths = _canonical_module_paths(
        repository
    )

    reference_by_route: dict[
        str,
        list[ReferenceItem],
    ] = {}

    detected_by_route: dict[
        str,
        list[object],
    ] = {}

    for item in reference_screens:
        reference_by_route.setdefault(
            item.route,
            [],
        ).append(
            item
        )

    for screen in detected_screens:
        route = normalize_route(
            screen.route
        )

        detected_by_route.setdefault(
            route,
            [],
        ).append(
            screen
        )

    shared_routes = sorted(
        set(reference_by_route)
        & set(detected_by_route)
    )

    title_matches = 0
    hierarchy_matches = 0

    title_mismatches = []
    hierarchy_mismatches = []

    for route in shared_routes:
        reference_titles = {
            normalize_text(item.name)
            for item in reference_by_route[
                route
            ]
        }

        detected_titles = {
            normalize_text(screen.title)
            for screen in detected_by_route[
                route
            ]
        }

        if (
            reference_titles
            & detected_titles
        ):
            title_matches += 1
        else:
            title_mismatches.append(
                {
                    "route":
                        route,

                    "reference_titles":
                        sorted(
                            reference_titles
                        ),

                    "detected_titles":
                        sorted(
                            detected_titles
                        ),
                }
            )

        reference_paths = {
            item.module_path_parts
            for item in reference_by_route[
                route
            ]
        }

        detected_paths = {
            module_paths.get(
                screen.module_id,
                (),
            )
            for screen in detected_by_route[
                route
            ]
        }

        if (
            reference_paths
            & detected_paths
        ):
            hierarchy_matches += 1
        else:
            hierarchy_mismatches.append(
                {
                    "route":
                        route,

                    "reference_module_paths":
                        sorted(
                            " > ".join(path)
                            for path
                            in reference_paths
                        ),

                    "detected_module_paths":
                        sorted(
                            " > ".join(path)
                            for path
                            in detected_paths
                        ),
                }
            )

    shared_count = len(
        shared_routes
    )

    return {
        "screen_route":
            _metrics(
                _reference_keys(
                    reference_screens,
                    "screen_route",
                ),
                _detected_keys(
                    repository,
                    "screen_route",
                ),
            ),

        "shared_route_count":
            shared_count,

        "title_match_on_shared_routes": {
            "matches":
                title_matches,

            "total":
                shared_count,

            "accuracy":
                (
                    title_matches
                    / shared_count
                    if shared_count
                    else None
                ),

            "mismatches":
                title_mismatches,
        },

        "hierarchy_match_on_shared_routes": {
            "matches":
                hierarchy_matches,

            "total":
                shared_count,

            "accuracy":
                (
                    hierarchy_matches
                    / shared_count
                    if shared_count
                    else None
                ),

            "mismatches":
                hierarchy_mismatches,
        },

        "interpretation": (
            "Diagnostics only. The frozen primary RQ1 dimensions remain "
            "module, screen and screen_hierarchy. screen_route separates "
            "route discovery from title-label disagreement without replacing "
            "the primary metrics."
        ),
    }


def evaluate(
    reference_path: Path,
    knowledge_path: Path,
    policy_scope_path: Path | None = None,
) -> dict[str, object]:
    reference = load_reference(reference_path)
    repository = CanonicalKnowledgeRepository(knowledge_path)
    policy_scope = (
        load_policy_scope(policy_scope_path)
        if policy_scope_path is not None
        else None
    )

    scoring_reference = reference
    blocked_module_paths: set[tuple[str, ...]] = set()
    blocked_screens: list[ReferenceItem] = []

    if policy_scope is not None:
        (
            scoring_reference,
            blocked_module_paths,
            blocked_screens,
        ) = _policy_partition_reference(reference, policy_scope)

        reference_blocked_routes = {item.route for item in blocked_screens}
        if reference_blocked_routes != policy_scope.policy_blocked_routes:
            missing = sorted(
                policy_scope.policy_blocked_routes - reference_blocked_routes
            )
            raise ValueError(
                "policy scope blocked routes do not match structural reference; "
                f"missing={missing}"
            )

    metrics = {}
    for dimension in ("module", "screen", "screen_hierarchy"):
        detected = _detected_keys(repository, dimension)
        if policy_scope is not None:
            detected = _filter_detected_for_policy(
                detected,
                dimension,
                policy_scope,
                blocked_module_paths,
            )
        metrics[dimension] = _metrics(
            _reference_keys(scoring_reference, dimension),
            detected,
        )

    diagnostics: dict[str, object] = {
        "screen_identity": _screen_identity_diagnostics(
            scoring_reference,
            repository,
            policy_scope,
        ),
    }

    payload: dict[str, object] = {
        "schema_version": "1.1.0",
        "evaluation_type": "structural_census",
        "reference_path": reference_path.as_posix(),
        "knowledge_path": knowledge_path.as_posix(),
        "knowledge_version": repository.knowledge.knowledge_version,
        "metrics": metrics,
        "diagnostics": diagnostics,
    }

    if policy_scope is not None:
        detected_under_blocked_prefix = sorted(
            {
                normalize_route(screen.route)
                for screen in repository.knowledge.screens
                if policy_scope.is_under_blocked_prefix(screen.route)
            }
        )
        diagnostics["all_reference_screen_route"] = _metrics(
            _reference_keys(reference, "screen_route"),
            _detected_keys(repository, "screen_route"),
        )
        payload["policy_scope"] = {
            "contract_id": policy_scope.contract_id,
            "path": policy_scope.source_path.as_posix(),
            "sha256": policy_scope.file_sha256,
            "blocked_route_prefix": policy_scope.blocked_route_prefix,
            "policy_blocked_screen_routes": sorted(
                policy_scope.policy_blocked_routes
            ),
            "policy_blocked_screen_count": len(blocked_screens),
            "policy_blocked_module_paths": sorted(
                " > ".join(path) for path in blocked_module_paths
            ),
            "eligible_screen_count": len(
                [
                    item
                    for item in scoring_reference
                    if item.entity_type == "screen"
                ]
            ),
            "detected_under_blocked_prefix": detected_under_blocked_prefix,
            "policy_violation_count": len(detected_under_blocked_prefix),
            "primary_metric_rule": (
                "POLICY_BLOCKED items are reported separately and excluded "
                "from primary TP/FP/FN denominators."
            ),
        }

    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evalúa módulos y pantallas canónicas contra un Gold Standard humano independiente."
    )
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--knowledge", type=Path, required=True)
    parser.add_argument("--policy-scope", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    payload = evaluate(args.reference, args.knowledge, args.policy_scope)
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
