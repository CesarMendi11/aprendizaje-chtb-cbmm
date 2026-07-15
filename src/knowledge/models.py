from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ScreenElement(BaseModel):
    model_config = ConfigDict(extra="allow")

    text: str | None = None
    label: str | None = None
    placeholder: str | None = None
    aria_label: str | None = None
    title: str | None = None
    href: str | None = None
    headers: list[str] = Field(default_factory=list)

    def display_names(self) -> list[str]:
        return [
            value.strip()
            for value in (self.label, self.text, self.aria_label, self.title, self.placeholder)
            if isinstance(value, str) and value.strip()
        ]


class StructuralScreen(BaseModel):
    model_config = ConfigDict(extra="allow")

    route: str
    title: str = ""
    functional_title: str = ""
    main_visible_text: str = ""
    visible_text: str = ""
    regions: dict[str, Any] = Field(default_factory=dict)
    inputs: list[ScreenElement] = Field(default_factory=list)
    buttons: list[ScreenElement] = Field(default_factory=list)
    tables: list[ScreenElement] = Field(default_factory=list)
    local_links: list[ScreenElement] = Field(default_factory=list)
    links: list[ScreenElement] = Field(default_factory=list)

    @property
    def display_title(self) -> str:
        return self.functional_title.strip() or self.title.strip() or self.route

    @property
    def field_names(self) -> list[str]:
        return _unique(
            name for item in self.inputs for name in [next(iter(item.display_names()), "")] if name
        )

    @property
    def button_names(self) -> list[str]:
        return _unique(name for item in self.buttons for name in item.display_names())

    @property
    def table_headers(self) -> list[str]:
        return _unique(
            header for table in self.tables for header in table.headers if header.strip()
        )

    @property
    def link_names(self) -> list[str]:
        return _unique(
            name for item in (*self.local_links, *self.links) for name in item.display_names()
        )


def _unique(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result
