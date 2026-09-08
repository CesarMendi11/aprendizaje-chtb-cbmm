from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from scripts.experiments.reference_harness import (
    SemanticReference,
    load_semantic_reference,
    semantic_reference_hash,
    semantic_reference_summary,
    structural_reference_summary,
)


def semantic_payload() -> dict:
    return {
        "schema_version":
            "1.0.0",

        "status":
            "frozen",

        "reference_id":
            "rq2-reference-test-v1",

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
                    "/admin/general/personas",

                "title":
                    "Personas",

                "module_path":
                    "General",

                "expected_purpose":
                    (
                        "Consultar y gestionar "
                        "información de personas."
                    ),

                "purpose_equivalents":
                    [
                        (
                            "Permite trabajar con "
                            "información de personas."
                        )
                    ],

                "expected_capabilities":
                    [
                        {
                            "claim_id":
                                "personas-c1",

                            "statement":
                                (
                                    "Permite consultar "
                                    "registros de personas."
                                ),

                            "acceptable_equivalents":
                                [
                                    (
                                        "Permite buscar "
                                        "personas registradas."
                                    )
                                ],
                        },
                        {
                            "claim_id":
                                "personas-c2",

                            "statement":
                                (
                                    "Presenta información "
                                    "asociada a cada persona."
                                ),

                            "acceptable_equivalents":
                                [],
                        },
                    ],

                "expert_notes":
                    None,
            }
        ],
    }


def test_semantic_reference_accepts_frozen_independent_reference():
    reference = (
        SemanticReference.model_validate(
            semantic_payload()
        )
    )

    assert (
        reference.status
        == "frozen"
    )

    assert (
        len(reference.screens)
        == 1
    )

    assert (
        len(
            reference.screens[
                0
            ].expected_capabilities
        )
        == 2
    )


def test_frozen_semantic_reference_must_not_be_empty():
    payload = semantic_payload()
    payload["screens"] = []

    with pytest.raises(
        ValidationError,
        match=(
            "must not be empty"
        ),
    ):
        SemanticReference.model_validate(
            payload
        )


def test_frozen_semantic_reference_rejects_prior_formal_output_exposure():
    payload = semantic_payload()

    payload[
        "formal_semantic_proposals_seen_before_freeze"
    ] = True

    with pytest.raises(
        ValidationError,
        match=(
            "before formal "
            "SemanticProposal output is seen"
        ),
    ):
        SemanticReference.model_validate(
            payload
        )


def test_semantic_reference_rejects_duplicate_screen_identity():
    payload = semantic_payload()

    payload["screens"].append(
        dict(
            payload["screens"][0]
        )
    )

    with pytest.raises(
        ValidationError,
        match=(
            "must be unique"
        ),
    ):
        SemanticReference.model_validate(
            payload
        )


def test_semantic_reference_rejects_duplicate_route_even_if_titles_differ():
    payload = semantic_payload()

    duplicate_route = dict(
        payload["screens"][0]
    )

    duplicate_route["title"] = (
        "Personas - título alternativo"
    )

    payload["screens"].append(
        duplicate_route
    )

    with pytest.raises(
        ValidationError,
        match=(
            "semantic screen routes "
            "must be unique"
        ),
    ):
        SemanticReference.model_validate(
            payload
        )


def test_semantic_reference_rejects_duplicate_claim_id():
    payload = semantic_payload()

    duplicate = dict(
        payload["screens"][0][
            "expected_capabilities"
        ][0]
    )

    payload["screens"][0][
        "expected_capabilities"
    ].append(
        duplicate
    )

    with pytest.raises(
        ValidationError,
        match=(
            "claim_id values must be unique"
        ),
    ):
        SemanticReference.model_validate(
            payload
        )


def test_semantic_reference_hash_is_canonical():
    first = (
        SemanticReference.model_validate(
            semantic_payload()
        )
    )

    second = (
        SemanticReference.model_validate(
            json.loads(
                json.dumps(
                    semantic_payload(),
                    ensure_ascii=False,
                )
            )
        )
    )

    assert (
        semantic_reference_hash(
            first
        )
        == semantic_reference_hash(
            second
        )
    )


def test_semantic_template_loads_and_summarizes(tmp_path):
    payload = semantic_payload()

    payload["status"] = (
        "template"
    )

    payload["screens"] = []

    path = (
        tmp_path
        / "semantic.json"
    )

    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loaded = (
        load_semantic_reference(
            path
        )
    )

    summary = (
        semantic_reference_summary(
            path
        )
    )

    assert (
        loaded.status
        == "template"
    )

    assert (
        summary["screens"]
        == 0
    )

    assert (
        summary[
            "expected_capability_claims"
        ]
        == 0
    )

    assert len(
        summary["file_sha256"]
    ) == 64

    assert len(
        summary["canonical_sha256"]
    ) == 64


def test_structural_reference_summary_uses_existing_rq1_contract(
    tmp_path,
):
    path = (
        tmp_path
        / "reference.csv"
    )

    path.write_text(
        (
            "entity_type,parent_module_path,"
            "name,route,notes\n"
            "module,,General,,\n"
            "screen,General,Personas,"
            "/admin/general/personas,\n"
        ),
        encoding="utf-8",
    )

    summary = (
        structural_reference_summary(
            path
        )
    )

    assert (
        summary["items"]
        == 2
    )

    assert (
        summary["modules"]
        == 1
    )

    assert (
        summary["screens"]
        == 1
    )

    assert len(
        summary["file_sha256"]
    ) == 64


def test_structural_reference_summary_rejects_empty_template(
    tmp_path,
):
    path = (
        tmp_path
        / "reference.csv"
    )

    path.write_text(
        (
            "entity_type,parent_module_path,"
            "name,route,notes\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=(
            "Gold Standard estructural "
            "está vacío"
        ),
    ):
        structural_reference_summary(
            path
        )
