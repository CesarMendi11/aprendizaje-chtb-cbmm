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

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question no puede estar vacía")
        return value.strip()


class ChatSource(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str
    route: str
    source_type: Literal["screen"] = Field(default="screen", alias="sourceType")


class ChatResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    answer: str
    conversation_id: str | None = Field(alias="conversationId")
    suggestions: list[str]
    status: Literal["answered", "not_found", "error"]
    sources: list[ChatSource]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["erp-assistant-api"] = "erp-assistant-api"
    knowledge_loaded: bool
    screens_count: int
