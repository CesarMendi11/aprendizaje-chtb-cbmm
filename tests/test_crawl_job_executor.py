from __future__ import annotations

from pathlib import Path

import pytest

from src.database.enums import PipelineJobScope
from src.pipeline.crawl_job_executor import (
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
    from src.pipeline.crawl_job_executor import CrawlJobExecutor

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
    from src.pipeline.crawl_job_executor import CrawlJobExecutor

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
