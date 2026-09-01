from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

DEFAULT_RRF_K = 60
DEFAULT_CHANNEL_PRIORITY = (
    "canonical",
    "lexical",
    "trigram",
    "structural_dense",
    "semantic_dense",
)


@dataclass(frozen=True)
class RankedItem:
    canonical_id: str
    raw_score: float | None = None


@dataclass(frozen=True)
class RankContribution:
    channel: str
    rank: int
    raw_score: float | None

    def as_dict(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "rank": self.rank,
            "raw_score": self.raw_score,
        }


@dataclass(frozen=True)
class FusedCandidate:
    canonical_id: str
    rrf_score: float
    contributions: tuple[RankContribution, ...]

    @property
    def best_rank(self) -> int:
        return min(contribution.rank for contribution in self.contributions)

    @property
    def channels(self) -> tuple[str, ...]:
        return tuple(contribution.channel for contribution in self.contributions)

    def as_dict(self) -> dict[str, object]:
        return {
            "canonical_id": self.canonical_id,
            "rrf_score": round(float(self.rrf_score), 9),
            "best_rank": self.best_rank,
            "channels": list(self.channels),
            "contributions": [contribution.as_dict() for contribution in self.contributions],
        }


class ReciprocalRankFusion:
    """Fuse independent retrieval rankings without comparing raw score scales.

    RRF uses only each candidate's ordinal position inside a channel. Raw scores
    remain attached strictly for diagnostics, because PostgreSQL full-text,
    pg_trgm and cosine similarity scores are not directly comparable.
    """

    def __init__(
        self,
        *,
        k: int = DEFAULT_RRF_K,
        channel_priority: Iterable[str] = DEFAULT_CHANNEL_PRIORITY,
    ):
        if int(k) <= 0:
            raise ValueError("rrf_k_must_be_positive")
        self.k = int(k)
        self.channel_priority = tuple(channel_priority)
        self._priority = {channel: index for index, channel in enumerate(self.channel_priority)}

    def fuse(
        self,
        rankings: Mapping[str, Iterable[RankedItem]],
        *,
        limit: int | None = None,
    ) -> tuple[FusedCandidate, ...]:
        scores: dict[str, float] = {}
        contributions: dict[str, list[RankContribution]] = {}

        for channel, items in rankings.items():
            seen = set()
            rank = 0
            for item in items:
                canonical_id = str(item.canonical_id or "").strip()
                if not canonical_id or canonical_id in seen:
                    continue
                seen.add(canonical_id)
                rank += 1
                scores[canonical_id] = scores.get(canonical_id, 0.0) + (1.0 / (self.k + rank))
                contributions.setdefault(canonical_id, []).append(
                    RankContribution(
                        channel=channel,
                        rank=rank,
                        raw_score=(float(item.raw_score) if item.raw_score is not None else None),
                    )
                )

        fused = [
            FusedCandidate(
                canonical_id=canonical_id,
                rrf_score=score,
                contributions=tuple(
                    sorted(
                        contributions[canonical_id],
                        key=lambda contribution: (
                            self._priority.get(
                                contribution.channel,
                                len(self._priority),
                            ),
                            contribution.rank,
                            contribution.channel,
                        ),
                    )
                ),
            )
            for canonical_id, score in scores.items()
        ]

        def tie_priority(candidate: FusedCandidate) -> int:
            return min(
                (
                    self._priority.get(
                        contribution.channel,
                        len(self._priority),
                    )
                    for contribution in candidate.contributions
                ),
                default=len(self._priority),
            )

        fused.sort(
            key=lambda candidate: (
                -candidate.rrf_score,
                candidate.best_rank,
                tie_priority(candidate),
                -len(candidate.contributions),
                candidate.canonical_id,
            )
        )

        if limit is not None:
            return tuple(fused[: max(0, int(limit))])
        return tuple(fused)
