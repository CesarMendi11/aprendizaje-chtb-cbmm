from __future__ import annotations

import csv
import json

import pytest

from scripts.experiments.benchmark_screen_purpose_v14 import (
    _model_aliases,
    load_development_set,
    summarize_model_result,
    write_review_csv,
)


def test_load_development_set_requires_explicit_holdout_exclusion(tmp_path):
    path = tmp_path / "screens.json"
    path.write_text(
        json.dumps(
            {
                "set_id": "dev",
                "purpose": "development_only",
                "exclude_from_final_m2_quality_evaluation": False,
                "screens": [
                    {"screen_id": f"screen:{index}", "title": "X", "route": "/x", "profile": "p"}
                    for index in range(3)
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="excluirse"):
        load_development_set(path)


def test_load_development_set_rejects_duplicate_screen_ids(tmp_path):
    path = tmp_path / "screens.json"
    path.write_text(
        json.dumps(
            {
                "set_id": "dev",
                "purpose": "development_only",
                "exclude_from_final_m2_quality_evaluation": True,
                "screens": [
                    {"screen_id": "screen:a", "title": "A", "route": "/a", "profile": "p1"},
                    {"screen_id": "screen:b", "title": "B", "route": "/b", "profile": "p2"},
                    {"screen_id": "screen:a", "title": "C", "route": "/c", "profile": "p3"},
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicada"):
        load_development_set(path)


def test_summarize_model_result_excludes_pre_generation_grounding_failure_from_latency():
    summary = summarize_model_result(
        {
            "results": [
                {
                    "status": "generated",
                    "generation_elapsed_ms": 100.0,
                    "functional_claims": [{}, {}],
                    "warnings": [],
                },
                {
                    "status": "generation_failed",
                    "generation_elapsed_ms": 300.0,
                    "error_type": "OllamaTimeoutError",
                },
                {
                    "status": "generation_failed",
                    "generation_elapsed_ms": 1.0,
                    "error_type": "InferenceGroundingError",
                },
            ]
        }
    )

    assert summary["generated"] == 1
    assert summary["ineligible"] == 1
    assert summary["generation_failures"] == 1
    assert summary["claims"] == 2
    assert summary["latency_ms"]["count"] == 2
    assert summary["latency_ms"]["mean"] == 200.0


def test_model_aliases_are_deterministic_and_do_not_expose_model_name():
    models = ["phi4-mini:3.8b", "qwen2.5:14b", "qwen3.5:9b"]

    first = _model_aliases(models, "abc")
    second = _model_aliases(models, "abc")

    assert first == second
    assert set(first.values()) == {"M01", "M02", "M03"}
    assert all(model not in alias for model, alias in first.items())


def test_write_review_csv_uses_model_alias_and_blank_human_judgements(tmp_path):
    output = tmp_path / "review.csv"
    benchmark = {
        "screen_set": {"screens": [{"screen_id": "screen:a", "profile": "table"}]},
        "models": ["real-model"],
        "model_aliases": {"real-model": "M01"},
        "results": {
            "real-model": {
                "run": {
                    "results": [
                        {
                            "screen_id": "screen:a",
                            "screen_title": "A",
                            "status": "generated",
                            "purpose_summary": "Purpose",
                            "functional_claims": [
                                {"statement": "Claim", "evidence_refs": ["column:a"]}
                            ],
                        }
                    ]
                }
            }
        },
    }

    write_review_csv(output, benchmark)

    with output.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 2
    assert {row["model_alias"] for row in rows} == {"M01"}
    assert "real-model" not in output.read_text(encoding="utf-8")
    assert rows[1]["text"] == "Claim"
    assert rows[1]["evidence_refs"] == "column:a"
    assert rows[1]["correctness"] == ""
    assert rows[1]["review_decision"] == ""
