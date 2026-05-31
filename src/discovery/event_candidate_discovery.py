from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.policy.route_policy import RoutePolicy


@dataclass
class EventCandidate:
    """
    Representa un elemento candidato para interacción UI.

    Este objeto NO ejecuta el evento.
    Solo describe qué elemento podría ser probado por el crawler.
    """

    label: str
    selector: str
    tag: str
    source: str
    event_type: str = "click"
    action_kind: str = "unknown"
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    dangerous: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "selector": self.selector,
            "tag": self.tag,
            "source": self.source,
            "event_type": self.event_type,
            "action_kind": self.action_kind,
            "score": self.score,
            "reasons": self.reasons,
            "dangerous": self.dangerous,
            "metadata": self.metadata,
        }


class EventCandidateDiscovery:
    """
    Descubre candidatos de interacción tipo Crawljax.

    Responsabilidad:
    - Tomar screen_data.
    - Detectar elementos clickeables aunque no tengan href.
    - Asignar puntaje de confianza.
    - Marcar acciones peligrosas.
    - Devolver candidatos ordenados.

    Este componente NO hace click.
    Este componente NO usa Playwright directamente.
    Este componente NO modifica el ERP.
    """

    GENERIC_CONTAINER_WORDS = {
        "menu de navegación",
        "menu de navegacion",
        "menú de navegación",
        "navigation menu",
    }

    NAVIGATION_WORDS = [
        "menu",
        "nav",
        "navigation",
        "sidebar",
        "drawer",
        "collapse",
        "collapsable",
        "accordion",
        "tab",
        "item",
    ]

    LOW_VALUE_LABELS = {
        "admin",
        "administrador",
        "usuario",
        "user",
        "perfil",
        "profile",
        "cuenta",
        "account",
    }

    LOW_VALUE_SELECTOR_WORDS = [
        "user",
        "profile",
        "avatar",
        "toolbar",
        "topbar",
        "header",
        "navbar-user",
        "account",
        "logout",
        "signout",
    ]

    HIGH_VALUE_SELECTOR_WORDS = [
        "vertical-navigation",
        "side",
        "sidebar",
        "drawer",
        "menu",
        "navigation-content",
        "collapsable",
        "accordion",
        "tree",
    ]

    def __init__(self, profile: dict[str, Any], policy: RoutePolicy):
        self.profile = profile
        self.policy = policy

        ui_events = profile.get("ui_events", {})
        candidate_limits = ui_events.get("candidate_limits", {})

        self.enabled = ui_events.get("enabled", True)
        self.min_candidate_score = ui_events.get("min_candidate_score", 3)
        self.max_candidates_per_screen = candidate_limits.get(
            "max_candidates_per_screen",
            80,
        )
        self.max_text_length = candidate_limits.get("max_text_length", 100)

        forms = profile.get("forms", {})
        self.forms_enabled = forms.get("enabled", False)
        self.forms_allow_submit = forms.get("allow_submit", False)

    def discover_candidates(self, screen_data: dict[str, Any]) -> list[EventCandidate]:
        if not self.enabled:
            return []

        raw_candidates: list[EventCandidate] = []

        raw_candidates.extend(self._from_links(screen_data.get("links", [])))
        raw_candidates.extend(self._from_buttons(screen_data.get("buttons", [])))
        raw_candidates.extend(
            self._from_custom_interactives(
                screen_data.get("custom_interactives", [])
            )
        )

        candidates = self._deduplicate(raw_candidates)

        candidates = [
            candidate
            for candidate in candidates
            if candidate.score >= self.min_candidate_score or candidate.dangerous
        ]

        candidates.sort(
            key=lambda candidate: (
                candidate.dangerous,
                -candidate.score,
                candidate.label.lower(),
            )
        )

        return candidates[: self.max_candidates_per_screen]

    def discover_safe_candidates(self, screen_data: dict[str, Any]) -> list[EventCandidate]:
        return [
            candidate
            for candidate in self.discover_candidates(screen_data)
            if not candidate.dangerous
        ]

    def _from_links(self, links: list[dict[str, Any]]) -> list[EventCandidate]:
        candidates = []

        for item in links:
            label = self._best_label(item)
            selector = item.get("selector", "")
            tag = (item.get("tag") or "a").lower()

            candidate = EventCandidate(
                label=label,
                selector=selector,
                tag=tag,
                source="links",
                action_kind="link_navigation",
                metadata={
                    "href": item.get("href"),
                    "absolute_href": item.get("absolute_href"),
                },
            )

            self._score_candidate(candidate, item)
            candidates.append(candidate)

        return candidates

    def _from_buttons(self, buttons: list[dict[str, Any]]) -> list[EventCandidate]:
        candidates = []

        for item in buttons:
            label = self._best_label(item)
            selector = item.get("selector", "")
            tag = (item.get("tag") or "button").lower()
            button_type = item.get("type")

            candidate = EventCandidate(
                label=label,
                selector=selector,
                tag=tag,
                source="buttons",
                action_kind="button_click",
                metadata={
                    "type": button_type,
                    "role": item.get("role"),
                    "aria_label": item.get("aria_label"),
                    "title": item.get("title"),
                },
            )

            self._score_candidate(candidate, item)

            if button_type == "submit" and not self.forms_allow_submit:
                candidate.dangerous = True
                candidate.reasons.append("submit_button_blocked_by_forms_policy")
                candidate.score -= 3

            candidates.append(candidate)

        return candidates

    def _from_custom_interactives(
        self,
        custom_interactives: list[dict[str, Any]],
    ) -> list[EventCandidate]:
        candidates = []

        for item in custom_interactives:
            label = self._best_label(item)
            selector = item.get("selector", "")
            tag = (item.get("tag") or "").lower()

            candidate = EventCandidate(
                label=label,
                selector=selector,
                tag=tag,
                source="custom_interactives",
                action_kind=self._infer_action_kind(item),
                metadata={
                    "role": item.get("role"),
                    "aria_expanded": item.get("aria_expanded"),
                    "onclick": item.get("onclick"),
                },
            )

            self._score_candidate(candidate, item)
            candidates.append(candidate)

        return candidates

    def _score_candidate(self, candidate: EventCandidate, raw_item: dict[str, Any]) -> None:
        label = candidate.label
        tag = candidate.tag
        selector = candidate.selector.lower()
        role = (raw_item.get("role") or "").lower()
        aria_expanded = raw_item.get("aria_expanded")
        onclick = raw_item.get("onclick")

        if self._is_bad_container_text(label):
            candidate.score -= 10
            candidate.reasons.append("ignored_large_or_generic_container_text")
            return

        if label:
            candidate.score += 2
            candidate.reasons.append("has_label")

        if label and len(label) <= self.max_text_length:
            candidate.score += 1
            candidate.reasons.append("label_length_ok")

        if tag in {"a", "button"}:
            candidate.score += 2
            candidate.reasons.append("native_interactive_tag")

        if role in {"button", "menuitem", "tab", "option"}:
            candidate.score += 2
            candidate.reasons.append("interactive_role")

        if aria_expanded is not None:
            candidate.score += 3
            candidate.reasons.append("has_aria_expanded")

        if onclick:
            candidate.score += 3
            candidate.reasons.append("has_onclick")

        if self._looks_like_navigation_selector(selector):
            candidate.score += 2
            candidate.reasons.append("navigation_like_selector")

        if self._looks_like_high_value_navigation(selector):
            candidate.score += 3
            candidate.reasons.append("high_value_navigation_area")

        if self._looks_like_low_value_area(selector):
            candidate.score -= 5
            candidate.reasons.append("low_value_header_or_user_area")

        if self._looks_like_low_value_label(label):
            candidate.score -= 5
            candidate.reasons.append("low_value_user_or_profile_label")

        if self._is_custom_element(tag):
            candidate.score += 1
            candidate.reasons.append("custom_element")

        if self._looks_like_collapsable(tag, selector, aria_expanded):
            candidate.score += 2
            candidate.reasons.append("collapsable_like_element")

        if candidate.selector:
            candidate.score += 1
            candidate.reasons.append("has_selector")

        if self.policy.is_dangerous_action_label(label):
            candidate.dangerous = True
            candidate.score -= 5
            candidate.reasons.append("dangerous_text_detected")

        if self.policy.is_safe_action_label(label):
            candidate.score += 1
            candidate.reasons.append("safe_text_detected")

        if not candidate.selector:
            candidate.score -= 5
            candidate.reasons.append("missing_selector")

    def _infer_action_kind(self, item: dict[str, Any]) -> str:
        tag = (item.get("tag") or "").lower()
        selector = (item.get("selector") or "").lower()
        role = (item.get("role") or "").lower()
        aria_expanded = item.get("aria_expanded")

        if aria_expanded is not None:
            return "expand_or_collapse"

        if role == "tab" or "tab" in selector:
            return "tab_click"

        if role == "menuitem":
            return "menu_item_click"

        if self._looks_like_collapsable(tag, selector, aria_expanded):
            return "expand_or_collapse"

        if self._looks_like_navigation_selector(selector):
            return "navigation_click"

        return "generic_ui_click"

    def _best_label(self, item: dict[str, Any]) -> str:
        values = [
            item.get("text"),
            item.get("aria_label"),
            item.get("title"),
            item.get("placeholder"),
            item.get("name"),
            item.get("id"),
        ]

        for value in values:
            cleaned = self._clean_text(value)
            if cleaned:
                return cleaned

        return ""

    def _clean_text(self, value: Any) -> str:
        if value is None:
            return ""

        text = str(value)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _is_bad_container_text(self, label: str) -> bool:
        normalized = label.lower().strip()

        if not normalized:
            return False

        if normalized in self.GENERIC_CONTAINER_WORDS:
            return True

        if any(normalized.startswith(item) for item in self.GENERIC_CONTAINER_WORDS):
            return True

        if len(label) > self.max_text_length:
            return True

        return False

    def _looks_like_navigation_selector(self, selector: str) -> bool:
        return any(word in selector for word in self.NAVIGATION_WORDS)

    def _is_custom_element(self, tag: str) -> bool:
        return "-" in tag

    def _looks_like_collapsable(
        self,
        tag: str,
        selector: str,
        aria_expanded: Any,
    ) -> bool:
        if aria_expanded is not None:
            return True

        values = f"{tag} {selector}".lower()

        return any(
            word in values
            for word in [
                "collapse",
                "collapsable",
                "accordion",
                "expansion",
                "tree",
                "submenu",
            ]
        )

    def _looks_like_high_value_navigation(self, selector: str) -> bool:
        return any(word in selector for word in self.HIGH_VALUE_SELECTOR_WORDS)

    def _looks_like_low_value_area(self, selector: str) -> bool:
        return any(word in selector for word in self.LOW_VALUE_SELECTOR_WORDS)

    def _looks_like_low_value_label(self, label: str) -> bool:
        normalized = label.lower().strip()
        return normalized in self.LOW_VALUE_LABELS

    def _deduplicate(self, candidates: list[EventCandidate]) -> list[EventCandidate]:
        best_by_key: dict[str, EventCandidate] = {}

        for candidate in candidates:
            key = self._candidate_key(candidate)

            existing = best_by_key.get(key)

            if existing is None or candidate.score > existing.score:
                best_by_key[key] = candidate

        return list(best_by_key.values())

    def _candidate_key(self, candidate: EventCandidate) -> str:
        if candidate.selector:
            return f"selector::{candidate.selector}"

        if candidate.label:
            return f"label::{candidate.label.lower()}::{candidate.tag}"

        return f"unknown::{candidate.tag}::{candidate.source}"