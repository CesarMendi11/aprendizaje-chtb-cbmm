from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from scripts.experiments.common import utc_now_iso, write_json_atomic

RQ3_STRATA = (
    "locate_screen",
    "screen_purpose",
    "fields_and_search",
    "tables_and_columns",
    "controls_actions_and_navigation",
    "current_route_context",
    "ambiguity_and_clarification",
    "out_of_scope_abstention",
    "mutative_safety",
)

ExpectedBehavior = Literal["answer", "clarification", "abstention"]
Condition = Literal["A", "B", "C"]


class ExpectedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=100)
    canonical: str = Field(min_length=1, max_length=1000)
    acceptable_equivalents: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("claim_id", "canonical")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be blank")
        return cleaned

    @field_validator("acceptable_equivalents")
    @classmethod
    def normalize_equivalents(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("acceptable equivalents must not be blank")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("acceptable equivalents must be unique")
        return cleaned


class RQ3Query(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str = Field(min_length=1, max_length=100)

    stratum: Literal[
        "locate_screen",
        "screen_purpose",
        "fields_and_search",
        "tables_and_columns",
        "controls_actions_and_navigation",
        "current_route_context",
        "ambiguity_and_clarification",
        "out_of_scope_abstention",
        "mutative_safety",
    ]

    question: str = Field(min_length=1, max_length=2000)
    current_route: str | None = Field(default=None, max_length=500)

    expected_behavior: ExpectedBehavior

    required_claims: list[ExpectedClaim] = Field(
        default_factory=list,
        max_length=30,
    )

    prohibited_claims: list[str] = Field(
        default_factory=list,
        max_length=30,
    )

    notes_for_evaluator: str | None = Field(
        default=None,
        max_length=2000,
    )

    @field_validator("query_id", "question")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be blank")
        return cleaned

    @field_validator(
        "current_route",
        "notes_for_evaluator",
    )
    @classmethod
    def strip_optional_text(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        cleaned = value.strip()
        return cleaned or None

    @field_validator("prohibited_claims")
    @classmethod
    def normalize_prohibited_claims(
        cls,
        values: list[str],
    ) -> list[str]:
        cleaned = [
            value.strip()
            for value in values
        ]

        if any(
            not value
            for value in cleaned
        ):
            raise ValueError(
                "prohibited claims must not be blank"
            )

        if (
            len(set(cleaned))
            != len(cleaned)
        ):
            raise ValueError(
                "prohibited claims must be unique"
            )

        return cleaned

    @model_validator(mode="after")
    def validate_query_contract(
        self,
    ) -> RQ3Query:
        claim_ids = [
            claim.claim_id
            for claim in self.required_claims
        ]

        if (
            len(set(claim_ids))
            != len(claim_ids)
        ):
            raise ValueError(
                "required claim ids must be unique within a query"
            )

        if (
            self.stratum
            == "current_route_context"
            and self.current_route is None
        ):
            raise ValueError(
                "current_route_context queries require current_route"
            )

        if (
            self.stratum
            == "ambiguity_and_clarification"
            and self.expected_behavior
            != "clarification"
        ):
            raise ValueError(
                "ambiguity_and_clarification requires clarification behavior"
            )

        if (
            self.stratum
            == "out_of_scope_abstention"
            and self.expected_behavior
            != "abstention"
        ):
            raise ValueError(
                "out_of_scope_abstention requires abstention behavior"
            )

        return self


class RQ3QueryBank(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0.0"] = "1.0.0"

    status: Literal[
        "template",
        "draft",
        "frozen",
    ] = "draft"

    bank_id: str = Field(
        min_length=1,
        max_length=200,
    )

    queries: list[RQ3Query]

    @field_validator("bank_id")
    @classmethod
    def strip_bank_id(
        cls,
        value: str,
    ) -> str:
        cleaned = value.strip()

        if not cleaned:
            raise ValueError(
                "bank_id must not be blank"
            )

        return cleaned

    @model_validator(mode="after")
    def validate_bank_contract(
        self,
    ) -> RQ3QueryBank:
        query_ids = [
            query.query_id
            for query in self.queries
        ]

        if (
            len(set(query_ids))
            != len(query_ids)
        ):
            raise ValueError(
                "query_id values must be unique"
            )

        if self.status == "frozen":
            if not self.queries:
                raise ValueError(
                    "a frozen query bank must not be empty"
                )

            present = {
                query.stratum
                for query in self.queries
            }

            missing = (
                set(RQ3_STRATA)
                - present
            )

            if missing:
                raise ValueError(
                    "a frozen query bank must cover every "
                    "RQ3 stratum; missing: "
                    + ", ".join(
                        sorted(missing)
                    )
                )

        return self


class RunPlanEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    query_id: str
    condition: Condition
    graph_enabled: bool


class CaptureRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    query_id: str

    condition: Condition
    graph_enabled: bool

    question: str
    current_route: str | None = None

    answer: str
    status: str

    sources: list[
        dict[str, Any]
    ] = Field(
        default_factory=list
    )

    end_to_end_latency_ms: float = Field(
        ge=0
    )

    writer_invoked: bool

    raw_response: dict[
        str,
        Any,
    ] = Field(
        default_factory=dict
    )


class BlindResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blind_id: str
    query_id: str

    question: str
    current_route: str | None

    answer: str
    status: str

    sources: list[
        dict[str, Any]
    ]


def _canonical_json_bytes(
    value: Any,
) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
    ).encode(
        "utf-8"
    )


def canonical_sha256(
    value: Any,
) -> str:
    return hashlib.sha256(
        _canonical_json_bytes(
            value
        )
    ).hexdigest()


def load_query_bank(
    path: str | Path,
) -> RQ3QueryBank:
    payload = json.loads(
        Path(path).read_text(
            encoding="utf-8"
        )
    )

    return RQ3QueryBank.model_validate(
        payload
    )


def query_bank_hash(
    bank: RQ3QueryBank,
) -> str:
    return canonical_sha256(
        bank.model_dump(
            mode="json"
        )
    )


def build_run_plan(
    bank: RQ3QueryBank,
    *,
    seed: int,
) -> list[RunPlanEntry]:
    if bank.status != "frozen":
        raise ValueError(
            "run plans may only be built "
            "from a frozen query bank"
        )

    variants: tuple[
        tuple[
            Condition,
            bool,
        ],
        ...,
    ] = (
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
    )

    plan = [
        RunPlanEntry(
            run_id=(
                f"{query.query_id}:"
                f"{condition}:"
                f"g{int(graph_enabled)}"
            ),
            query_id=query.query_id,
            condition=condition,
            graph_enabled=graph_enabled,
        )
        for query in bank.queries
        for condition, graph_enabled
        in variants
    ]

    random.Random(
        seed
    ).shuffle(
        plan
    )

    return plan


def validate_capture_completeness(
    bank: RQ3QueryBank,
    captures: list[CaptureRecord],
) -> None:
    expected = {
        (
            entry.query_id,
            entry.condition,
            entry.graph_enabled,
        )
        for entry in build_run_plan(
            bank,
            seed=0,
        )
    }

    observed = {
        (
            capture.query_id,
            capture.condition,
            capture.graph_enabled,
        )
        for capture in captures
    }

    if (
        len(observed)
        != len(captures)
    ):
        raise ValueError(
            "captures contain duplicate "
            "query/condition/graph variants"
        )

    missing = (
        expected
        - observed
    )

    unexpected = (
        observed
        - expected
    )

    if (
        missing
        or unexpected
    ):
        raise ValueError(
            "capture matrix mismatch: "
            f"missing={sorted(missing)} "
            f"unexpected={sorted(unexpected)}"
        )


def build_blind_packet(
    bank: RQ3QueryBank,
    captures: list[CaptureRecord],
    *,
    seed: int,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
]:
    if bank.status != "frozen":
        raise ValueError(
            "blind packets require "
            "a frozen query bank"
        )

    validate_capture_completeness(
        bank,
        captures,
    )

    query_by_id = {
        query.query_id:
            query
        for query in bank.queries
    }

    rng = random.Random(
        seed
    )

    shuffled = list(
        captures
    )

    rng.shuffle(
        shuffled
    )

    blinded: list[
        BlindResponse
    ] = []

    mapping: list[
        dict[str, Any]
    ] = []

    for index, capture in enumerate(
        shuffled,
        start=1,
    ):
        blind_id = (
            f"R{index:04d}"
        )

        query = query_by_id[
            capture.query_id
        ]

        blinded.append(
            BlindResponse(
                blind_id=blind_id,
                query_id=query.query_id,
                question=query.question,
                current_route=query.current_route,
                answer=capture.answer,
                status=capture.status,
                sources=capture.sources,
            )
        )

        mapping.append(
            {
                "blind_id":
                    blind_id,

                "run_id":
                    capture.run_id,

                "query_id":
                    capture.query_id,

                "condition":
                    capture.condition,

                "graph_enabled":
                    capture.graph_enabled,

                "end_to_end_latency_ms":
                    capture.end_to_end_latency_ms,

                "writer_invoked":
                    capture.writer_invoked,
            }
        )

    packet = {
        "schema_version":
            "1.0.0",

        "packet_type":
            "rq3_blind_scoring_packet",

        "created_at":
            utc_now_iso(),

        "query_bank_id":
            bank.bank_id,

        "query_bank_sha256":
            query_bank_hash(
                bank
            ),

        "seed":
            seed,

        "condition_identity_included":
            False,

        "responses": [
            row.model_dump(
                mode="json"
            )
            for row in blinded
        ],
    }

    private_mapping = {
        "schema_version":
            "1.0.0",

        "mapping_type":
            "rq3_private_condition_mapping",

        "created_at":
            utc_now_iso(),

        "query_bank_id":
            bank.bank_id,

        "query_bank_sha256":
            query_bank_hash(
                bank
            ),

        "seed":
            seed,

        "entries":
            mapping,
    }

    return (
        packet,
        private_mapping,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "RQ3 experimental harness utilities."
        )
    )

    subparsers = (
        parser.add_subparsers(
            dest="command",
            required=True,
        )
    )

    validate = (
        subparsers.add_parser(
            "validate-bank"
        )
    )

    validate.add_argument(
        "bank"
    )

    plan = (
        subparsers.add_parser(
            "build-plan"
        )
    )

    plan.add_argument(
        "bank"
    )

    plan.add_argument(
        "--seed",
        type=int,
        required=True,
    )

    plan.add_argument(
        "--output",
        required=True,
    )

    blind = (
        subparsers.add_parser(
            "blind"
        )
    )

    blind.add_argument(
        "bank"
    )

    blind.add_argument(
        "captures"
    )

    blind.add_argument(
        "--seed",
        type=int,
        required=True,
    )

    blind.add_argument(
        "--packet-output",
        required=True,
    )

    blind.add_argument(
        "--mapping-output",
        required=True,
    )

    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    bank = load_query_bank(
        args.bank
    )

    if (
        args.command
        == "validate-bank"
    ):
        print(
            f"bank_id={bank.bank_id}"
        )

        print(
            f"status={bank.status}"
        )

        print(
            f"queries={len(bank.queries)}"
        )

        print(
            "sha256="
            f"{query_bank_hash(bank)}"
        )

        return 0

    if (
        args.command
        == "build-plan"
    ):
        plan = build_run_plan(
            bank,
            seed=args.seed,
        )

        payload = {
            "schema_version":
                "1.0.0",

            "plan_type":
                "rq3_condition_execution_plan",

            "created_at":
                utc_now_iso(),

            "query_bank_id":
                bank.bank_id,

            "query_bank_sha256":
                query_bank_hash(
                    bank
                ),

            "seed":
                args.seed,

            "entries": [
                entry.model_dump(
                    mode="json"
                )
                for entry in plan
            ],
        }

        output = write_json_atomic(
            args.output,
            payload,
        )

        print(
            output
        )

        return 0

    captures_payload = json.loads(
        Path(
            args.captures
        ).read_text(
            encoding="utf-8"
        )
    )

    captures = [
        CaptureRecord.model_validate(
            row
        )
        for row in captures_payload[
            "records"
        ]
    ]

    packet, mapping = (
        build_blind_packet(
            bank,
            captures,
            seed=args.seed,
        )
    )

    packet_output = (
        write_json_atomic(
            args.packet_output,
            packet,
        )
    )

    mapping_output = (
        write_json_atomic(
            args.mapping_output,
            mapping,
        )
    )

    print(
        packet_output
    )

    print(
        mapping_output
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
