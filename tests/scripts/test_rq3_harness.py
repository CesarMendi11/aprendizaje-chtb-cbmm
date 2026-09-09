from __future__ import annotations

import pytest
from pydantic import ValidationError

from scripts.experiments.rq3_harness import (
    CaptureRecord,
    RQ3QueryBank,
    build_blind_packet,
    build_run_plan,
    query_bank_hash,
    validate_capture_completeness,
)


def frozen_bank() -> RQ3QueryBank:
    strata = (
        (
            "locate_screen",
            "answer",
            None,
        ),
        (
            "screen_purpose",
            "answer",
            None,
        ),
        (
            "fields_and_search",
            "answer",
            None,
        ),
        (
            "tables_and_columns",
            "answer",
            None,
        ),
        (
            "controls_actions_and_navigation",
            "answer",
            None,
        ),
        (
            "current_route_context",
            "answer",
            "/admin/example",
        ),
        (
            "ambiguity_and_clarification",
            "clarification",
            None,
        ),
        (
            "out_of_scope_abstention",
            "abstention",
            None,
        ),
        (
            "mutative_safety",
            "abstention",
            None,
        ),
    )

    queries = []
    index = 1

    for (
        stratum,
        behavior,
        current_route,
    ) in strata:
        for repetition in range(6):
            queries.append(
                {
                    "query_id":
                        f"Q{index:03d}",

                    "stratum":
                        stratum,

                    "question":
                        (
                            f"Pregunta {index} "
                            f"repetición {repetition + 1}"
                        ),

                    "current_route":
                        current_route,

                    "expected_behavior":
                        behavior,

                    "required_claims":
                        [],

                    "prohibited_claims":
                        [],
                }
            )

            index += 1

    return RQ3QueryBank.model_validate(
        {
            "schema_version":
                "1.0.0",

            "status":
                "frozen",

            "bank_id":
                "rq3-test-v1",

            "queries":
                queries,
        }
    )

def captures_for(
    bank: RQ3QueryBank,
) -> list[CaptureRecord]:
    query_by_id = {
        query.query_id:
            query
        for query in bank.queries
    }

    return [
        CaptureRecord(
            run_id=
                entry.run_id,

            query_id=
                entry.query_id,

            condition=
                entry.condition,

            graph_enabled=
                entry.graph_enabled,

            question=
                query_by_id[
                    entry.query_id
                ].question,

            answer=(
                "Respuesta "
                f"{entry.run_id}"
            ),

            status=
                "answered",

            sources=[
                {
                    "title":
                        "Pantalla",

                    "route":
                        "/admin/example",
                }
            ],

            end_to_end_latency_ms=
                10.0,

            writer_invoked=(
                entry.condition
                == "C"
            ),

            raw_response={
                "experimentCondition":
                    entry.condition,
            },
        )
        for entry in build_run_plan(
            bank,
            seed=1,
        )
    ]


def test_frozen_bank_requires_all_nine_strata():
    payload = frozen_bank().model_dump(
        mode="json"
    )

    payload["queries"] = (
        payload["queries"][:-1]
    )

    with pytest.raises(
        ValidationError,
        match=(
            "cover every RQ3 stratum"
        ),
    ):
        RQ3QueryBank.model_validate(
            payload
        )


def test_stratum_specific_behavior_guards_are_enforced():
    payload = frozen_bank().model_dump(
        mode="json"
    )

    ambiguity = next(
        query
        for query in payload["queries"]
        if (
            query["stratum"]
            == "ambiguity_and_clarification"
        )
    )

    ambiguity[
        "expected_behavior"
    ] = "answer"

    with pytest.raises(
        ValidationError,
        match=(
            "requires clarification behavior"
        ),
    ):
        RQ3QueryBank.model_validate(
            payload
        )


def test_mutative_safety_may_require_safe_guidance_answer():
    payload = frozen_bank().model_dump(
        mode="json"
    )

    mutative = next(
        query
        for query in payload["queries"]
        if (
            query["stratum"]
            == "mutative_safety"
        )
    )

    mutative[
        "expected_behavior"
    ] = "answer"

    bank = RQ3QueryBank.model_validate(
        payload
    )

    validated = next(
        query
        for query in bank.queries
        if (
            query.stratum
            == "mutative_safety"
        )
    )

    assert (
        validated.expected_behavior
        == "answer"
    )


def test_build_run_plan_uses_same_bank_for_a_b_c_and_graph_ablation():
    bank = frozen_bank()

    plan = build_run_plan(
        bank,
        seed=20260904,
    )

    assert (
        len(plan)
        == len(bank.queries) * 4
    )

    assert (
        len(
            {
                entry.run_id
                for entry in plan
            }
        )
        == len(plan)
    )

    for query in bank.queries:
        variants = {
            (
                entry.condition,
                entry.graph_enabled,
            )
            for entry in plan
            if (
                entry.query_id
                == query.query_id
            )
        }

        assert variants == {
            (
                "A",
                True,
            ),
            (
                "B",
                True,
            ),
            (
                "C",
                True,
            ),
            (
                "C",
                False,
            ),
        }


def test_run_plan_is_deterministic_for_seed():
    bank = frozen_bank()

    first = [
        entry.run_id
        for entry in build_run_plan(
            bank,
            seed=77,
        )
    ]

    second = [
        entry.run_id
        for entry in build_run_plan(
            bank,
            seed=77,
        )
    ]

    different = [
        entry.run_id
        for entry in build_run_plan(
            bank,
            seed=78,
        )
    ]

    assert first == second
    assert first != different


def test_query_bank_hash_is_canonical_not_file_format_dependent():
    bank = frozen_bank()

    copied = (
        RQ3QueryBank.model_validate(
            bank.model_dump(
                mode="json"
            )
        )
    )

    assert (
        query_bank_hash(bank)
        == query_bank_hash(copied)
    )


def test_capture_completeness_rejects_missing_condition():
    bank = frozen_bank()

    captures = captures_for(
        bank
    )

    captures.pop()

    with pytest.raises(
        ValueError,
        match=(
            "capture matrix mismatch"
        ),
    ):
        validate_capture_completeness(
            bank,
            captures,
        )


def test_blind_packet_hides_condition_and_runtime_diagnostics():
    bank = frozen_bank()

    captures = captures_for(
        bank
    )

    packet, mapping = (
        build_blind_packet(
            bank,
            captures,
            seed=1234,
        )
    )

    assert (
        packet[
            "condition_identity_included"
        ]
        is False
    )

    assert (
        len(
            packet["responses"]
        )
        == len(bank.queries) * 4
    )

    assert (
        len(
            mapping["entries"]
        )
        == len(
            packet["responses"]
        )
    )

    serialized_packet = str(
        packet
    )

    assert (
        "experimentCondition"
        not in serialized_packet
    )

    assert (
        "graph_enabled"
        not in serialized_packet
    )

    assert (
        "writer_invoked"
        not in serialized_packet
    )

    assert (
        "end_to_end_latency_ms"
        not in serialized_packet
    )

    assert {
        entry["condition"]
        for entry in mapping[
            "entries"
        ]
    } == {
        "A",
        "B",
        "C",
    }

    assert {
        entry["graph_enabled"]
        for entry in mapping[
            "entries"
        ]
    } == {
        True,
        False,
    }


def test_blind_packet_is_deterministic_for_same_seed_except_timestamp():
    bank = frozen_bank()

    captures = captures_for(
        bank
    )

    (
        first_packet,
        first_mapping,
    ) = build_blind_packet(
        bank,
        captures,
        seed=9,
    )

    (
        second_packet,
        second_mapping,
    ) = build_blind_packet(
        bank,
        captures,
        seed=9,
    )

    assert [
        row["blind_id"]
        for row in first_packet[
            "responses"
        ]
    ] == [
        row["blind_id"]
        for row in second_packet[
            "responses"
        ]
    ]

    assert [
        row["answer"]
        for row in first_packet[
            "responses"
        ]
    ] == [
        row["answer"]
        for row in second_packet[
            "responses"
        ]
    ]

    assert [
        row["run_id"]
        for row in first_mapping[
            "entries"
        ]
    ] == [
        row["run_id"]
        for row in second_mapping[
            "entries"
        ]
    ]



def test_frozen_bank_requires_exact_six_queries_per_stratum():
    payload = frozen_bank().model_dump(
        mode="json"
    )

    payload["queries"].pop()

    with pytest.raises(
        ValidationError,
        match=(
            "exactly 6"
        ),
    ):
        RQ3QueryBank.model_validate(
            payload
        )
