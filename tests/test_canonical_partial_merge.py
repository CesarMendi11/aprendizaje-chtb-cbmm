from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.knowledge.canonical.merge import CanonicalPartialMergeError, CanonicalPartialMerger
from src.knowledge.canonical.models import CanonicalKnowledgeBase
from src.knowledge.canonical.snapshot import CanonicalSnapshotContext


def _module(module_id, name, *, parent=None, depth=0, path=None):
    return {
        "id": module_id,
        "erp_id": "erp:test",
        "parent_module_id": parent,
        "depth": depth,
        "navigation_path": path or [name],
        "name": name,
        "normalized_name": name.casefold(),
    }


def _screen(screen_id, title, route, module_id=None):
    return {
        "id": screen_id,
        "erp_id": "erp:test",
        "module_id": module_id,
        "title": title,
        "normalized_title": title.casefold(),
        "route": route,
    }


def _knowledge(*, version: str, partial: bool = False) -> CanonicalKnowledgeBase:
    sales = _module("module:sales", "Sales")
    tracking = _module(
        "module:tracking",
        "Tracking",
        parent="module:sales",
        depth=1,
        path=["Sales", "Tracking"],
    )
    integrations = _module(
        "module:integrations",
        "Integrations",
        parent="module:tracking",
        depth=2,
        path=["Sales", "Tracking", "Integrations"],
    )

    if partial:
        modules = [sales, tracking, integrations]
        screens = [
            _screen("screen:home", "Partial home must not replace base", "/app/home"),
            _screen("screen:tracking", "Tracking refreshed", "/tracking", "module:tracking"),
            _screen(
                "screen:external-new",
                "External new",
                "/tracking/integrations/external-new",
                "module:integrations",
            ),
            _screen(
                "screen:provider",
                "Provider",
                "/tracking/integrations/provider",
                "module:integrations",
            ),
        ]
        fields = [
            {
                "id": "field:tracking:new",
                "screen_id": "screen:tracking",
                "label": "Status",
                "normalized_label": "status",
            }
        ]
        tables = [
            {
                "id": "table:provider",
                "screen_id": "screen:provider",
                "name": "Providers",
                "normalized_name": "providers",
                "column_ids": ["column:provider:name"],
            }
        ]
        columns = [
            {
                "id": "column:provider:name",
                "table_id": "table:provider",
                "name": "Name",
                "normalized_name": "name",
                "position": 0,
            }
        ]
    else:
        orders = _module(
            "module:orders",
            "Orders",
            parent="module:sales",
            depth=1,
            path=["Sales", "Orders"],
        )
        modules = [sales, orders, tracking, integrations]
        screens = [
            _screen("screen:home", "Base home", "/app/home"),
            _screen("screen:orders", "Orders", "/orders", "module:orders"),
            _screen("screen:tracking", "Tracking", "/tracking", "module:tracking"),
            _screen(
                "screen:external-old",
                "External old",
                "/tracking/integrations/external-old",
                "module:integrations",
            ),
        ]
        fields = [
            {
                "id": "field:orders:number",
                "screen_id": "screen:orders",
                "label": "Number",
                "normalized_label": "number",
            },
            {
                "id": "field:tracking:old",
                "screen_id": "screen:tracking",
                "label": "Legacy",
                "normalized_label": "legacy",
            },
        ]
        tables = []
        columns = []

    payload = {
        "schema_version": "1.1.0",
        "knowledge_version": version,
        "generated_at": datetime(2026, 8, 12, tzinfo=timezone.utc),
        "generator_version": "test",
        "source_profile": "fictional",
        "source_artifacts": ["screen_index.json"],
        "source_artifact_hashes": {"screen_index.json": f"hash-{version}"},
        "erp_system": {
            "id": "erp:test",
            "slug": "test",
            "name": "Test ERP",
            "profile_name": "fictional",
        },
        "modules": modules,
        "screens": screens,
        "ui_states": [],
        "fields": fields,
        "controls": [],
        "tables": tables,
        "table_columns": columns,
        "links": [],
        "events": [],
        "transitions": [],
        "evidence": [],
        "build_warnings": [],
        "statistics": {
            "modules": len(modules),
            "screens": len(screens),
            "ui_states": 0,
            "fields": len(fields),
            "controls": 0,
            "tables": len(tables),
            "table_columns": len(columns),
            "links": 0,
            "events": 0,
            "transitions": 0,
            "evidence": 0,
        },
    }
    return CanonicalKnowledgeBase.model_validate(payload)


def _snapshot() -> CanonicalSnapshotContext:
    return CanonicalSnapshotContext(
        mode="partial",
        scope="module",
        target="module:tracking",
        target_module_id="module:tracking",
        base_knowledge_version_id="00000000-0000-0000-0000-000000000001",
        base_knowledge_version="base-v1",
        erp_id="erp:test",
    )


def test_module_partial_replaces_only_target_subtree_and_preserves_siblings():
    base = _knowledge(version="base-v1")
    partial = _knowledge(version="partial-v1", partial=True)

    merged, report = CanonicalPartialMerger().merge(base, partial, _snapshot())

    modules = {item.id for item in merged.modules}
    screens = {item.id: item for item in merged.screens}
    fields = {item.id for item in merged.fields}

    assert modules == {
        "module:sales",
        "module:orders",
        "module:tracking",
        "module:integrations",
    }
    assert set(screens) == {
        "screen:home",
        "screen:orders",
        "screen:tracking",
        "screen:external-new",
        "screen:provider",
    }
    assert screens["screen:home"].title == "Base home"
    assert screens["screen:orders"].title == "Orders"
    assert screens["screen:tracking"].title == "Tracking refreshed"
    assert "screen:external-old" not in screens
    assert fields == {"field:orders:number", "field:tracking:new"}
    assert {item.id for item in merged.tables} == {"table:provider"}
    assert {item.id for item in merged.table_columns} == {"column:provider:name"}

    assert report.target_module_id == "module:tracking"
    assert report.removed_counts["screens"] == 2
    assert report.inserted_counts["screens"] == 3
    assert report.preserved_counts["screens"] == 2
    assert merged.statistics["screens"] == 5


def test_partial_navigation_context_does_not_overwrite_ancestor_module():
    base = _knowledge(version="base-v1")
    partial_payload = _knowledge(version="partial-v1", partial=True).model_dump(mode="json")
    partial_payload["modules"][0]["description"] = "Context observed only by MODULE crawl"
    partial = CanonicalKnowledgeBase.model_validate(partial_payload)

    merged, _ = CanonicalPartialMerger().merge(base, partial, _snapshot())

    sales = next(item for item in merged.modules if item.id == "module:sales")
    assert sales.description is None


def test_partial_must_match_pinned_base_version_and_erp():
    base = _knowledge(version="base-v1")
    partial = _knowledge(version="partial-v1", partial=True)
    payload = _snapshot().model_dump()
    payload["base_knowledge_version"] = "other-base"

    with pytest.raises(CanonicalPartialMergeError, match="knowledge_version base"):
        CanonicalPartialMerger().merge(
            base,
            partial,
            CanonicalSnapshotContext.model_validate(payload),
        )


def test_partial_entity_cannot_collide_with_preserved_sibling_identity():
    base = _knowledge(version="base-v1")
    payload = _knowledge(version="partial-v1", partial=True).model_dump(mode="json")
    tracking = next(item for item in payload["screens"] if item["id"] == "screen:tracking")
    tracking["id"] = "screen:orders"
    for field in payload["fields"]:
        field["screen_id"] = "screen:orders"
    partial = CanonicalKnowledgeBase.model_validate(payload)

    with pytest.raises(CanonicalPartialMergeError, match="colisiona"):
        CanonicalPartialMerger().merge(base, partial, _snapshot())


def test_merge_knowledge_version_is_deterministic_for_same_inputs():
    base = _knowledge(version="base-v1")
    partial = _knowledge(version="partial-v1", partial=True)
    merger = CanonicalPartialMerger()

    first, _ = merger.merge(base, partial, _snapshot())
    second, _ = merger.merge(base, partial, _snapshot())

    assert first.knowledge_version == second.knowledge_version
    assert first.generator_version == "canonical-partial-merge-1.1.0"
    assert first.source_artifact_hashes == {
        "base:screen_index.json": "hash-base-v1",
        "partial:screen_index.json": "hash-partial-v1",
    }


def _screen_snapshot() -> CanonicalSnapshotContext:
    return CanonicalSnapshotContext(
        mode="partial",
        scope="screen",
        target="/tracking",
        target_screen_id="screen:tracking",
        base_knowledge_version_id="00000000-0000-0000-0000-000000000001",
        base_knowledge_version="base-v1",
        erp_id="erp:test",
    )


def test_screen_partial_replaces_only_target_screen_and_preserves_module_context():
    base = _knowledge(version="base-v1")
    partial_payload = _knowledge(version="partial-v1", partial=True).model_dump(mode="json")
    target = next(item for item in partial_payload["screens"] if item["id"] == "screen:tracking")
    target["module_id"] = None
    partial = CanonicalKnowledgeBase.model_validate(partial_payload)

    merged, report = CanonicalPartialMerger().merge(base, partial, _screen_snapshot())

    modules = {item.id for item in merged.modules}
    screens = {item.id: item for item in merged.screens}
    fields = {item.id for item in merged.fields}

    assert modules == {
        "module:sales",
        "module:orders",
        "module:tracking",
        "module:integrations",
    }
    assert set(screens) == {
        "screen:home",
        "screen:orders",
        "screen:tracking",
        "screen:external-old",
    }
    assert screens["screen:tracking"].title == "Tracking refreshed"
    assert screens["screen:tracking"].module_id == "module:tracking"
    assert "screen:provider" not in screens
    assert "screen:external-new" not in screens
    assert fields == {"field:orders:number", "field:tracking:new"}
    assert not merged.tables
    assert report.scope == "screen"
    assert report.target == "/tracking"
    assert report.target_screen_id == "screen:tracking"
    assert report.target_module_id is None
    assert report.removed_counts["screens"] == 1
    assert report.inserted_counts["screens"] == 1


def test_screen_partial_requires_same_pinned_screen_route():
    base = _knowledge(version="base-v1")
    partial = _knowledge(version="partial-v1", partial=True)
    payload = _screen_snapshot().model_dump()
    payload["target"] = "/other"

    with pytest.raises(CanonicalPartialMergeError, match="ruta"):
        CanonicalPartialMerger().merge(
            base,
            partial,
            CanonicalSnapshotContext.model_validate(payload),
        )
