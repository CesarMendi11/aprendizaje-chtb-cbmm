from scripts.certification.run_m3_9_conversational_matrix import (
    SourceExpectation,
    TurnExpectation,
    _percentile,
    default_matrix,
    evaluate_payload,
    load_matrix,
)


def payload(**overrides):
    base = {
        "answer": 'La pantalla "Año" está dentro del módulo "General".',
        "conversationId": "conv",
        "status": "answered",
        "sources": [
            {"title": "Año", "route": "/admin/general/anios", "sourceType": "screen"},
            {"title": "General", "route": "", "sourceType": "module"},
        ],
        "answer_mode": "deterministic_graph",
        "answerDecision": {
            "decision": "DETERMINISTIC_ANSWER",
            "reason": "deterministic_structural_answer",
            "intent": "LOCATE_SCREEN",
            "confidence": "high",
        },
        "intent": "LOCATE_SCREEN",
        "confidence": "high",
        "evidence_ids": ["screen:ano", "module:general"],
        "retrieval": {
            "selected_sources": 2,
            "selected_relations": 1,
            "selected_semantics": 0,
        },
    }
    base.update(overrides)
    return base


def test_evaluate_payload_accepts_expected_contract():
    expectation = TurnExpectation(
        intent="LOCATE_SCREEN",
        decision="DETERMINISTIC_ANSWER",
        reason="deterministic_structural_answer",
        status="answered",
        answer_contains=("Año", "General"),
        required_sources=(
            SourceExpectation("Año", "screen"),
            SourceExpectation("General", "module"),
        ),
        allowed_source_types=("screen", "module"),
        retrieval_exact=(("selected_sources", 2),),
    )

    assert evaluate_payload(payload(), expectation) == []


def test_evaluate_payload_detects_cross_entity_leak_and_wrong_source_type():
    expectation = TurnExpectation(
        decision="ABSTENTION",
        reason="screen_purpose_semantic_missing",
        forbidden_source_titles=("Año",),
        allowed_source_types=("screen",),
        retrieval_exact=(("selected_semantics", 0),),
    )
    candidate = payload(
        answer="No sé para qué sirve Dashboard; screen:ano no corresponde.",
        sources=[
            {"title": "Dashboard", "route": "/admin/home", "sourceType": "screen"},
            {"title": "Año", "route": "/admin/general/anios", "sourceType": "module"},
        ],
        answerDecision={
            "decision": "DETERMINISTIC_ANSWER",
            "reason": "approved_semantic_answer",
        },
        retrieval={"selected_semantics": 1},
    )

    errors = evaluate_payload(candidate, expectation)

    assert any("decision expected" in error for error in errors)
    assert any("reason expected" in error for error in errors)
    assert any("forbidden source title" in error for error in errors)
    assert any("unexpected source types" in error for error in errors)
    assert any("leaked canonical id prefix" in error for error in errors)
    assert any("selected_semantics expected 0" in error for error in errors)


def test_evaluate_payload_detects_missing_contract_keys():
    candidate = payload()
    del candidate["conversationId"]
    del candidate["answerDecision"]

    errors = evaluate_payload(candidate, TurnExpectation())

    assert any("missing response keys" in error for error in errors)
    assert any("answerDecision must be an object" in error for error in errors)


def test_matrix_contains_multi_turn_ambiguity_typo_and_root_cases():
    scenarios = {scenario.name: scenario for scenario in default_matrix()}

    assert "year_typo_rrf_recovery" in scenarios
    assert len(scenarios["year_controlled_multi_turn"].turns) >= 4
    assert "ruc_ambiguity" in scenarios
    assert "missing_conversation_reference" in scenarios
    assert len(scenarios["dashboard_cross_entity_grounding"].turns) == 2


def test_percentile_uses_nearest_rank():
    values = [10.0, 20.0, 30.0, 40.0]

    assert _percentile(values, 0.50) == 20.0
    assert _percentile(values, 0.95) == 40.0
    assert _percentile([], 0.95) == 0.0


def test_load_matrix_reads_instance_expectations_from_external_config(tmp_path):
    matrix_path = tmp_path / "matrix.json"
    matrix_path.write_text(
        """{
          "scenarios": [
            {
              "name": "generic",
              "turns": [
                {
                  "name": "locate",
                  "question": "Where is Products?",
                  "expectation": {
                    "intent": "LOCATE_SCREEN",
                    "required_sources": [
                      {"title": "Products", "source_type": "screen"}
                    ],
                    "retrieval_exact": [["selected_sources", 1]]
                  }
                }
              ]
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    scenarios = load_matrix(matrix_path)

    assert len(scenarios) == 1
    turn = scenarios[0].turns[0]
    assert turn.question == "Where is Products?"
    assert turn.expectation.required_sources == (SourceExpectation("Products", "screen"),)
    assert turn.expectation.retrieval_exact == (("selected_sources", 1),)


def test_matrix_covers_every_query_intent():
    scenarios = default_matrix()
    intents = {
        turn.expectation.intent
        for scenario in scenarios
        for turn in scenario.turns
        if turn.expectation.intent is not None
    }

    assert intents == {
        "MUTATIVE_ACTION",
        "SCREEN_PURPOSE",
        "SEARCH_BY_FIELD",
        "LIST_FIELDS",
        "LOCATE_FIELD",
        "LOCATE_SCREEN",
        "FIND_CONTROL",
        "LIST_COLUMNS",
        "NAVIGATION_EVENT",
    }


def test_matrix_has_cross_entity_safety_and_out_of_domain_cases():
    scenarios = {scenario.name: scenario for scenario in default_matrix()}

    assert len(scenarios["comprobantes_full_intent_surface"].turns) == 6
    assert len(scenarios["mutative_guidance_without_execution"].turns) == 3
    assert len(scenarios["explicit_cross_entity_round_trip"].turns) == 5
    assert len(scenarios["out_of_domain_fail_closed"].turns) == 1

    total_turns = sum(len(scenario.turns) for scenario in scenarios.values())
    assert total_turns >= 26
