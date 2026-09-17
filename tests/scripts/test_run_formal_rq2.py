from __future__ import annotations

import pytest

from scripts.experiments import run_formal_rq2 as runner


def test_frozen_rq2_universe_has_56_routes():
    routes = runner.eligible_routes(
        "experiments/gold_standard/formal/rq2/semantic_reference_v1.json",
        "experiments/protocol/policy_scope_contract_v1.json",
    )

    assert len(routes) == 56
    assert len(set(routes)) == 56


def test_formal_guard_is_required(monkeypatch):
    monkeypatch.delenv("ERP_ASSISTANT_FORMAL_RUN", raising=False)

    with pytest.raises(runner.FormalRQ2Error):
        runner._require_formal_target()
