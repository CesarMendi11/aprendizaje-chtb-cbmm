from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatContext(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda value: "".join(
            [value.split("_")[0], *[part.title() for part in value.split("_")[1:]]]
        ),
        populate_by_name=True,
        extra="ignore",
    )

    current_route: str | None = Field(default=None, max_length=500)
    user_id: int | None = None
    username: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, max_length=100)


class ChatRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    question: str = Field(max_length=2000)
    conversation_id: str | None = Field(default=None, alias="conversationId", max_length=200)
    context: ChatContext | None = None
    experiment_condition: Literal["A", "B", "C"] = Field(
        default="C", alias="experimentCondition"
    )
    graph_enabled: bool = Field(default=True, alias="graphEnabled")

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question no puede estar vacía")
        return value.strip()

    @field_validator("conversation_id")
    @classmethod
    def normalize_conversation_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ChatSource(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str
    route: str
    source_type: str = Field(default="screen", alias="sourceType", min_length=1, max_length=50)


class ChatResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    answer: str
    conversation_id: str | None = Field(alias="conversationId")
    suggestions: list[str]
    status: Literal["answered", "not_found", "error"]
    sources: list[ChatSource]
    answer_mode: str | None = None
    answer_decision: dict[str, object] | None = Field(default=None, alias="answerDecision")
    intent: str | None = None
    confidence: str | None = None
    evidence_ids: list[str] = []
    retrieval: dict[str, int] | None = None
    experiment_condition: Literal["A", "B", "C"] = Field(alias="experimentCondition")
    graph_enabled: bool = Field(alias="graphEnabled")
    graph_expansion: dict[str, object] | None = Field(default=None, alias="graphExpansion")


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["erp-assistant-api"] = "erp-assistant-api"
