import pytest

from src.knowledge.canonical.snapshot import CanonicalSnapshotContext


def test_full_snapshot_contract_is_strict():
    snapshot = CanonicalSnapshotContext.full()
    assert snapshot.mode == "full"
    assert snapshot.scope == "full"
    assert snapshot.target is None

    with pytest.raises(ValueError, match="scope=full no acepta target"):
        CanonicalSnapshotContext(mode="full", scope="full", target="/x")


def test_module_snapshot_requires_pinned_base_and_matching_target():
    snapshot = CanonicalSnapshotContext(
        mode="partial",
        scope="module",
        target="module:tracking",
        target_module_id="module:tracking",
        base_knowledge_version_id="00000000-0000-0000-0000-000000000001",
        base_knowledge_version="active-v10",
        erp_id="erp:demo",
    )
    assert snapshot.target_module_id == "module:tracking"

    with pytest.raises(ValueError, match="versión base"):
        CanonicalSnapshotContext(
            mode="partial",
            scope="module",
            target="module:tracking",
            target_module_id="module:tracking",
        )


def test_screen_snapshot_is_partial_and_route_scoped():
    snapshot = CanonicalSnapshotContext(
        mode="partial",
        scope="screen",
        target="/admin/tracking",
    )
    assert snapshot.target == "/admin/tracking"

    with pytest.raises(ValueError, match="ruta interna"):
        CanonicalSnapshotContext(
            mode="partial",
            scope="screen",
            target="https://example.test/admin/tracking",
        )
