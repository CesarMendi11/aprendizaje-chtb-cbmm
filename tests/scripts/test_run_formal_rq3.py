from __future__ import annotations

import pytest

from scripts.experiments import run_formal_rq3 as runner
from scripts.experiments.rq3_harness import CaptureRecord


def test_frozen_rq3_matrix_is_54_by_4():
    queries, entries = runner.load_inputs(
        "experiments/query_bank/formal/rq3/rq3_query_bank_v2.json",
        "experiments/query_bank/formal/rq3/rq3_execution_plan_v2.json",
    )

    assert len(queries) == 54
    assert len(entries) == 216


def test_output_record_matches_existing_blinding_contract():
    record = CaptureRecord(
        run_id="Q001:A:g1",
        query_id="Q001",
        condition="A",
        graph_enabled=True,
        question="Pregunta",
        current_route=None,
        answer="Respuesta",
        status="answered",
        sources=[],
        end_to_end_latency_ms=1.0,
        writer_invoked=False,
        raw_response={},
    )

    payload = {"records": [record.model_dump(mode="json")]}

    assert "records" in payload
    assert CaptureRecord.model_validate(payload["records"][0]).run_id == "Q001:A:g1"


def test_formal_guard_is_required(monkeypatch):
    monkeypatch.delenv("ERP_ASSISTANT_FORMAL_RUN", raising=False)

    with pytest.raises(runner.FormalRQ3Error):
        runner._require_formal_target()
