from dataclasses import dataclass, field


@dataclass
class ScreenSummary:
    route: str
    url: str | None = None
    title: str | None = None
    buttons: list[str] = field(default_factory=list)
    inputs: list[dict] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)