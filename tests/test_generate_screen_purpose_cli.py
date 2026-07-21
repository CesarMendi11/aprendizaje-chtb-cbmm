from __future__ import annotations

import json

import pytest

from scripts import generate_screen_purpose_proposal as cli
from src.analysis.generation.errors import InferenceGroundingError


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class Connection:
    def __init__(self, *, revision="20260721_01", proposals=True, actions=True):
        self.revision = revision
        self.proposals = proposals
        self.actions = actions

    def exec_driver_sql(self, statement):
        if "alembic_version')" in statement:
            return Result("alembic_version")
        if statement == "SELECT version_num FROM alembic_version":
            return Result(self.revision)
        if "semantic_proposals" in statement:
            return Result("semantic_proposals" if self.proposals else None)
        if "semantic_review_actions" in statement:
            return Result("semantic_review_actions" if self.actions else None)
        raise AssertionError(statement)


def test_persist_preflight_accepts_only_complete_semantic_schema():
    cli._semantic_schema_preflight(Connection())
    for connection in (
        Connection(revision="20260716_01"),
        Connection(proposals=False),
        Connection(actions=False),
    ):
        with pytest.raises(cli.CLIError) as captured:
            cli._semantic_schema_preflight(connection)
        assert captured.value.category == "semantic_schema_not_applied"


def test_cli_domain_error_is_single_sanitized_json_without_traceback(monkeypatch, capsys):
    rejected = "No se puede editar la información confidencial."

    def fail():
        raise InferenceGroundingError(
            "Inferencia rechazada",
            stage="grounding_validation",
            location=("uncertainties", 0),
            category="unsupported_absolute_negative_claim",
            value_length=len(rejected),
            value_type="str",
        )

    monkeypatch.setattr(cli, "main", fail)
    assert cli.cli_main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    diagnostic = json.loads(captured.err)
    assert diagnostic == {
        "category": "unsupported_absolute_negative_claim",
        "error_class": "InferenceGroundingError",
        "location": ["uncertainties", "0"],
        "ok": False,
        "ollama_called": False,
        "persisted": False,
        "stage": "grounding_validation",
        "structured_output_mode": "json_schema",
        "value_length": len(rejected),
        "value_type": "str",
    }
    assert rejected not in captured.err
    assert "Traceback" not in captured.err


def test_cli_reports_ollama_called_after_grounding_rejection(monkeypatch, capsys):
    def fail():
        cli.EXECUTION_STATE.ollama_called = True
        raise InferenceGroundingError(
            "Inferencia rechazada",
            stage="grounding_validation",
            category="unsupported_absolute_negative_claim",
        )

    monkeypatch.setattr(cli, "main", fail)
    assert cli.cli_main() == 2
    assert json.loads(capsys.readouterr().err)["ollama_called"] is True


def test_dry_run_read_only_check_is_explicit():
    source = open(cli.__file__, encoding="utf-8").read()
    assert "SHOW transaction_read_only" in source
    assert 'read_only != "on"' in source
