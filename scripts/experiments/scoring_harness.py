from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from scripts.experiments.common import (
    utc_now_iso,
    write_json_atomic,
)
from scripts.experiments.reference_harness import (
    SemanticReference,
    load_semantic_reference,
    semantic_reference_hash,
)
from scripts.experiments.rq3_harness import (
    RQ3QueryBank,
    load_query_bank,
    query_bank_hash,
)


class ClaimMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_claim_id: str = Field(
        min_length=1,
        max_length=100,
    )
    reference_claim_id: str = Field(
        min_length=1,
        max_length=100,
    )


class RQ2StageScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: str = Field(
        min_length=1,
        max_length=500,
    )

    stage: Literal[
        "pre_hitl",
        "post_hitl",
    ]

    output_available: bool

    purpose_rating: Literal[
        "correct",
        "partial",
        "incorrect",
        "not_available",
    ]

    output_claim_ids: list[str] = Field(
        default_factory=list,
        max_length=100,
    )

    matches: list[ClaimMatch] = Field(
        default_factory=list,
        max_length=100,
    )

    @model_validator(mode="after")
    def validate_score(
        self,
    ) -> RQ2StageScore:
        if (
            len(set(self.output_claim_ids))
            != len(self.output_claim_ids)
        ):
            raise ValueError(
                "output_claim_ids must be unique"
            )

        if not self.output_available:
            if self.output_claim_ids:
                raise ValueError(
                    "unavailable output must not "
                    "contain output claims"
                )

            if self.matches:
                raise ValueError(
                    "unavailable output must not "
                    "contain claim matches"
                )

            if (
                self.purpose_rating
                != "not_available"
            ):
                raise ValueError(
                    "unavailable output requires "
                    "purpose_rating=not_available"
                )

        elif (
            self.purpose_rating
            == "not_available"
        ):
            raise ValueError(
                "available output may not use "
                "purpose_rating=not_available"
            )

        matched_outputs = [
            row.output_claim_id
            for row in self.matches
        ]

        matched_refs = [
            row.reference_claim_id
            for row in self.matches
        ]

        if (
            len(set(matched_outputs))
            != len(matched_outputs)
        ):
            raise ValueError(
                "one output atomic claim may match "
                "at most one reference atomic claim"
            )

        if (
            len(set(matched_refs))
            != len(matched_refs)
        ):
            raise ValueError(
                "one reference atomic claim may be "
                "matched at most once"
            )

        if not set(
            matched_outputs
        ).issubset(
            self.output_claim_ids
        ):
            raise ValueError(
                "matches reference unknown "
                "output_claim_id"
            )

        return self


class RQ2HitlEffort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: str = Field(
        min_length=1,
        max_length=500,
    )

    action: Literal[
        "approve",
        "correct",
        "reject",
    ]

    review_duration_ms: int = Field(
        ge=0
    )

    retained_claims: int = Field(
        default=0,
        ge=0,
    )

    changed_claims: int = Field(
        default=0,
        ge=0,
    )

    removed_claims: int = Field(
        default=0,
        ge=0,
    )

    human_added_claims: int = Field(
        default=0,
        ge=0,
    )


class RQ2ScoringSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[
        "1.0.0"
    ] = "1.0.0"

    evaluation_type: Literal[
        "rq2_semantic_quality"
    ] = "rq2_semantic_quality"

    reference_id: str

    records: list[RQ2StageScore]

    hitl_effort: list[
        RQ2HitlEffort
    ] = Field(
        default_factory=list
    )


class RQ3ResponseScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blind_id: str = Field(
        min_length=1,
        max_length=100,
    )

    output_claim_ids: list[str] = Field(
        default_factory=list,
        max_length=100,
    )

    matches: list[ClaimMatch] = Field(
        default_factory=list,
        max_length=100,
    )

    grounded_output_claim_ids: list[str] = Field(
        default_factory=list,
        max_length=100,
    )

    expected_behavior_correct: bool

    prohibited_claim_indices_present: list[
        int
    ] = Field(
        default_factory=list,
        max_length=100,
    )

    safety_compliant: bool | None = None

    @model_validator(mode="after")
    def validate_score(
        self,
    ) -> RQ3ResponseScore:
        if (
            len(set(self.output_claim_ids))
            != len(self.output_claim_ids)
        ):
            raise ValueError(
                "output_claim_ids must be unique"
            )

        matched_outputs = [
            row.output_claim_id
            for row in self.matches
        ]

        matched_refs = [
            row.reference_claim_id
            for row in self.matches
        ]

        if (
            len(set(matched_outputs))
            != len(matched_outputs)
        ):
            raise ValueError(
                "matched output claims must be unique"
            )

        if (
            len(set(matched_refs))
            != len(matched_refs)
        ):
            raise ValueError(
                "matched reference claims must be unique"
            )

        if not set(
            matched_outputs
        ).issubset(
            self.output_claim_ids
        ):
            raise ValueError(
                "matches contain unknown output claim"
            )

        if not set(
            self.grounded_output_claim_ids
        ).issubset(
            self.output_claim_ids
        ):
            raise ValueError(
                "grounded claims must belong "
                "to output_claim_ids"
            )

        if (
            len(
                set(
                    self.grounded_output_claim_ids
                )
            )
            != len(
                self.grounded_output_claim_ids
            )
        ):
            raise ValueError(
                "grounded output claims must be unique"
            )

        if any(
            index < 0
            for index
            in self.prohibited_claim_indices_present
        ):
            raise ValueError(
                "prohibited claim indices "
                "must be non-negative"
            )

        if (
            len(
                set(
                    self.prohibited_claim_indices_present
                )
            )
            != len(
                self.prohibited_claim_indices_present
            )
        ):
            raise ValueError(
                "prohibited claim indices "
                "must be unique"
            )

        return self


class RQ3ScoringSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[
        "1.0.0"
    ] = "1.0.0"

    evaluation_type: Literal[
        "rq3_blind_answer_scoring"
    ] = "rq3_blind_answer_scoring"

    records: list[RQ3ResponseScore]

    @model_validator(mode="after")
    def validate_unique_blind_ids(
        self,
    ) -> RQ3ScoringSet:
        ids = [
            row.blind_id
            for row in self.records
        ]

        if (
            len(set(ids))
            != len(ids)
        ):
            raise ValueError(
                "blind_id values must be unique"
            )

        return self


def _claim_metrics(
    tp: int,
    fp: int,
    fn: int,
) -> dict[str, Any]:
    precision = (
        tp / (tp + fp)
        if tp + fp
        else None
    )

    recall = (
        tp / (tp + fn)
        if tp + fn
        else None
    )

    if (
        precision is None
        or recall is None
    ):
        f1 = None

    elif precision + recall:
        f1 = (
            2
            * precision
            * recall
            / (
                precision
                + recall
            )
        )

    else:
        f1 = 0.0

    return {
        "tp":
            tp,

        "fp":
            fp,

        "fn":
            fn,

        "precision":
            precision,

        "recall":
            recall,

        "f1":
            f1,
    }


def _safe_rate(
    numerator: int,
    denominator: int,
) -> float | None:
    if denominator == 0:
        return None

    return (
        numerator
        / denominator
    )


def _percentile_linear(
    values: list[float],
    quantile: float,
) -> float | None:
    if not values:
        return None

    if not (
        0.0
        <= quantile
        <= 1.0
    ):
        raise ValueError(
            "quantile must be in [0, 1]"
        )

    ordered = sorted(
        float(value)
        for value in values
    )

    if len(ordered) == 1:
        return ordered[0]

    position = (
        (len(ordered) - 1)
        * quantile
    )

    lower = int(
        position
    )

    upper = min(
        lower + 1,
        len(ordered) - 1,
    )

    fraction = (
        position
        - lower
    )

    return (
        ordered[lower]
        + (
            ordered[upper]
            - ordered[lower]
        )
        * fraction
    )


def _load_json(
    path: str | Path,
) -> dict[str, Any]:
    return json.loads(
        Path(path).read_text(
            encoding="utf-8"
        )
    )


def aggregate_rq2(
    reference: SemanticReference,
    scoring: RQ2ScoringSet,
) -> dict[str, Any]:
    if (
        scoring.reference_id
        != reference.reference_id
    ):
        raise ValueError(
            "RQ2 scoring reference_id "
            "does not match reference"
        )

    reference_by_route = {
        screen.route:
            screen
        for screen
        in reference.screens
    }

    expected_keys = {
        (
            route,
            stage,
        )
        for route
        in reference_by_route
        for stage
        in (
            "pre_hitl",
            "post_hitl",
        )
    }

    observed_keys = [
        (
            row.route,
            row.stage,
        )
        for row
        in scoring.records
    ]

    if (
        len(set(observed_keys))
        != len(observed_keys)
    ):
        raise ValueError(
            "duplicate RQ2 route/stage score"
        )

    observed_set = set(
        observed_keys
    )

    if (
        observed_set
        != expected_keys
    ):
        raise ValueError(
            "RQ2 score matrix does not "
            "cover every reference screen "
            "at pre_hitl and post_hitl"
        )

    stage_metrics = {}

    for stage in (
        "pre_hitl",
        "post_hitl",
    ):
        rows = [
            row
            for row
            in scoring.records
            if row.stage == stage
        ]

        tp = 0
        fp = 0
        fn = 0

        available = 0

        purposes = Counter()

        for row in rows:
            reference_screen = (
                reference_by_route[
                    row.route
                ]
            )

            reference_claim_ids = {
                claim.claim_id
                for claim
                in reference_screen.expected_capabilities
            }

            matched_reference_ids = {
                match.reference_claim_id
                for match
                in row.matches
            }

            if not (
                matched_reference_ids
                <= reference_claim_ids
            ):
                raise ValueError(
                    "RQ2 score contains "
                    "unknown reference claim id "
                    f"for route {row.route}"
                )

            matched_output_ids = {
                match.output_claim_id
                for match
                in row.matches
            }

            tp += len(
                row.matches
            )

            fp += (
                len(
                    row.output_claim_ids
                )
                - len(
                    matched_output_ids
                )
            )

            fn += (
                len(
                    reference_claim_ids
                )
                - len(
                    matched_reference_ids
                )
            )

            if row.output_available:
                available += 1

            purposes[
                row.purpose_rating
            ] += 1

        stage_metrics[
            stage
        ] = {
            "screens":
                len(rows),

            "output_available":
                available,

            "generation_coverage":
                _safe_rate(
                    available,
                    len(rows),
                ),

            "claims":
                _claim_metrics(
                    tp,
                    fp,
                    fn,
                ),

            "unsupported_claims":
                fp,

            "unsupported_claim_rate":
                _safe_rate(
                    fp,
                    tp + fp,
                ),

            "purpose_ratings":
                dict(
                    sorted(
                        purposes.items()
                    )
                ),

            "purpose_exact_accuracy":
                _safe_rate(
                    purposes[
                        "correct"
                    ],
                    len(rows),
                ),
        }

    effort = scoring.hitl_effort

    if effort:
        effort_routes = [
            row.route
            for row in effort
        ]

        if (
            len(set(effort_routes))
            != len(effort_routes)
        ):
            raise ValueError(
                "RQ2 HITL effort contains "
                "duplicate screen routes"
            )

        if not set(
            effort_routes
        ).issubset(
            reference_by_route
        ):
            raise ValueError(
                "RQ2 HITL effort contains "
                "unknown route"
            )

    durations = [
        row.review_duration_ms
        for row in effort
    ]

    actions = Counter(
        row.action
        for row in effort
    )

    effort_summary = {
        "reviewed_screens":
            len(effort),

        "action_counts":
            dict(
                sorted(
                    actions.items()
                )
            ),

        "total_duration_ms":
            sum(durations),

        "mean_duration_ms":
            (
                mean(durations)
                if durations
                else None
            ),

        "median_duration_ms":
            (
                median(durations)
                if durations
                else None
            ),

        "retained_claims":
            sum(
                row.retained_claims
                for row in effort
            ),

        "changed_claims":
            sum(
                row.changed_claims
                for row in effort
            ),

        "removed_claims":
            sum(
                row.removed_claims
                for row in effort
            ),

        "human_added_claims":
            sum(
                row.human_added_claims
                for row in effort
            ),
    }

    return {
        "schema_version":
            "1.0.0",

        "evaluation_type":
            "rq2_semantic_quality_aggregate",

        "created_at":
            utc_now_iso(),

        "reference_id":
            reference.reference_id,

        "reference_canonical_sha256":
            semantic_reference_hash(
                reference
            ),

        "screens":
            len(
                reference.screens
            ),

        "stages":
            stage_metrics,

        "hitl_effort":
            effort_summary,
    }


def _condition_key(
    condition: str,
    graph_enabled: bool,
) -> str:
    return (
        f"{condition}_graph_"
        f"{'on' if graph_enabled else 'off'}"
    )


def _aggregate_rq3_rows(
    rows: list[
        dict[str, Any]
    ],
) -> dict[str, Any]:
    tp = sum(
        row["tp"]
        for row in rows
    )

    fp = sum(
        row["fp"]
        for row in rows
    )

    fn = sum(
        row["fn"]
        for row in rows
    )

    output_claims = sum(
        row["output_claims"]
        for row in rows
    )

    grounded_claims = sum(
        row["grounded_claims"]
        for row in rows
    )

    behavior_correct = sum(
        int(
            row[
                "expected_behavior_correct"
            ]
        )
        for row in rows
    )

    safety_rows = [
        row
        for row in rows
        if (
            row[
                "safety_compliant"
            ]
            is not None
        )
    ]

    safety_correct = sum(
        int(
            row[
                "safety_compliant"
            ]
        )
        for row in safety_rows
    )

    latencies = [
        row[
            "end_to_end_latency_ms"
        ]
        for row in rows
    ]

    writer_count = sum(
        int(
            row[
                "writer_invoked"
            ]
        )
        for row in rows
    )

    prohibited_violations = sum(
        len(
            row[
                "prohibited_claim_indices_present"
            ]
        )
        for row in rows
    )

    return {
        "responses":
            len(rows),

        "expected_behavior_accuracy":
            _safe_rate(
                behavior_correct,
                len(rows),
            ),

        "claims":
            _claim_metrics(
                tp,
                fp,
                fn,
            ),

        "grounding_rate":
            _safe_rate(
                grounded_claims,
                output_claims,
            ),

        "supported_claim_rate":
            _safe_rate(
                grounded_claims,
                output_claims,
            ),

        "unsupported_claim_rate":
            _safe_rate(
                output_claims
                - grounded_claims,
                output_claims,
            ),

        "output_atomic_claims":
            output_claims,

        "grounded_output_atomic_claims":
            grounded_claims,

        "prohibited_claim_violations":
            prohibited_violations,

        "safety_compliance_rate":
            _safe_rate(
                safety_correct,
                len(safety_rows),
            ),

        "mean_latency_ms":
            (
                mean(latencies)
                if latencies
                else None
            ),

        "median_latency_ms":
            (
                median(latencies)
                if latencies
                else None
            ),

        "p50_latency_ms":
            _percentile_linear(
                latencies,
                0.50,
            ),

        "p95_latency_ms":
            _percentile_linear(
                latencies,
                0.95,
            ),

        "writer_invocation_rate":
            _safe_rate(
                writer_count,
                len(rows),
            ),
    }


def aggregate_rq3(
    bank: RQ3QueryBank,
    packet: dict[str, Any],
    mapping: dict[str, Any],
    scoring: RQ3ScoringSet,
) -> dict[str, Any]:
    bank_hash = query_bank_hash(
        bank
    )

    if (
        packet.get(
            "query_bank_sha256"
        )
        != bank_hash
    ):
        raise ValueError(
            "blind packet query bank hash mismatch"
        )

    if (
        mapping.get(
            "query_bank_sha256"
        )
        != bank_hash
    ):
        raise ValueError(
            "private mapping query bank hash mismatch"
        )

    packet_rows = {
        row["blind_id"]:
            row
        for row
        in packet[
            "responses"
        ]
    }

    mapping_rows = {
        row["blind_id"]:
            row
        for row
        in mapping[
            "entries"
        ]
    }

    scoring_rows = {
        row.blind_id:
            row
        for row
        in scoring.records
    }

    if not (
        set(packet_rows)
        == set(mapping_rows)
        == set(scoring_rows)
    ):
        raise ValueError(
            "RQ3 packet/mapping/scoring "
            "blind_id sets differ"
        )

    query_by_id = {
        query.query_id:
            query
        for query
        in bank.queries
    }

    evaluated = []

    for blind_id in sorted(
        packet_rows
    ):
        packet_row = packet_rows[
            blind_id
        ]

        map_row = mapping_rows[
            blind_id
        ]

        score = scoring_rows[
            blind_id
        ]

        query_id = packet_row[
            "query_id"
        ]

        if (
            map_row["query_id"]
            != query_id
        ):
            raise ValueError(
                "blind mapping query mismatch"
            )

        query = query_by_id[
            query_id
        ]

        reference_claim_ids = {
            claim.claim_id
            for claim
            in query.required_claims
        }

        matched_reference_ids = {
            match.reference_claim_id
            for match
            in score.matches
        }

        if not (
            matched_reference_ids
            <= reference_claim_ids
        ):
            raise ValueError(
                "RQ3 score contains unknown "
                "reference claim id"
            )

        matched_output_ids = {
            match.output_claim_id
            for match
            in score.matches
        }

        for index in (
            score.prohibited_claim_indices_present
        ):
            if (
                index
                >= len(
                    query.prohibited_claims
                )
            ):
                raise ValueError(
                    "RQ3 prohibited claim index "
                    "out of range"
                )

        if (
            query.stratum
            == "mutative_safety"
        ):
            if (
                score.safety_compliant
                is None
            ):
                raise ValueError(
                    "mutative_safety requires "
                    "safety_compliant score"
                )

        elif (
            score.safety_compliant
            is not None
        ):
            raise ValueError(
                "safety_compliant is only "
                "used for mutative_safety"
            )

        evaluated.append(
            {
                "blind_id":
                    blind_id,

                "query_id":
                    query_id,

                "stratum":
                    query.stratum,

                "condition":
                    map_row[
                        "condition"
                    ],

                "graph_enabled":
                    map_row[
                        "graph_enabled"
                    ],

                "variant":
                    _condition_key(
                        map_row[
                            "condition"
                        ],
                        map_row[
                            "graph_enabled"
                        ],
                    ),

                "tp":
                    len(
                        score.matches
                    ),

                "fp":
                    (
                        len(
                            score.output_claim_ids
                        )
                        - len(
                            matched_output_ids
                        )
                    ),

                "fn":
                    (
                        len(
                            reference_claim_ids
                        )
                        - len(
                            matched_reference_ids
                        )
                    ),

                "output_claims":
                    len(
                        score.output_claim_ids
                    ),

                "grounded_claims":
                    len(
                        score.grounded_output_claim_ids
                    ),

                "expected_behavior_correct":
                    score.expected_behavior_correct,

                "prohibited_claim_indices_present":
                    score.prohibited_claim_indices_present,

                "safety_compliant":
                    score.safety_compliant,

                "end_to_end_latency_ms":
                    float(
                        map_row[
                            "end_to_end_latency_ms"
                        ]
                    ),

                "writer_invoked":
                    bool(
                        map_row[
                            "writer_invoked"
                        ]
                    ),
            }
        )

    grouped = defaultdict(
        list
    )

    for row in evaluated:
        grouped[
            row["variant"]
        ].append(
            row
        )

    expected_variants = {
        "A_graph_on",
        "B_graph_on",
        "C_graph_on",
        "C_graph_off",
    }

    if set(grouped) != expected_variants:
        raise ValueError(
            "RQ3 condition matrix is incomplete"
        )

    conditions = {}

    for variant in sorted(
        grouped
    ):
        rows = grouped[
            variant
        ]

        per_stratum = {}

        stratum_groups = defaultdict(
            list
        )

        for row in rows:
            stratum_groups[
                row["stratum"]
            ].append(
                row
            )

        for stratum, values in sorted(
            stratum_groups.items()
        ):
            per_stratum[
                stratum
            ] = (
                _aggregate_rq3_rows(
                    values
                )
            )

        conditions[
            variant
        ] = {
            "overall":
                _aggregate_rq3_rows(
                    rows
                ),

            "by_stratum":
                per_stratum,
        }

    return {
        "schema_version":
            "1.0.0",

        "evaluation_type":
            "rq3_condition_aggregate",

        "created_at":
            utc_now_iso(),

        "query_bank_id":
            bank.bank_id,

        "query_bank_sha256":
            bank_hash,

        "responses":
            len(evaluated),

        "conditions":
            conditions,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Mechanically aggregate human "
            "RQ2/RQ3 scoring decisions."
        )
    )

    subparsers = (
        parser.add_subparsers(
            dest="command",
            required=True,
        )
    )

    rq2 = subparsers.add_parser(
        "aggregate-rq2"
    )

    rq2.add_argument(
        "--reference",
        required=True,
    )

    rq2.add_argument(
        "--scores",
        required=True,
    )

    rq2.add_argument(
        "--output",
        required=True,
    )

    rq3 = subparsers.add_parser(
        "aggregate-rq3"
    )

    rq3.add_argument(
        "--bank",
        required=True,
    )

    rq3.add_argument(
        "--packet",
        required=True,
    )

    rq3.add_argument(
        "--mapping",
        required=True,
    )

    rq3.add_argument(
        "--scores",
        required=True,
    )

    rq3.add_argument(
        "--output",
        required=True,
    )

    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    if (
        args.command
        == "aggregate-rq2"
    ):
        reference = (
            load_semantic_reference(
                args.reference
            )
        )

        scoring = (
            RQ2ScoringSet.model_validate(
                _load_json(
                    args.scores
                )
            )
        )

        result = aggregate_rq2(
            reference,
            scoring,
        )

    else:
        bank = load_query_bank(
            args.bank
        )

        packet = _load_json(
            args.packet
        )

        mapping = _load_json(
            args.mapping
        )

        scoring = (
            RQ3ScoringSet.model_validate(
                _load_json(
                    args.scores
                )
            )
        )

        result = aggregate_rq3(
            bank,
            packet,
            mapping,
            scoring,
        )

    output = write_json_atomic(
        args.output,
        result,
    )

    print(
        output
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
