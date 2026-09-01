from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import Float, cast, func, literal, literal_column, or_, select

from erp_assistant.persistence.postgres.models import KnowledgeItem
from erp_assistant.structural.canonical.enums import ReviewStatus
from erp_assistant.structural.canonical.privacy import sanitize_text
from erp_assistant.structural.services.effective_knowledge_service import EffectiveKnowledgeService

from .query_plan import QueryPlan

DEFAULT_RESOLVABLE_ENTITY_TYPES = (
    "screen",
    "module",
    "field",
    "control",
    "table",
    "table_column",
    "link",
)

# Only function words are removed from the PostgreSQL lexical query. Intent words
# such as "buscar", "nuevo" or "crear" are intentionally retained because they
# can also be canonical labels in an ERP.
LEXICAL_STOPWORDS = {
    "a",
    "al",
    "algo",
    "como",
    "con",
    "cual",
    "cuales",
    "de",
    "del",
    "donde",
    "el",
    "ella",
    "en",
    "esa",
    "ese",
    "eso",
    "esta",
    "este",
    "esto",
    "la",
    "las",
    "lo",
    "los",
    "me",
    "mi",
    "para",
    "por",
    "que",
    "se",
    "su",
    "un",
    "una",
    "unos",
    "unas",
    "y",
}

LABEL_KEYS = {
    "module": ("name", "title", "label"),
    "screen": ("title", "name", "label"),
    "field": ("label", "name", "title"),
    "control": ("label", "name", "title"),
    "table": ("name", "title", "label"),
    "table_column": ("name", "label", "title"),
    "link": ("label", "name", "title"),
    "event": ("label", "name", "title"),
    "ui_state": ("label", "name", "title"),
    "transition": ("label", "name", "title"),
}


@dataclass(frozen=True)
class EntityResolutionCandidate:
    canonical_id: str
    entity_type: str
    safe_label: str
    route: str | None
    score: float
    channels: tuple[str, ...]
    matched_terms: tuple[str, ...]
    channel_scores: tuple[tuple[str, float], ...] = ()

    def channel_score(self, channel: str) -> float | None:
        for current, score in self.channel_scores:
            if current == channel:
                return float(score)
        return None

    def as_dict(self) -> dict[str, object]:
        return {
            "canonical_id": self.canonical_id,
            "entity_type": self.entity_type,
            "safe_label": self.safe_label,
            "route": self.route,
            "score": round(float(self.score), 6),
            "channels": list(self.channels),
            "matched_terms": list(self.matched_terms),
            "channel_scores": {
                channel: round(float(score), 6) for channel, score in self.channel_scores
            },
        }


@dataclass(frozen=True)
class EntityResolution:
    query: str
    normalized_query: str
    candidates: tuple[EntityResolutionCandidate, ...]

    @property
    def ambiguous_labels(self) -> tuple[str, ...]:
        groups: dict[tuple[str, str], list[EntityResolutionCandidate]] = {}
        for candidate in self.candidates:
            if candidate.score < 0.97:
                continue
            label = normalize_entity_text(candidate.safe_label)
            groups.setdefault((candidate.entity_type, label), []).append(candidate)
        return tuple(
            sorted(
                f"{entity_type}:{label}"
                for (entity_type, label), rows in groups.items()
                if label and len(rows) > 1
            )
        )

    @property
    def seed_candidates(self) -> tuple[EntityResolutionCandidate, ...]:
        ambiguous = set(self.ambiguous_labels)
        return tuple(
            candidate
            for candidate in self.candidates
            if candidate.score >= 0.90
            and f"{candidate.entity_type}:{normalize_entity_text(candidate.safe_label)}"
            not in ambiguous
        )

    @property
    def status(self) -> str:
        if not self.candidates:
            return "not_found"
        if self.ambiguous_labels:
            return "ambiguous"
        return "resolved"

    @property
    def primary_canonical_id(self) -> str | None:
        seeds = self.seed_candidates
        return seeds[0].canonical_id if self.status == "resolved" and len(seeds) == 1 else None

    @property
    def ambiguous_candidate_ids(self) -> tuple[str, ...]:
        ambiguous = set(self.ambiguous_labels)
        return tuple(
            candidate.canonical_id
            for candidate in self.candidates
            if f"{candidate.entity_type}:{normalize_entity_text(candidate.safe_label)}" in ambiguous
        )

    def ranking(self, channel: str) -> tuple[tuple[str, float], ...]:
        if channel == "canonical":
            accepted = {"normalized_mention", "alias", "normalized_containment"}
            rows = []
            for candidate in self.candidates:
                scores = [
                    score for current, score in candidate.channel_scores if current in accepted
                ]
                if not scores and any(current in accepted for current in candidate.channels):
                    scores = [candidate.score]
                if scores:
                    rows.append((candidate.canonical_id, max(scores)))
        else:
            rows = []
            for candidate in self.candidates:
                scores = [
                    score for current, score in candidate.channel_scores if current == channel
                ]
                if not scores and channel in candidate.channels:
                    scores = [candidate.score]
                if scores:
                    rows.append((candidate.canonical_id, max(scores)))
        return tuple(sorted(rows, key=lambda row: (-row[1], row[0])))

    def as_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "normalized_query": self.normalized_query,
            "status": self.status,
            "primary_canonical_id": self.primary_canonical_id,
            "ambiguous_labels": list(self.ambiguous_labels),
            "seed_canonical_ids": [candidate.canonical_id for candidate in self.seed_candidates],
            "candidates": [candidate.as_dict() for candidate in self.candidates],
        }


class CanonicalEntityResolver:
    """Resolve natural-language mentions to reviewed canonical entities.

    PostgreSQL is the authority for lexical/trigram candidate generation. A
    portable scorer is retained for tests and non-PostgreSQL development, and
    corrected items are always re-scored from their effective payload so review
    corrections cannot be bypassed by stale raw labels.
    """

    def __init__(self, session, *, aliases=None):
        self.session = session
        self.aliases = aliases or {}
        self.effective = EffectiveKnowledgeService(session) if session is not None else None

    def resolve(
        self,
        query_plan: QueryPlan,
        *,
        version_id,
        limit: int = 12,
    ) -> EntityResolution:
        normalized_query = normalize_entity_text(
            query_plan.normalized_question or query_plan.question
        )
        entity_types = tuple(query_plan.target_entity_types) or DEFAULT_RESOLVABLE_ENTITY_TYPES
        query_forms = query_entity_forms(normalized_query)
        alias_targets = self._matched_alias_targets(query_forms)

        rows = self._candidate_rows(
            version_id=version_id,
            entity_types=entity_types,
            normalized_query=normalized_query,
            query_forms=query_forms,
            alias_targets=alias_targets,
            limit=max(limit * 4, 40),
        )

        # Corrections are few and must be searchable by their effective label,
        # even when the raw KnowledgeItem title predates the human correction.
        corrected = self._corrected_items(
            version_id=version_id,
            entity_types=entity_types,
        )
        seen_item_ids = {str(item.id) for item, _, _ in rows}
        rows.extend((item, 0.0, 0.0) for item in corrected if str(item.id) not in seen_item_ids)

        candidates: dict[str, EntityResolutionCandidate] = {}
        type_priority = {entity_type: index for index, entity_type in enumerate(entity_types)}

        for item, lexical_score, trigram_score in rows:
            payload = self._effective_payload(item)
            label = safe_entity_label(item.entity_type, payload, fallback=item.title)
            normalized_label = normalize_entity_text(label)
            if not normalized_label:
                continue

            if getattr(item, "current_review_status", None) == ReviewStatus.CORRECTED:
                # PostgreSQL metadata columns reflect the imported source payload.
                # Human corrections are authoritative, so stale raw-label scores
                # must never make a corrected item resolvable under its old name.
                lexical_score = portable_lexical_score(normalized_label, normalized_query)
                trigram_score = portable_trigram_score(normalized_label, query_forms)

            score, channels, matched_terms, channel_scores = self._score(
                normalized_label=normalized_label,
                normalized_query=normalized_query,
                query_forms=query_forms,
                alias_targets=alias_targets,
                lexical_score=lexical_score,
                trigram_score=trigram_score,
            )
            if score <= 0:
                continue

            candidate = EntityResolutionCandidate(
                canonical_id=item.canonical_id,
                entity_type=item.entity_type,
                safe_label=label,
                route=item.route,
                score=score,
                channels=channels,
                matched_terms=matched_terms,
                channel_scores=channel_scores,
            )
            previous = candidates.get(item.canonical_id)
            if previous is None or candidate.score > previous.score:
                candidates[item.canonical_id] = candidate

        ordered = sorted(
            candidates.values(),
            key=lambda candidate: (
                -candidate.score,
                type_priority.get(candidate.entity_type, len(type_priority)),
                normalize_entity_text(candidate.safe_label),
                candidate.canonical_id,
            ),
        )[: max(1, min(limit, 50))]

        return EntityResolution(
            query=query_plan.question,
            normalized_query=normalized_query,
            candidates=tuple(ordered),
        )

    def scope_to_screen(
        self,
        resolution: EntityResolution,
        *,
        version_id,
        screen_id: str,
        context_label: str | None = None,
    ) -> EntityResolution:
        """Narrow already-resolved candidates to one governed screen scope.

        The scope itself is not inferred here: callers may pass only a screen
        that already came from governed conversation state.  PostgreSQL remains
        the authority for membership.  Parent chains cover field/control/table
        -> screen and table_column -> table -> screen, while events and UI
        states are imported with the screen in their structural ancestry.

        ``context_label`` is the safe label that the conversation layer injected
        into the effective question.  UI states commonly inherit the screen
        title as their own label, so that synthetic phrase can make multiple
        states look like an explicit ambiguous user mention.  Those shadow
        candidates are not user-selected entities and are removed only when the
        caller supplies the governed context label.  Real child ambiguities such
        as two controls or two events with the same label remain untouched.
        """

        screen_id = str(screen_id or "").strip()
        if not screen_id or not resolution.candidates:
            return resolution

        candidate_ids = tuple(
            candidate.canonical_id for candidate in resolution.candidates if candidate.canonical_id
        )
        items = self._scope_items(
            candidate_ids,
            version_id=version_id,
        )
        scoped = tuple(
            candidate
            for candidate in resolution.candidates
            if self._belongs_to_screen(
                candidate.canonical_id,
                screen_id,
                items,
            )
        )

        normalized_context_label = normalize_entity_text(context_label or "")
        if normalized_context_label:
            scoped = tuple(
                candidate
                for candidate in scoped
                if not (
                    candidate.entity_type == "ui_state"
                    and candidate.canonical_id != screen_id
                    and normalize_entity_text(candidate.safe_label) == normalized_context_label
                )
            )

        return EntityResolution(
            query=resolution.query,
            normalized_query=resolution.normalized_query,
            candidates=scoped,
        )

    def _scope_items(self, candidate_ids, *, version_id):
        pending = {str(value) for value in candidate_ids if str(value).strip()}
        by_id = {}
        for _ in range(4):
            missing = sorted(pending - set(by_id))
            if not missing:
                break
            statement = select(KnowledgeItem).where(
                KnowledgeItem.knowledge_version_id == version_id,
                KnowledgeItem.current_review_status.in_(
                    [ReviewStatus.APPROVED, ReviewStatus.CORRECTED]
                ),
                KnowledgeItem.canonical_id.in_(missing),
            )
            rows = list(self.session.scalars(statement))
            if not rows:
                break
            for item in rows:
                by_id[item.canonical_id] = item
                parent_id = str(getattr(item, "parent_canonical_id", None) or "").strip()
                if parent_id:
                    pending.add(parent_id)
        return by_id

    @staticmethod
    def _belongs_to_screen(canonical_id, screen_id, items):
        current = str(canonical_id or "").strip()
        seen = set()
        for _ in range(5):
            if not current or current in seen:
                return False
            if current == screen_id:
                return True
            seen.add(current)
            item = items.get(current)
            if item is None:
                return False
            current = str(getattr(item, "parent_canonical_id", None) or "").strip()
        return False

    def _candidate_rows(
        self,
        *,
        version_id,
        entity_types,
        normalized_query,
        query_forms,
        alias_targets,
        limit,
    ):
        dialect = self._dialect_name()
        if dialect == "postgresql":
            return self._postgres_candidate_rows(
                version_id=version_id,
                entity_types=entity_types,
                normalized_query=normalized_query,
                query_forms=query_forms,
                alias_targets=alias_targets,
                limit=limit,
            )
        return self._portable_candidate_rows(
            version_id=version_id,
            entity_types=entity_types,
            normalized_query=normalized_query,
            query_forms=query_forms,
            alias_targets=alias_targets,
            limit=limit,
        )

    def _postgres_candidate_rows(
        self,
        *,
        version_id,
        entity_types,
        normalized_query,
        query_forms,
        alias_targets,
        limit,
    ):
        statement = self._postgres_statement(
            version_id=version_id,
            entity_types=entity_types,
            normalized_query=normalized_query,
            query_forms=query_forms,
            alias_targets=alias_targets,
            limit=limit,
        )
        return [
            (row[0], float(row[1] or 0.0), float(row[2] or 0.0))
            for row in self.session.execute(statement)
        ]

    @staticmethod
    def _postgres_statement(
        *,
        version_id,
        entity_types,
        normalized_query,
        query_forms,
        alias_targets,
        limit,
    ):
        empty = literal_column("''")
        space = literal_column("' '")
        label_expr = func.coalesce(
            KnowledgeItem.normalized_title,
            func.lower(KnowledgeItem.title),
            empty,
        )
        search_text = (
            func.coalesce(KnowledgeItem.title, empty)
            + space
            + func.coalesce(KnowledgeItem.normalized_title, empty)
            + space
            + func.coalesce(KnowledgeItem.route, empty)
        )
        lexical_terms = lexical_query_terms(normalized_query)
        tsquery_text = " | ".join(f"{term}:*" for term in lexical_terms)
        if tsquery_text:
            regconfig = literal_column("'simple'::regconfig")
            tsquery = func.to_tsquery(regconfig, tsquery_text)
            lexical_vector = func.to_tsvector(regconfig, search_text)
            lexical_score = func.ts_rank_cd(lexical_vector, tsquery)
            lexical_match = lexical_vector.op("@@")(tsquery)
        else:
            lexical_score = cast(literal(0.0), Float)
            lexical_match = literal(False)

        trigram_score = func.word_similarity(label_expr, normalized_query)
        exact_values = sorted(set(query_forms) | set(alias_targets))
        exact_match = label_expr.in_(exact_values) if exact_values else literal(False)

        return (
            select(
                KnowledgeItem,
                lexical_score.label("lexical_score"),
                trigram_score.label("trigram_score"),
            )
            .where(
                KnowledgeItem.knowledge_version_id == version_id,
                KnowledgeItem.current_review_status.in_(
                    [ReviewStatus.APPROVED, ReviewStatus.CORRECTED]
                ),
                KnowledgeItem.entity_type.in_(entity_types),
                or_(
                    exact_match,
                    lexical_match,
                    trigram_score >= 0.34,
                ),
            )
            .order_by(
                exact_match.desc(),
                lexical_score.desc(),
                trigram_score.desc(),
                KnowledgeItem.entity_type,
                KnowledgeItem.canonical_id,
            )
            .limit(max(1, min(int(limit), 500)))
        )

    def _portable_candidate_rows(
        self,
        *,
        version_id,
        entity_types,
        normalized_query,
        query_forms,
        alias_targets,
        limit,
    ):
        statement = (
            select(KnowledgeItem)
            .where(
                KnowledgeItem.knowledge_version_id == version_id,
                KnowledgeItem.current_review_status.in_(
                    [ReviewStatus.APPROVED, ReviewStatus.CORRECTED]
                ),
                KnowledgeItem.entity_type.in_(entity_types),
            )
            .order_by(KnowledgeItem.entity_type, KnowledgeItem.canonical_id)
            .limit(5000)
        )
        rows = []
        for item in self.session.scalars(statement):
            payload = self._effective_payload(item)
            label = safe_entity_label(item.entity_type, payload, fallback=item.title)
            normalized_label = normalize_entity_text(label)
            lexical = portable_lexical_score(normalized_label, normalized_query)
            trigram = portable_trigram_score(normalized_label, query_forms)
            score, _, _, _ = self._score(
                normalized_label=normalized_label,
                normalized_query=normalized_query,
                query_forms=query_forms,
                alias_targets=alias_targets,
                lexical_score=lexical,
                trigram_score=trigram,
            )
            if score > 0:
                rows.append((item, lexical, trigram))
        rows.sort(key=lambda row: (-row[1], -row[2], row[0].entity_type, row[0].canonical_id))
        return rows[:limit]

    def _corrected_items(self, *, version_id, entity_types):
        statement = (
            select(KnowledgeItem)
            .where(
                KnowledgeItem.knowledge_version_id == version_id,
                KnowledgeItem.current_review_status == ReviewStatus.CORRECTED,
                KnowledgeItem.entity_type.in_(entity_types),
            )
            .order_by(KnowledgeItem.entity_type, KnowledgeItem.canonical_id)
            .limit(1000)
        )
        return list(self.session.scalars(statement))

    def _effective_payload(self, item):
        if (
            self.effective is None
            or getattr(item, "current_review_status", None) != ReviewStatus.CORRECTED
        ):
            return item.source_payload
        return self.effective.describe(item.id)["effective_payload"]

    def _dialect_name(self) -> str:
        try:
            return str(self.session.get_bind().dialect.name)
        except (AttributeError, TypeError):
            return "portable"

    def _matched_alias_targets(self, query_forms: set[str]) -> set[str]:
        targets = set()
        for canonical_label, aliases in self.aliases.items():
            target = normalize_entity_text(canonical_label)
            if not target:
                continue
            for alias in aliases or []:
                normalized_alias = normalize_entity_text(alias)
                if normalized_alias and normalized_alias in query_forms:
                    targets.add(target)
                    break
        return targets

    @staticmethod
    def _score(
        *,
        normalized_label,
        normalized_query,
        query_forms,
        alias_targets,
        lexical_score,
        trigram_score,
    ):
        channels = []
        matched_terms = []
        channel_scores: list[tuple[str, float]] = []

        def add_channel(channel: str, score: float) -> None:
            channels.append(channel)
            channel_scores.append((channel, float(score)))

        if normalized_label in query_forms:
            add_channel("normalized_mention", 1.0)
            matched_terms.append(normalized_label)

        if normalized_label in alias_targets:
            add_channel("alias", 0.99)
            matched_terms.append(normalized_label)

        lexical_score = float(lexical_score or 0.0)
        if lexical_score > 0:
            add_channel(
                "lexical",
                min(0.94, 0.72 + math.log1p(lexical_score) * 0.12),
            )

        trigram_score = float(trigram_score or 0.0)
        if trigram_score >= 0.34:
            add_channel("trigram", min(0.93, 0.55 + trigram_score * 0.38))

        # Portable word containment is useful for corrected effective labels and
        # mirrors PostgreSQL word_similarity without claiming the same metric.
        if contains_normalized_phrase(normalized_query, normalized_label):
            add_channel("normalized_containment", 0.97)
            matched_terms.append(normalized_label)

        score_values = [score for _, score in channel_scores]
        return (
            max(score_values) if score_values else 0.0,
            tuple(dict.fromkeys(channels)),
            tuple(dict.fromkeys(matched_terms)),
            tuple(dict.fromkeys(channel_scores)),
        )


def normalize_entity_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def contains_normalized_phrase(normalized_query: str, normalized_label: str) -> bool:
    if not normalized_query or not normalized_label:
        return False
    return f" {normalized_label} " in f" {normalized_query} "


def singularize_token(token: str) -> str:
    if len(token) >= 5 and token.endswith("es"):
        return token[:-2]
    if len(token) >= 4 and token.endswith("s"):
        return token[:-1]
    return token


def query_entity_forms(normalized_query: str, *, max_ngram: int = 6) -> set[str]:
    tokens = normalized_query.split()
    forms = {normalized_query} if normalized_query else set()
    if not tokens:
        return forms

    max_size = min(max_ngram, len(tokens))
    for size in range(1, max_size + 1):
        for start in range(0, len(tokens) - size + 1):
            chunk = tokens[start : start + size]
            forms.add(" ".join(chunk))
            singularized = [singularize_token(token) for token in chunk]
            forms.add(" ".join(singularized))
    return {form for form in forms if form}


def lexical_query_terms(normalized_query: str) -> tuple[str, ...]:
    terms = []
    for token in normalized_query.split():
        if len(token) < 2 or token in LEXICAL_STOPWORDS:
            continue
        if not re.fullmatch(r"[a-z0-9_]+", token):
            continue
        terms.append(token)
        singularized = singularize_token(token)
        if singularized != token:
            terms.append(singularized)
    return tuple(dict.fromkeys(terms))


def portable_lexical_score(normalized_label: str, normalized_query: str) -> float:
    label_terms = set(normalized_label.split())
    query_terms = set(lexical_query_terms(normalized_query))
    if not label_terms or not query_terms:
        return 0.0
    overlap = len(label_terms & query_terms)
    return overlap / len(label_terms) if overlap else 0.0


def portable_trigram_score(normalized_label: str, query_forms: set[str]) -> float:
    if not normalized_label or not query_forms:
        return 0.0
    return max(SequenceMatcher(None, normalized_label, form).ratio() for form in query_forms)


def safe_entity_label(entity_type: str, payload: dict, *, fallback: str | None = None) -> str:
    for key in LABEL_KEYS.get(entity_type, ("label", "name", "title")):
        value, detections = sanitize_text(payload.get(key), 240)
        if value and not detections:
            return value
    value, detections = sanitize_text(fallback, 240)
    if value and not detections:
        return value
    return "Entidad validada"
