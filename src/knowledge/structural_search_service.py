from __future__ import annotations

from dataclasses import dataclass

from src.knowledge.models import StructuralScreen
from src.knowledge.structural_knowledge_repository import StructuralKnowledgeRepository
from src.knowledge.text_normalizer import normalize_text, tokens

CONTEXTUAL_PHRASES = ("esta pantalla", "este modulo", "aqui", "en esta", "en este")


@dataclass(frozen=True)
class SearchResult:
    screen: StructuralScreen
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class SearchResponse:
    best: SearchResult | None
    alternatives: tuple[SearchResult, ...] = ()


class StructuralSearchService:
    def __init__(
        self,
        repository: StructuralKnowledgeRepository,
        max_results: int = 3,
        minimum_score: float = 2.5,
    ):
        self.repository = repository
        self.max_results = max(1, max_results)
        self.minimum_score = minimum_score

    def search(self, question: str, current_route: str | None = None) -> SearchResponse:
        query = normalize_text(question)
        query_tokens = tokens(question)
        contextual = any(phrase in query for phrase in CONTEXTUAL_PHRASES)
        results = [
            self._score(screen, query, query_tokens, current_route, contextual)
            for screen in self.repository.screens
        ]
        matches = sorted(
            (result for result in results if result.score >= self.minimum_score),
            key=lambda item: (-item.score, item.screen.display_title.casefold()),
        )[: self.max_results]
        return SearchResponse(matches[0] if matches else None, tuple(matches[1:]))

    def _score(
        self,
        screen: StructuralScreen,
        query: str,
        query_tokens: set[str],
        current_route: str | None,
        contextual: bool,
    ) -> SearchResult:
        score = 0.0
        reasons: list[str] = []
        title = normalize_text(screen.display_title)
        title_tokens = tokens(screen.display_title)
        if title and title in query:
            score += 12.0
            reasons.append("title_exact")
        elif overlap := query_tokens & title_tokens:
            score += 5.0 * len(overlap) / max(1, len(title_tokens))
            reasons.append("title_partial")
        route_tokens = tokens(screen.route)
        if overlap := query_tokens & route_tokens:
            score += 2.5 * len(overlap)
            reasons.append("route")
        score += self._collection_score(
            query_tokens, tokens(screen.main_visible_text), 0.35, "content", reasons
        )
        score += self._collection_score(
            query_tokens, tokens(" ".join(screen.field_names)), 1.2, "fields", reasons
        )
        score += self._collection_score(
            query_tokens, tokens(" ".join(screen.button_names)), 1.1, "buttons", reasons
        )
        score += self._collection_score(
            query_tokens, tokens(" ".join(screen.table_headers)), 0.8, "tables", reasons
        )
        score += self._collection_score(
            query_tokens, tokens(" ".join(screen.link_names)), 0.6, "links", reasons
        )
        if contextual and self.repository.get_by_route(current_route) is screen:
            score += 15.0
            reasons.append("current_route_context")
        return SearchResult(screen, round(score, 3), tuple(reasons))

    @staticmethod
    def _collection_score(
        query_tokens: set[str],
        candidate_tokens: set[str],
        weight: float,
        reason: str,
        reasons: list[str],
    ) -> float:
        overlap = query_tokens & candidate_tokens
        if overlap:
            reasons.append(reason)
        return weight * len(overlap)
