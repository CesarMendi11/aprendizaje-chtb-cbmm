from dataclasses import dataclass, field


@dataclass
class CrawlResult:
    source_module: str
    visited_count: int
    routes: list[str] = field(default_factory=list)