from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from .screen_purpose_inference import InferenceModel

RecognizedAction = Literal["search", "navigate", "view", "create", "edit", "delete", "process"]
SupportLevel = Literal["direct", "prudent_only"]
NarrativeRule = Literal["direct_allowed", "prudent_only"]


class ActionGroundingHint(InferenceModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: RecognizedAction
    support_level: SupportLevel
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    reference_types: tuple[str, ...] = Field(min_length=1)
    narrative_rule: NarrativeRule


class ScreenPurposeGroundingPlan(InferenceModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    supported_actions: tuple[ActionGroundingHint, ...]
    forbidden_actions: tuple[RecognizedAction, ...]
