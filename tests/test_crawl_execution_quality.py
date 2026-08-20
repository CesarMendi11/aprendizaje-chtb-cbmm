from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from src.knowledge.crawl_execution_quality import (
    CRAWL_EXECUTION_QUALITY_CONTRACT_VERSION,
    CrawlExecutionQualityError,
    build_crawl_execution_quality,
    validate_certified_quality_source,
    validate_crawl_execution_quality,
    validate_matching_certified_quality,
)
from tests.crawl_quality_fixtures import (
    certified_crawl_quality,
    source_crawl_result,
    write_state_exploration_summary,
)


def _dirs(tmp_path: Path):
    structural = tmp_path / "processed" / "structural"
    review = tmp_path / "review" / "structural"
    structural.mkdir(parents=True)
    review.mkdir(parents=True)
    write_state_exploration_summary(structural)
    return structural, review


def _quality(tmp_path: Path, run_id: uuid.UUID, **result_overrides):
    structural, review = _dirs(tmp_path)
    result = source_crawl_result(
        run_id,
        scope="full",
        target=None,
        **result_overrides,
    )
    return structural, review, result


def test_quality_contract_accepts_closed_frontiers_without_ui_event_results(tmp_path):
    run_id = uuid.uuid4()
    structural, review, result = _quality(tmp_path, run_id)

    quality = build_crawl_execution_quality(
        review_dir=review,
        structural_dir=structural,
        source_crawl_result=result,
        expected_run_id=str(run_id),
        expected_scope="full",
        expected_target=None,
    )

    assert quality == certified_crawl_quality(run_id=run_id)
    assert validate_crawl_execution_quality(quality) == quality


def test_quality_contract_counts_ui_and_dynamic_restore_failures(tmp_path):
    run_id = uuid.uuid4()
    structural, review, result = _quality(tmp_path, run_id)
    (review / "home_ui_events_state_uncertainty.json").write_text(
        json.dumps(
            {
                "route": "/admin/home",
                "results": [
                    {"error": "state_restore_failed"},
                    {"error": "timeout: overlay intercepts pointer events"},
                    {"error": None},
                ],
            }
        ),
        encoding="utf-8",
    )
    (review / "home_dynamic_state_restore_failed_state_uncertainty.json").write_text(
        json.dumps({"reason": "dynamic_state_restore_failed"}),
        encoding="utf-8",
    )

    quality = build_crawl_execution_quality(
        review_dir=review,
        structural_dir=structural,
        source_crawl_result=result,
        expected_run_id=str(run_id),
        expected_scope="full",
        expected_target=None,
    )

    assert quality["ui_event_state_restore_failures"] == 1
    assert quality["dynamic_state_restore_failures"] == 1
    assert quality["state_restore_failures"] == 2
    assert quality["other_error_events"] == 1
    assert quality["blocking_failures"] == 2
    assert quality["gate_passed"] is False
    with pytest.raises(CrawlExecutionQualityError, match="no supera"):
        validate_crawl_execution_quality(quality)


@pytest.mark.parametrize(
    ("reason", "field"),
    [
        ("dynamic_state_exploration_error", "dynamic_state_exploration_errors"),
        ("navigation_error", "navigation_errors"),
        ("crawl_fixed_point_stalled", "fixed_point_stalls"),
    ],
)
def test_quality_contract_blocks_coverage_uncertainty_reasons(tmp_path, reason, field):
    run_id = uuid.uuid4()
    structural, review, result = _quality(tmp_path, run_id)
    (review / f"case_{reason}_uncertainty.json").write_text(
        json.dumps({"reason": reason}),
        encoding="utf-8",
    )

    quality = build_crawl_execution_quality(
        review_dir=review,
        structural_dir=structural,
        source_crawl_result=result,
        expected_run_id=str(run_id),
        expected_scope="full",
        expected_target=None,
    )

    assert quality[field] == 1
    assert quality["blocking_failures"] == 1
    assert quality["gate_passed"] is False


def test_quality_contract_blocks_pending_route_frontier(tmp_path):
    run_id = uuid.uuid4()
    structural, review, result = _quality(tmp_path, run_id, pending_routes=2)

    quality = build_crawl_execution_quality(
        review_dir=review,
        structural_dir=structural,
        source_crawl_result=result,
        expected_run_id=str(run_id),
        expected_scope="full",
        expected_target=None,
    )

    assert quality["route_frontier_pending"] == 2
    assert quality["blocking_failures"] == 2
    assert quality["gate_passed"] is False


def test_quality_contract_blocks_pending_state_frontier_and_cross_checks_artifact(tmp_path):
    run_id = uuid.uuid4()
    structural = tmp_path / "processed" / "structural"
    review = tmp_path / "review" / "structural"
    structural.mkdir(parents=True)
    review.mkdir(parents=True)
    write_state_exploration_summary(structural, states_pending=3)
    result = source_crawl_result(
        run_id,
        scope="full",
        target=None,
        states_pending=3,
    )

    quality = build_crawl_execution_quality(
        review_dir=review,
        structural_dir=structural,
        source_crawl_result=result,
        expected_run_id=str(run_id),
        expected_scope="full",
        expected_target=None,
    )

    assert quality["state_frontier_pending"] == 3
    assert quality["blocking_failures"] == 3
    assert quality["gate_passed"] is False


def test_quality_contract_rejects_state_frontier_disagreement(tmp_path):
    run_id = uuid.uuid4()
    structural, review, result = _quality(tmp_path, run_id, states_pending=1)

    with pytest.raises(CrawlExecutionQualityError, match="discrepan"):
        build_crawl_execution_quality(
            review_dir=review,
            structural_dir=structural,
            source_crawl_result=result,
            expected_run_id=str(run_id),
            expected_scope="full",
            expected_target=None,
        )


def test_quality_contract_rejects_legacy_or_inconsistent_payloads():
    with pytest.raises(CrawlExecutionQualityError, match="versión del contrato"):
        validate_crawl_execution_quality({"gate_passed": True})

    quality = certified_crawl_quality()
    quality["quality_contract_version"] = CRAWL_EXECUTION_QUALITY_CONTRACT_VERSION
    quality["blocking_failures"] = 1
    with pytest.raises(CrawlExecutionQualityError, match="blocking_failures"):
        validate_crawl_execution_quality(quality)


def test_matching_quality_requires_exact_certified_provenance():
    quality = certified_crawl_quality()
    assert validate_matching_certified_quality(quality, dict(quality)) == quality

    mismatch = dict(quality)
    mismatch["other_error_events"] = 1
    with pytest.raises(CrawlExecutionQualityError, match="no coinciden"):
        validate_matching_certified_quality(quality, mismatch)


def test_certified_quality_is_bound_to_crawl_identity():
    run_id = uuid.uuid4()
    quality = certified_crawl_quality(
        run_id=run_id, scope="screen", target="/admin/example"
    )

    assert validate_certified_quality_source(
        quality,
        source_run_id=run_id,
        source_scope="screen",
        source_target="/admin/example",
        check_target=True,
    ) == quality

    with pytest.raises(CrawlExecutionQualityError, match="source_crawl_job_id"):
        validate_certified_quality_source(quality, source_run_id=uuid.uuid4())

    with pytest.raises(CrawlExecutionQualityError, match="scope"):
        validate_certified_quality_source(
            quality, source_run_id=run_id, source_scope="module"
        )

    with pytest.raises(CrawlExecutionQualityError, match="target"):
        validate_certified_quality_source(
            quality,
            source_run_id=run_id,
            source_scope="screen",
            source_target="/admin/other",
            check_target=True,
        )
