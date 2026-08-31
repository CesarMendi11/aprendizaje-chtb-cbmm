from __future__ import annotations

from datetime import datetime, timezone

import pytest

from erp_assistant.structural.canonical.merge import CanonicalPartialMergeError, CanonicalPartialMerger
from erp_assistant.structural.canonical.models import CanonicalKnowledgeBase
from erp_assistant.structural.canonical.snapshot import CanonicalSnapshotContext


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
    assert first.generator_version == "canonical-partial-merge-1.1.4"
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


def test_screen_partial_drops_route_without_module_warning_after_module_restore():
    base = _knowledge(version="base-v1")
    partial_payload = _knowledge(version="partial-v1", partial=True).model_dump(mode="json")
    target = next(item for item in partial_payload["screens"] if item["id"] == "screen:tracking")
    target["module_id"] = None
    partial_payload["build_warnings"] = [
        {
            "code": "route_without_module",
            "message": "Pantalla sin módulo inferible",
            "entity_type": "screen",
            "entity_id": "screen:tracking",
        }
    ]
    partial = CanonicalKnowledgeBase.model_validate(partial_payload)

    merged, _ = CanonicalPartialMerger().merge(base, partial, _screen_snapshot())

    tracking = next(item for item in merged.screens if item.id == "screen:tracking")
    assert tracking.module_id == "module:tracking"
    assert not any(
        warning.code == "route_without_module"
        and warning.entity_id == "screen:tracking"
        for warning in merged.build_warnings
    )


def test_screen_partial_preserves_and_deduplicates_unrelated_warnings():
    base_payload = _knowledge(version="base-v1").model_dump(mode="json")
    partial_payload = _knowledge(version="partial-v1", partial=True).model_dump(mode="json")
    warning = {
        "code": "synthetic_warning",
        "message": "Synthetic warning",
        "entity_type": "screen",
        "entity_id": None,
    }
    base_payload["build_warnings"] = [{**warning, "count": 1}]
    partial_payload["build_warnings"] = [{**warning, "count": 2}]
    base = CanonicalKnowledgeBase.model_validate(base_payload)
    partial = CanonicalKnowledgeBase.model_validate(partial_payload)

    merged, _ = CanonicalPartialMerger().merge(base, partial, _screen_snapshot())

    matching = [
        item for item in merged.build_warnings if item.code == "synthetic_warning"
    ]
    assert len(matching) == 1
    assert matching[0].count == 2


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


def _with_cross_scope_dashboard_link(knowledge: CanonicalKnowledgeBase, *, target_screen_id):
    payload = knowledge.model_dump(mode="json")
    payload["links"].append(
        {
            "id": "link:tracking:dashboard",
            "screen_id": "screen:tracking",
            "label": "Dashboard",
            "normalized_label": "dashboard",
            "target_route": "/app/home",
            "target_screen_id": target_screen_id,
            "region": "global_navigation",
            "source_refs": ["screen_index.json"],
            "evidence_ids": [],
        }
    )
    payload["statistics"]["links"] = len(payload["links"])
    return CanonicalKnowledgeBase.model_validate(payload)


def test_module_partial_preserves_resolved_cross_scope_link_target_from_base():
    base = _with_cross_scope_dashboard_link(
        _knowledge(version="base-v1"),
        target_screen_id="screen:home",
    )
    partial = _with_cross_scope_dashboard_link(
        _knowledge(version="partial-v1", partial=True),
        target_screen_id=None,
    )

    merged, _ = CanonicalPartialMerger().merge(base, partial, _snapshot())

    dashboard = next(item for item in merged.links if item.id == "link:tracking:dashboard")
    assert dashboard.target_route == "/app/home"
    assert dashboard.target_screen_id == "screen:home"


def test_screen_partial_preserves_resolved_cross_scope_link_target_from_base():
    base = _with_cross_scope_dashboard_link(
        _knowledge(version="base-v1"),
        target_screen_id="screen:home",
    )
    partial = _with_cross_scope_dashboard_link(
        _knowledge(version="partial-v1", partial=True),
        target_screen_id=None,
    )

    merged, _ = CanonicalPartialMerger().merge(base, partial, _screen_snapshot())

    dashboard = next(item for item in merged.links if item.id == "link:tracking:dashboard")
    assert dashboard.target_screen_id == "screen:home"


def test_partial_does_not_restore_link_target_removed_from_merged_full():
    base_payload = _knowledge(version="base-v1").model_dump(mode="json")
    base_payload["links"].append(
        {
            "id": "link:tracking:legacy",
            "screen_id": "screen:tracking",
            "label": "Legacy external",
            "normalized_label": "legacy external",
            "target_route": "/tracking/integrations/external-old",
            "target_screen_id": "screen:external-old",
            "region": "main_content",
            "source_refs": ["screen_index.json"],
            "evidence_ids": [],
        }
    )
    base_payload["statistics"]["links"] = 1
    base = CanonicalKnowledgeBase.model_validate(base_payload)

    partial_payload = _knowledge(version="partial-v1", partial=True).model_dump(mode="json")
    partial_payload["links"].append(
        {
            "id": "link:tracking:legacy",
            "screen_id": "screen:tracking",
            "label": "Legacy external",
            "normalized_label": "legacy external",
            "target_route": "/tracking/integrations/external-old",
            "target_screen_id": None,
            "region": "main_content",
            "source_refs": ["screen_index.json"],
            "evidence_ids": [],
        }
    )
    partial_payload["statistics"]["links"] = 1
    partial = CanonicalKnowledgeBase.model_validate(partial_payload)

    merged, _ = CanonicalPartialMerger().merge(base, partial, _snapshot())

    legacy = next(item for item in merged.links if item.id == "link:tracking:legacy")
    assert "screen:external-old" not in {item.id for item in merged.screens}
    assert legacy.target_screen_id is None


def _with_tracking_title(
    knowledge: CanonicalKnowledgeBase,
    *,
    title: str,
    title_source: str,
) -> CanonicalKnowledgeBase:
    payload = knowledge.model_dump(mode="json")
    screen = next(item for item in payload["screens"] if item["id"] == "screen:tracking")
    screen["title"] = title
    screen["normalized_title"] = title.casefold()
    screen["title_source"] = title_source
    return CanonicalKnowledgeBase.model_validate(payload)


@pytest.mark.parametrize("snapshot", [_snapshot(), _screen_snapshot()])
def test_partial_route_fallback_does_not_replace_stronger_active_title(snapshot):
    base = _with_tracking_title(
        _knowledge(version="base-v1"),
        title="InspInformeRiesgo",
        title_source="discovery_hint",
    )
    partial = _with_tracking_title(
        _knowledge(version="partial-v1", partial=True),
        title="7",
        title_source="route_fallback",
    )
    partial_payload = partial.model_dump(mode="json")
    partial_screen = next(
        item for item in partial_payload["screens"] if item["id"] == "screen:tracking"
    )
    partial_screen["main_content_text"] = (
        "7 | Primera página | InspInformeRiesgo | Etiqueta nueva"
    )
    partial = CanonicalKnowledgeBase.model_validate(partial_payload)

    merged, _ = CanonicalPartialMerger().merge(base, partial, snapshot)

    tracking = next(item for item in merged.screens if item.id == "screen:tracking")
    assert tracking.title == "InspInformeRiesgo"
    assert tracking.normalized_title == "inspinformeriesgo"
    assert tracking.title_source == "discovery_hint"
    assert tracking.main_content_text == (
        "InspInformeRiesgo | Primera página | Etiqueta nueva"
    )


@pytest.mark.parametrize("snapshot", [_snapshot(), _screen_snapshot()])
def test_partial_direct_title_evidence_can_replace_active_title(snapshot):
    base = _with_tracking_title(
        _knowledge(version="base-v1"),
        title="Tracking",
        title_source="discovery_hint",
    )
    partial = _with_tracking_title(
        _knowledge(version="partial-v1", partial=True),
        title="Tracking refreshed",
        title_source="main_heading",
    )

    merged, _ = CanonicalPartialMerger().merge(base, partial, snapshot)

    tracking = next(item for item in merged.screens if item.id == "screen:tracking")
    assert tracking.title == "Tracking refreshed"
    assert tracking.title_source == "main_heading"


def test_partial_route_fallback_can_refresh_previous_route_fallback():
    base = _with_tracking_title(
        _knowledge(version="base-v1"),
        title="Old route label",
        title_source="route_fallback",
    )
    partial = _with_tracking_title(
        _knowledge(version="partial-v1", partial=True),
        title="Tracking",
        title_source="route_fallback",
    )

    merged, _ = CanonicalPartialMerger().merge(base, partial, _snapshot())

    tracking = next(item for item in merged.screens if item.id == "screen:tracking")
    assert tracking.title == "Tracking"
    assert tracking.title_source == "route_fallback"


def test_merge_preserves_distinct_base_and_partial_profile_fingerprints():
    base = _knowledge(version="base-v1").model_copy(
        update={
            "source_profile": "configs/base.yaml",
            "source_artifacts": [
                "profile:configs/base.yaml",
                "screen_index.json",
            ],
            "source_artifact_hashes": {
                "profile:configs/base.yaml": "a" * 64,
                "screen_index.json": "hash-base-v1",
            },
        }
    )
    partial = _knowledge(version="partial-v1", partial=True).model_copy(
        update={
            "source_profile": "configs/partial.yaml",
            "source_artifacts": [
                "profile:configs/partial.yaml",
                "screen_index.json",
            ],
            "source_artifact_hashes": {
                "profile:configs/partial.yaml": "b" * 64,
                "screen_index.json": "hash-partial-v1",
            },
        }
    )

    merged, _ = CanonicalPartialMerger().merge(base, partial, _snapshot())

    assert merged.source_profile == "configs/base.yaml"
    assert (
        merged.source_artifact_hashes["base:profile:configs/base.yaml"]
        == "a" * 64
    )
    assert (
        merged.source_artifact_hashes["partial:profile:configs/partial.yaml"]
        == "b" * 64
    )
