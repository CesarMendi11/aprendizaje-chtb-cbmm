from __future__ import annotations

import json

from scripts.experiments.reference_harness import (
    SemanticReference,
)
from scripts.experiments.rq3_harness import (
    CaptureRecord,
    RQ3QueryBank,
    build_blind_packet,
    build_run_plan,
)
from scripts.experiments.scoring_harness import (
    ClaimMatch,
    RQ2HitlEffort,
    RQ2ScoringSet,
    RQ2StageScore,
    RQ3ResponseScore,
    RQ3ScoringSet,
    aggregate_rq2,
    aggregate_rq3,
)


def semantic_reference() -> SemanticReference:
    return SemanticReference.model_validate(
        {
            "schema_version":
                "1.0.0",

            "status":
                "frozen",

            "reference_id":
                "rq2-test",

            "author_role":
                "expert_A",

            "source_basis":
                (
                    "direct_erp_inspection_and_"
                    "expert_knowledge"
                ),

            "formal_semantic_proposals_seen_before_freeze":
                False,

            "screens": [
                {
                    "route":
                        "/admin/a",

                    "title":
                        "A",

                    "expected_purpose":
                        "Gestiona A.",

                    "expected_capabilities":
                        [
                            {
                                "claim_id":
                                    "A1",

                                "statement":
                                    "Consulta A."
                            },
                            {
                                "claim_id":
                                    "A2",

                                "statement":
                                    "Filtra A."
                            },
                        ],
                },
                {
                    "route":
                        "/admin/b",

                    "title":
                        "B",

                    "expected_purpose":
                        "Gestiona B.",

                    "expected_capabilities":
                        [
                            {
                                "claim_id":
                                    "B1",

                                "statement":
                                    "Consulta B."
                            }
                        ],
                },
            ],
        }
    )


def test_rq2_aggregates_pre_post_and_hitl_effort():
    reference = semantic_reference()

    scoring = RQ2ScoringSet(
        reference_id=
            reference.reference_id,

        records=[
            RQ2StageScore(
                route="/admin/a",
                stage="pre_hitl",
                output_available=True,
                purpose_rating="partial",
                output_claim_ids=[
                    "o1",
                    "o2",
                ],
                matches=[
                    ClaimMatch(
                        output_claim_id="o1",
                        reference_claim_id="A1",
                    )
                ],
            ),
            RQ2StageScore(
                route="/admin/b",
                stage="pre_hitl",
                output_available=False,
                purpose_rating="not_available",
            ),
            RQ2StageScore(
                route="/admin/a",
                stage="post_hitl",
                output_available=True,
                purpose_rating="correct",
                output_claim_ids=[
                    "o1",
                    "o2",
                ],
                matches=[
                    ClaimMatch(
                        output_claim_id="o1",
                        reference_claim_id="A1",
                    ),
                    ClaimMatch(
                        output_claim_id="o2",
                        reference_claim_id="A2",
                    ),
                ],
            ),
            RQ2StageScore(
                route="/admin/b",
                stage="post_hitl",
                output_available=True,
                purpose_rating="correct",
                output_claim_ids=[
                    "o1"
                ],
                matches=[
                    ClaimMatch(
                        output_claim_id="o1",
                        reference_claim_id="B1",
                    )
                ],
            ),
        ],

        hitl_effort=[
            RQ2HitlEffort(
                route="/admin/a",
                action="correct",
                review_duration_ms=1200,
                retained_claims=1,
                changed_claims=1,
            ),
            RQ2HitlEffort(
                route="/admin/b",
                action="approve",
                review_duration_ms=800,
                retained_claims=1,
            ),
        ],
    )

    result = aggregate_rq2(
        reference,
        scoring,
    )

    pre = result[
        "stages"
    ][
        "pre_hitl"
    ]

    post = result[
        "stages"
    ][
        "post_hitl"
    ]

    assert (
        pre["generation_coverage"]
        == 0.5
    )

    assert pre["claims"] == {
        "tp": 1,
        "fp": 1,
        "fn": 2,
        "precision": 0.5,
        "recall": 1 / 3,
        "f1": 0.4,
    }

    assert (
        pre[
            "unsupported_claim_rate"
        ]
        == 0.5
    )

    assert post["claims"] == {
        "tp": 3,
        "fp": 0,
        "fn": 0,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
    }

    assert (
        result[
            "hitl_effort"
        ][
            "total_duration_ms"
        ]
        == 2000
    )

    assert (
        result[
            "hitl_effort"
        ][
            "action_counts"
        ]
        == {
            "approve": 1,
            "correct": 1,
        }
    )


def rq3_bank() -> RQ3QueryBank:
    definitions = [
        (
            "locate_screen",
            "answer",
        ),
        (
            "screen_purpose",
            "answer",
        ),
        (
            "fields_and_search",
            "answer",
        ),
        (
            "tables_and_columns",
            "answer",
        ),
        (
            "controls_actions_and_navigation",
            "answer",
        ),
        (
            "current_route_context",
            "answer",
        ),
        (
            "ambiguity_and_clarification",
            "clarification",
        ),
        (
            "out_of_scope_abstention",
            "abstention",
        ),
        (
            "mutative_safety",
            "abstention",
        ),
    ]

    queries = []
    index = 1

    for (
        stratum,
        behavior,
    ) in definitions:
        for _ in range(6):
            query_id = (
                f"Q{index:03d}"
            )

            query = {
                "query_id":
                    query_id,

                "stratum":
                    stratum,

                "question":
                    f"Pregunta {index}",

                "expected_behavior":
                    behavior,

                "required_claims":
                    [],

                "prohibited_claims":
                    [],
            }

            if (
                behavior
                == "answer"
            ):
                query[
                    "required_claims"
                ] = [
                    {
                        "claim_id":
                            f"{query_id}-C1",

                        "canonical":
                            "Hecho esperado.",
                    }
                ]

            if (
                stratum
                == "current_route_context"
            ):
                query[
                    "current_route"
                ] = "/admin/a"

            if (
                stratum
                == "mutative_safety"
            ):
                query[
                    "prohibited_claims"
                ] = [
                    (
                        "Afirmar que la acción "
                        "mutativa fue ejecutada."
                    )
                ]

            queries.append(
                query
            )

            index += 1

    return RQ3QueryBank.model_validate(
        {
            "schema_version":
                "1.0.0",

            "status":
                "frozen",

            "bank_id":
                "rq3-test",

            "queries":
                queries,
        }
    )

def test_rq3_aggregates_condition_metrics_without_using_condition_in_blind_packet():
    bank = rq3_bank()

    query_by_id = {
        query.query_id:
            query
        for query
        in bank.queries
    }

    captures = []

    for entry in build_run_plan(
        bank,
        seed=10,
    ):
        query = query_by_id[
            entry.query_id
        ]

        captures.append(
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
                    query.question,

                current_route=
                    query.current_route,

                answer=
                    "Respuesta controlada.",

                status=
                    "answered",

                sources=[],

                end_to_end_latency_ms=
                    100.0,

                writer_invoked=(
                    entry.condition
                    == "C"
                ),
            )
        )

    packet, mapping = (
        build_blind_packet(
            bank,
            captures,
            seed=88,
        )
    )

    scores = []

    packet_by_id = {
        row["blind_id"]:
            row
        for row
        in packet["responses"]
    }

    for blind_id, row in (
        packet_by_id.items()
    ):
        query = query_by_id[
            row["query_id"]
        ]

        if query.required_claims:
            output_claim_ids = [
                "o1"
            ]

            matches = [
                ClaimMatch(
                    output_claim_id="o1",
                    reference_claim_id=
                        query.required_claims[
                            0
                        ].claim_id,
                )
            ]

            grounded = [
                "o1"
            ]

        else:
            output_claim_ids = []
            matches = []
            grounded = []

        scores.append(
            RQ3ResponseScore(
                blind_id=
                    blind_id,

                output_claim_ids=
                    output_claim_ids,

                matches=
                    matches,

                grounded_output_claim_ids=
                    grounded,

                expected_behavior_correct=
                    True,

                safety_compliant=(
                    True
                    if (
                        query.stratum
                        == "mutative_safety"
                    )
                    else None
                ),
            )
        )

    result = aggregate_rq3(
        bank,
        packet,
        mapping,
        RQ3ScoringSet(
            records=scores
        ),
    )

    assert (
        set(
            result[
                "conditions"
            ]
        )
        == {
            "A_graph_on",
            "B_graph_on",
            "C_graph_on",
            "C_graph_off",
        }
    )

    for condition in (
        result[
            "conditions"
        ].values()
    ):
        overall = condition[
            "overall"
        ]

        assert (
            overall[
                "expected_behavior_accuracy"
            ]
            == 1.0
        )

        assert (
            overall[
                "claims"
            ][
                "precision"
            ]
            == 1.0
        )

        assert (
            overall[
                "claims"
            ][
                "recall"
            ]
            == 1.0
        )

        assert (
            overall[
                "grounding_rate"
            ]
            == 1.0
        )

        assert (
            overall[
                "supported_claim_rate"
            ]
            == 1.0
        )

        assert (
            overall[
                "unsupported_claim_rate"
            ]
            == 0.0
        )

        assert (
            overall[
                "median_latency_ms"
            ]
            == 100.0
        )

        assert (
            overall[
                "p50_latency_ms"
            ]
            == 100.0
        )

        assert (
            overall[
                "p95_latency_ms"
            ]
            == 100.0
        )

    assert (
        result[
            "conditions"
        ][
            "C_graph_on"
        ][
            "overall"
        ][
            "writer_invocation_rate"
        ]
        == 1.0
    )

    assert (
        result[
            "conditions"
        ][
            "A_graph_on"
        ][
            "overall"
        ][
            "writer_invocation_rate"
        ]
        == 0.0
    )


def test_rq3_reports_mutative_safety_separately():
    bank = rq3_bank()

    query_by_id = {
        query.query_id:
            query
        for query
        in bank.queries
    }

    captures = [
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

            current_route=
                query_by_id[
                    entry.query_id
                ].current_route,

            answer=
                "Respuesta.",

            status=
                "answered",

            sources=[],

            end_to_end_latency_ms=
                50.0,

            writer_invoked=
                False,
        )
        for entry
        in build_run_plan(
            bank,
            seed=1,
        )
    ]

    packet, mapping = (
        build_blind_packet(
            bank,
            captures,
            seed=2,
        )
    )

    scores = []

    for row in packet[
        "responses"
    ]:
        query = query_by_id[
            row["query_id"]
        ]

        scores.append(
            RQ3ResponseScore(
                blind_id=
                    row["blind_id"],

                expected_behavior_correct=
                    True,

                safety_compliant=(
                    True
                    if (
                        query.stratum
                        == "mutative_safety"
                    )
                    else None
                ),
            )
        )

    result = aggregate_rq3(
        bank,
        packet,
        mapping,
        RQ3ScoringSet(
            records=scores
        ),
    )

    for condition in (
        result[
            "conditions"
        ].values()
    ):
        assert (
            condition[
                "by_stratum"
            ][
                "mutative_safety"
            ][
                "safety_compliance_rate"
            ]
            == 1.0
        )


def test_rq2_policy_scope_excludes_blocked_reference_routes(tmp_path):
    from scripts.experiments.policy_scope import load_policy_scope

    reference = semantic_reference()
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "contract_id": "policy-test",
                "status": "FROZEN_PRE_FORMAL002_INPUT",
                "policy_source": {
                    "blocked_route_prefix": "/admin/b",
                },
                "policy_blocked_routes": [
                    "/admin/b",
                ],
            }
        ),
        encoding="utf-8",
    )
    policy_scope = load_policy_scope(policy_path)

    scoring = RQ2ScoringSet(
        reference_id=reference.reference_id,
        records=[
            RQ2StageScore(
                route="/admin/a",
                stage="pre_hitl",
                output_available=False,
                purpose_rating="not_available",
            ),
            RQ2StageScore(
                route="/admin/a",
                stage="post_hitl",
                output_available=True,
                purpose_rating="correct",
                output_claim_ids=["o1", "o2"],
                matches=[
                    ClaimMatch(
                        output_claim_id="o1",
                        reference_claim_id="A1",
                    ),
                    ClaimMatch(
                        output_claim_id="o2",
                        reference_claim_id="A2",
                    ),
                ],
            ),
        ],
        hitl_effort=[
            RQ2HitlEffort(
                route="/admin/a",
                action="correct",
                review_duration_ms=500,
                human_added_claims=2,
            ),
        ],
    )

    result = aggregate_rq2(
        reference,
        scoring,
        policy_scope,
    )

    assert result["screens"] == 1
    assert result["policy_scope"]["reference_screens_total"] == 2
    assert result["policy_scope"]["policy_blocked_screens"] == 1
    assert result["policy_scope"]["primary_eligible_screens"] == 1
    assert result["stages"]["pre_hitl"]["generation_coverage"] == 0.0
    assert result["stages"]["pre_hitl"]["claims"]["fn"] == 2
    assert result["stages"]["post_hitl"]["claims"]["recall"] == 1.0
