from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from erp_assistant.structural.canonical.ids import (
    normalize_route,
    normalize_text,
)
from scripts.experiments.common import (
    sha256_file,
)
from scripts.experiments.evaluate_structural import (
    load_reference,
)


class SemanticReferenceClaim(BaseModel):
    model_config = ConfigDict(
        extra="forbid"
    )

    claim_id: str = Field(
        min_length=1,
        max_length=100,
    )

    statement: str = Field(
        min_length=1,
        max_length=1000,
    )

    acceptable_equivalents: list[str] = Field(
        default_factory=list,
        max_length=20,
    )

    @field_validator(
        "claim_id",
        "statement",
    )
    @classmethod
    def strip_required_text(
        cls,
        value: str,
    ) -> str:
        cleaned = " ".join(
            value.split()
        )

        if not cleaned:
            raise ValueError(
                "value must not be blank"
            )

        return cleaned

    @field_validator(
        "acceptable_equivalents"
    )
    @classmethod
    def validate_equivalents(
        cls,
        values: list[str],
    ) -> list[str]:
        cleaned = [
            " ".join(
                value.split()
            )
            for value in values
        ]

        if any(
            not value
            for value in cleaned
        ):
            raise ValueError(
                "acceptable equivalents "
                "must not be blank"
            )

        normalized = [
            normalize_text(
                value
            )
            for value in cleaned
        ]

        if (
            len(set(normalized))
            != len(normalized)
        ):
            raise ValueError(
                "acceptable equivalents "
                "must be unique"
            )

        return cleaned


class SemanticScreenReference(BaseModel):
    model_config = ConfigDict(
        extra="forbid"
    )

    route: str = Field(
        min_length=1,
        max_length=500,
    )

    title: str | None = Field(
        default=None,
        max_length=500,
    )

    title_status: Literal[
        "observed",
        "absent",
    ] = "observed"

    module_path: str | None = Field(
        default=None,
        max_length=1000,
    )

    expected_purpose: str = Field(
        min_length=1,
        max_length=2000,
    )

    purpose_equivalents: list[str] = Field(
        default_factory=list,
        max_length=20,
    )

    expected_capabilities: list[
        SemanticReferenceClaim
    ] = Field(
        default_factory=list,
        max_length=50,
    )

    expert_notes: str | None = Field(
        default=None,
        max_length=3000,
    )

    @field_validator(
        "route"
    )
    @classmethod
    def normalize_screen_route(
        cls,
        value: str,
    ) -> str:
        cleaned = value.strip()

        if not cleaned:
            raise ValueError(
                "route must not be blank"
            )

        normalized = normalize_route(
            cleaned
        )

        if not normalized.startswith(
            "/"
        ):
            raise ValueError(
                "route must be an ERP path"
            )

        return normalized

    @field_validator(
        "expected_purpose",
    )
    @classmethod
    def strip_required_text(
        cls,
        value: str,
    ) -> str:
        cleaned = " ".join(
            value.split()
        )

        if not cleaned:
            raise ValueError(
                "value must not be blank"
            )

        return cleaned

    @field_validator(
        "title",
    )
    @classmethod
    def strip_optional_title(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        cleaned = " ".join(
            value.split()
        )

        return cleaned or None

    @field_validator(
        "module_path",
        "expert_notes",
    )
    @classmethod
    def strip_optional_text(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        cleaned = " ".join(
            value.split()
        )

        return cleaned or None

    @field_validator(
        "purpose_equivalents"
    )
    @classmethod
    def validate_purpose_equivalents(
        cls,
        values: list[str],
    ) -> list[str]:
        cleaned = [
            " ".join(
                value.split()
            )
            for value in values
        ]

        if any(
            not value
            for value in cleaned
        ):
            raise ValueError(
                "purpose equivalents "
                "must not be blank"
            )

        normalized = [
            normalize_text(
                value
            )
            for value in cleaned
        ]

        if (
            len(set(normalized))
            != len(normalized)
        ):
            raise ValueError(
                "purpose equivalents "
                "must be unique"
            )

        return cleaned

    @model_validator(
        mode="after"
    )
    def validate_title_and_claim_ids(
        self,
    ) -> SemanticScreenReference:
        if (
            self.title_status == "observed"
            and self.title is None
        ):
            raise ValueError(
                "observed title_status requires "
                "a non-blank title"
            )

        if (
            self.title_status == "absent"
            and self.title is not None
        ):
            raise ValueError(
                "absent title_status requires "
                "title to be null"
            )

        claim_ids = [
            claim.claim_id
            for claim
            in self.expected_capabilities
        ]

        if (
            len(set(claim_ids))
            != len(claim_ids)
        ):
            raise ValueError(
                "claim_id values must be "
                "unique within a screen"
            )

        return self


class SemanticReference(BaseModel):
    model_config = ConfigDict(
        extra="forbid"
    )

    schema_version: Literal[
        "1.0.0",
        "1.1.0",
    ] = "1.1.0"

    status: Literal[
        "template",
        "draft",
        "frozen",
    ] = "draft"

    reference_id: str = Field(
        min_length=1,
        max_length=200,
    )

    author_role: Literal[
        "expert_A"
    ] = "expert_A"

    source_basis: Literal[
        "direct_erp_inspection_and_expert_knowledge"
    ] = (
        "direct_erp_inspection_and_expert_knowledge"
    )

    formal_semantic_proposals_seen_before_freeze: bool = False

    screens: list[
        SemanticScreenReference
    ] = Field(
        default_factory=list
    )

    @field_validator(
        "reference_id"
    )
    @classmethod
    def strip_reference_id(
        cls,
        value: str,
    ) -> str:
        cleaned = value.strip()

        if not cleaned:
            raise ValueError(
                "reference_id must not be blank"
            )

        return cleaned

    @model_validator(
        mode="after"
    )
    def validate_reference(
        self,
    ) -> SemanticReference:
        routes = [
            screen.route
            for screen
            in self.screens
        ]

        if (
            len(set(routes))
            != len(routes)
        ):
            raise ValueError(
                "semantic screen routes "
                "must be unique"
            )

        routes = [
            screen.route
            for screen
            in self.screens
        ]

        if (
            len(set(routes))
            != len(routes)
        ):
            raise ValueError(
                "semantic screen routes "
                "must be unique"
            )

        if self.status == "frozen":
            if not self.screens:
                raise ValueError(
                    "a frozen semantic reference "
                    "must not be empty"
                )

            if (
                self.formal_semantic_proposals_seen_before_freeze
            ):
                raise ValueError(
                    "the formal semantic reference "
                    "must be frozen before formal "
                    "SemanticProposal output is seen"
                )

        return self


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


def load_semantic_reference(
    path: str | Path,
) -> SemanticReference:
    payload = json.loads(
        Path(path).read_text(
            encoding="utf-8"
        )
    )

    return (
        SemanticReference.model_validate(
            payload
        )
    )


def semantic_reference_hash(
    reference: SemanticReference,
) -> str:
    return canonical_sha256(
        reference.model_dump(
            mode="json"
        )
    )


def structural_reference_summary(
    path: str | Path,
) -> dict[str, Any]:
    reference_path = Path(
        path
    )

    items = load_reference(
        reference_path
    )

    modules = [
        item
        for item in items
        if item.entity_type == "module"
    ]

    screens = [
        item
        for item in items
        if item.entity_type == "screen"
    ]

    if not modules:
        raise ValueError(
            "the frozen structural reference "
            "must contain at least one module"
        )

    if not screens:
        raise ValueError(
            "the frozen structural reference "
            "must contain at least one screen"
        )

    return {
        "schema_version":
            "1.0.0",

        "reference_type":
            "rq1_structural_reference",

        "file_sha256":
            sha256_file(
                reference_path
            ),

        "items":
            len(items),

        "modules":
            len(modules),

        "screens":
            len(screens),
    }


def semantic_reference_summary(
    path: str | Path,
) -> dict[str, Any]:
    reference_path = Path(
        path
    )

    reference = (
        load_semantic_reference(
            reference_path
        )
    )

    claim_count = sum(
        len(
            screen.expected_capabilities
        )
        for screen
        in reference.screens
    )

    return {
        "schema_version":
            reference.schema_version,

        "reference_type":
            "rq2_semantic_reference",

        "reference_id":
            reference.reference_id,

        "status":
            reference.status,

        "author_role":
            reference.author_role,

        "screens":
            len(
                reference.screens
            ),

        "expected_capability_claims":
            claim_count,

        "file_sha256":
            sha256_file(
                reference_path
            ),

        "canonical_sha256":
            semantic_reference_hash(
                reference
            ),
    }



def load_semantic_exclusions(
    path: str | Path,
) -> list[dict[str, str]]:
    import csv

    rows: list[
        dict[str, str]
    ] = []

    with Path(path).open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        reader = csv.DictReader(
            handle
        )

        expected = [
            "route",
            "title",
            "reason",
            "notes",
        ]

        if reader.fieldnames != expected:
            raise ValueError(
                "RQ2 exclusions must use header: "
                + ",".join(expected)
            )

        for line_number, raw in enumerate(
            reader,
            start=2,
        ):
            if not any(
                str(value or "").strip()
                for value in raw.values()
            ):
                continue

            route_raw = str(
                raw.get(
                    "route"
                )
                or ""
            ).strip()

            if not route_raw:
                raise ValueError(
                    f"RQ2 exclusion line {line_number}: "
                    "route is required"
                )

            route = normalize_route(
                route_raw
            )

            if not route.startswith(
                "/"
            ):
                raise ValueError(
                    f"RQ2 exclusion line {line_number}: "
                    "route must be an ERP path"
                )

            reason = " ".join(
                str(
                    raw.get(
                        "reason"
                    )
                    or ""
                ).split()
            )

            if not reason:
                raise ValueError(
                    f"RQ2 exclusion line {line_number}: "
                    "reason is required"
                )

            rows.append(
                {
                    "route":
                        route,

                    "title":
                        " ".join(
                            str(
                                raw.get(
                                    "title"
                                )
                                or ""
                            ).split()
                        ),

                    "reason":
                        reason,

                    "notes":
                        " ".join(
                            str(
                                raw.get(
                                    "notes"
                                )
                                or ""
                            ).split()
                        ),
                }
            )

    routes = [
        row["route"]
        for row in rows
    ]

    if (
        len(set(routes))
        != len(routes)
    ):
        raise ValueError(
            "RQ2 exclusion routes "
            "must be unique"
        )

    return rows


def semantic_bundle_summary(
    reference_path: str | Path,
    exclusions_path: str | Path,
    structural_reference_path: str | Path,
) -> dict[str, Any]:
    reference_path = Path(
        reference_path
    )

    exclusions_path = Path(
        exclusions_path
    )

    structural_reference_path = Path(
        structural_reference_path
    )

    reference = (
        load_semantic_reference(
            reference_path
        )
    )

    if (
        reference.status
        != "frozen"
    ):
        raise ValueError(
            "RQ2 bundle validation "
            "requires status=frozen"
        )

    structural_items = load_reference(
        structural_reference_path
    )

    universe = {
        item.route
        for item in structural_items
        if item.entity_type == "screen"
    }

    included = {
        screen.route
        for screen
        in reference.screens
    }

    exclusions = (
        load_semantic_exclusions(
            exclusions_path
        )
    )

    excluded = {
        row["route"]
        for row in exclusions
    }

    overlap = (
        included
        & excluded
    )

    if overlap:
        raise ValueError(
            "RQ2 routes cannot be both "
            "included and excluded: "
            + ", ".join(
                sorted(
                    overlap
                )
            )
        )

    extras = (
        included
        | excluded
    ) - universe

    if extras:
        raise ValueError(
            "RQ2 bundle contains routes "
            "outside frozen RQ1: "
            + ", ".join(
                sorted(
                    extras
                )
            )
        )

    missing = universe - (
        included
        | excluded
    )

    if missing:
        raise ValueError(
            "RQ2 bundle must account for "
            "every frozen RQ1 functional "
            "screen route; missing: "
            + ", ".join(
                sorted(
                    missing
                )
            )
        )

    absent_title_included = sum(
        (
            screen.title_status
            == "absent"
        )
        for screen
        in reference.screens
    )

    return {
        "schema_version":
            "1.0.0",

        "reference_type":
            "rq2_semantic_reference_bundle",

        "reference_id":
            reference.reference_id,

        "reference_status":
            reference.status,

        "rq1_functional_screen_universe":
            len(
                universe
            ),

        "included_semantic_screens":
            len(
                included
            ),

        "excluded_semantic_screens":
            len(
                excluded
            ),

        "accounted_routes":
            len(
                included
                | excluded
            ),

        "included_without_visible_title":
            absent_title_included,

        "expected_capability_claims":
            sum(
                len(
                    screen.expected_capabilities
                )
                for screen
                in reference.screens
            ),

        "reference_file_sha256":
            sha256_file(
                reference_path
            ),

        "reference_canonical_sha256":
            semantic_reference_hash(
                reference
            ),

        "exclusions_file_sha256":
            sha256_file(
                exclusions_path
            ),

        "rq1_structural_reference_sha256":
            sha256_file(
                structural_reference_path
            ),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and fingerprint "
            "RQ1/RQ2 human reference artifacts."
        )
    )

    subparsers = (
        parser.add_subparsers(
            dest="command",
            required=True,
        )
    )

    structural = (
        subparsers.add_parser(
            "validate-structural"
        )
    )

    structural.add_argument(
        "reference"
    )

    semantic = (
        subparsers.add_parser(
            "validate-semantic"
        )
    )

    semantic.add_argument(
        "reference"
    )

    bundle = (
        subparsers.add_parser(
            "validate-semantic-bundle"
        )
    )

    bundle.add_argument(
        "reference"
    )

    bundle.add_argument(
        "exclusions"
    )

    bundle.add_argument(
        "structural_reference"
    )

    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    if (
        args.command
        == "validate-structural"
    ):
        summary = (
            structural_reference_summary(
                args.reference
            )
        )

    elif (
        args.command
        == "validate-semantic"
    ):
        summary = (
            semantic_reference_summary(
                args.reference
            )
        )

    else:
        summary = (
            semantic_bundle_summary(
                args.reference,
                args.exclusions,
                args.structural_reference,
            )
        )

    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
