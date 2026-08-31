from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from erp_assistant.persistence.postgres.enums import PipelineJobScope
from erp_assistant.orchestration.pipeline.executors.crawl import (
    CrawlJobExecutionError,
    _isolated_profile,
)


def base_profile(tmp_path):
    return {
        "erp": {"name": "ERP Test", "code": "erp_test", "base_url": "http://erp.test"},
        "exploration": {"allowed_routes": ["/admin/"], "blocked_routes": ["/admin/security"]},
        "output": {"raw_playwright_dir": str(tmp_path / "official" / "raw")},
    }


def test_isolated_profile_redirects_outputs_without_mutating_source(tmp_path):
    profile = base_profile(tmp_path)
    original_output = dict(profile["output"])
    run_root = tmp_path / "runs" / "job-1"

    isolated = _isolated_profile(profile, run_root)

    assert profile["output"] == original_output
    assert isolated is not profile
    assert isolated["output"]
    for value in isolated["output"].values():
        assert Path(value).resolve().is_relative_to(run_root.resolve())
    assert isolated["output"]["processed_structural_dir"].endswith("processed/structural")


def test_screen_scope_validation_rejects_external_or_blocked_routes(tmp_path):
    from erp_assistant.orchestration.pipeline.executors.crawl import CrawlJobExecutor

    executor = CrawlJobExecutor(profile_path=tmp_path / "unused.yaml", runs_root=tmp_path)
    profile = base_profile(tmp_path)

    assert executor._validate_target(
        profile,
        PipelineJobScope.SCREEN,
        "/admin/facturas",
    ) == "/admin/facturas"

    with pytest.raises(CrawlJobExecutionError, match="ruta interna"):
        executor._validate_target(
            profile,
            PipelineJobScope.SCREEN,
            "https://example.test/admin/facturas",
        )
    with pytest.raises(CrawlJobExecutionError, match="no está permitida"):
        executor._validate_target(
            profile,
            PipelineJobScope.SCREEN,
            "/admin/security/users",
        )
    with pytest.raises(CrawlJobExecutionError, match="no acepta target"):
        executor._validate_target(
            profile,
            PipelineJobScope.FULL,
            "/admin/facturas",
        )


def test_module_scope_requires_pinned_boundary_and_matching_target(tmp_path):
    from erp_assistant.orchestration.pipeline.executors.crawl import CrawlJobExecutor

    executor = CrawlJobExecutor(profile_path=tmp_path / 'unused.yaml', runs_root=tmp_path)
    profile = base_profile(tmp_path)

    assert executor._validate_target(
        profile,
        PipelineJobScope.MODULE,
        'module:tracking',
    ) == 'module:tracking'

    with pytest.raises(CrawlJobExecutionError, match='target_module_id canónico'):
        executor._validate_target(profile, PipelineJobScope.MODULE, '/admin/tracking')

    parameters = {
        'target_module_id': 'module:tracking',
        'module_scope': {
            'root_module_id': 'module:tracking',
            'module_ids': ['module:tracking'],
            'known_screen_routes': ['/admin/tracking'],
            'navigation_path': ['Sales', 'Tracking'],
            'navigation_origin_path': ['#sales', '#tracking'],
        },
    }
    resolved = executor._module_boundary(
        PipelineJobScope.MODULE,
        'module:tracking',
        parameters,
    )
    assert resolved is not None
    assert resolved.root_module_id == 'module:tracking'

    with pytest.raises(CrawlJobExecutionError, match='consistente'):
        executor._module_boundary(
            PipelineJobScope.MODULE,
            'module:other',
            parameters,
        )


def test_crawl_result_publishes_profile_provenance(tmp_path):
    from erp_assistant.orchestration.pipeline.executors.crawl import CrawlJobExecutor

    structural = tmp_path / "processed" / "structural"
    structural.mkdir(parents=True)
    summary = SimpleNamespace(
        visited_count=1,
        pending_count=0,
        functional_screen_count=1,
        unavailable_count=0,
        nodes_count=2,
        edges_count=1,
        states_count=1,
        state_transitions_count=0,
        state_frontier_explored_count=1,
        state_frontier_pending_count=0,
        routes_graph_path=structural / "routes_graph.json",
        screen_index_path=structural / "screen_index.json",
        state_flow_graph_path=structural / "state_flow_graph.json",
        network_evidence_count=0,
        network_evidence_path=None,
    )

    result = CrawlJobExecutor._result(
        summary,
        tmp_path,
        PipelineJobScope.FULL,
        None,
        profile_path="configs/test.yaml",
        profile_sha256="a" * 64,
    )

    assert result["profile_path"] == "configs/test.yaml"
    assert result["profile_sha256"] == "a" * 64
