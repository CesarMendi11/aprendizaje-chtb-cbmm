from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.models.ui_event import EventDecision, RiskLevel, UIEvent, UIEventType
from src.policy.event_policy import EventPolicy
from src.policy.route_policy import RoutePolicy


@dataclass
class EventCandidate:
    """Elemento candidato para una interacción controlada de interfaz."""

    label: str
    selector: str
    tag: str
    source: str
    event_type: str = "click"
    action_kind: str = "unknown"
    event_category: str = UIEventType.UNKNOWN.value
    decision: str = EventDecision.REVIEW.value
    risk_level: str = RiskLevel.MEDIUM.value
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    policy_reasons: list[str] = field(default_factory=list)
    dangerous: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_allowed(self) -> bool:
        return self.decision == EventDecision.ALLOW.value and not self.dangerous

    def to_ui_event(self) -> UIEvent:
        return UIEvent(
            event_type=UIEventType(self.event_category),
            label=self.label,
            selector=self.selector,
            decision=EventDecision(self.decision),
            risk_level=RiskLevel(self.risk_level),
            source=self.source,
            tag=self.tag,
            reasons=tuple([*self.reasons, *self.policy_reasons]),
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "selector": self.selector,
            "tag": self.tag,
            "source": self.source,
            "event_type": self.event_type,
            "action_kind": self.action_kind,
            "event_category": self.event_category,
            "decision": self.decision,
            "risk_level": self.risk_level,
            "score": self.score,
            "reasons": self.reasons,
            "policy_reasons": self.policy_reasons,
            "dangerous": self.dangerous,
            "metadata": self.metadata,
        }


class EventCandidateDiscovery:
    """
    Descubre, clasifica y evalúa candidatos de interacción estilo Crawljax.

    El puntaje sirve para priorizar candidatos. La autorización no depende del
    puntaje: la decide ``EventPolicy`` mediante categorías explícitas y una
    política de denegación predeterminada.
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

    SEARCH_WORDS = {
        "buscar",
        "consultar",
        "filtrar",
        "filtro",
        "search",
        "filter",
    }
    READONLY_WORDS = {"ver", "detalle", "visualizar", "view", "details"}
    CLOSE_WORDS = {"cerrar", "close"}
    PAGINATION_WORDS = {
        "siguiente",
        "anterior",
        "primera",
        "primero",
        "última",
        "ultimo",
        "último",
        "next",
        "previous",
        "prev",
        "paginator",
        "pagination",
    }
    MUTATIVE_HINTS = {
        "crear",
        "nuevo",
        "nueva",
        "registrar",
        "guardar",
        "editar",
        "modificar",
        "actualizar",
        "eliminar",
        "borrar",
        "aprobar",
        "anular",
        "firmar",
        "emitir",
        "enviar",
        "procesar",
        "confirmar",
        "pagar",
        "finalizar",
        "publicar",
        "activar",
        "desactivar",
        "create",
        "save",
        "edit",
        "update",
        "delete",
        "approve",
        "submit",
        "send",
        "pay",
    }

    def __init__(self, profile: dict[str, Any], policy: RoutePolicy):
        self.profile = profile
        self.policy = policy
        self.event_policy = EventPolicy(profile, policy)

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
        self.home_route = profile.get("navigation", {}).get("home_url", "")

    def discover_candidates(self, screen_data: dict[str, Any]) -> list[EventCandidate]:
        """Devuelve una muestra equilibrada para auditoría y revisión.

        Las acciones denegadas o ambiguas no pueden desaparecer detrás de
        cientos de elementos de navegación global. Por eso se reservan cupos
        para DENY y REVIEW antes de completar con candidatos permitidos.
        """
        candidates = self._discover_all_candidates(screen_data)
        if not candidates:
            return []

        denied = [c for c in candidates if c.decision == EventDecision.DENY.value]
        review = [c for c in candidates if c.decision == EventDecision.REVIEW.value]
        allowed = [c for c in candidates if c.decision == EventDecision.ALLOW.value]

        denied.sort(key=self._audit_sort_key)
        review.sort(key=self._audit_sort_key)
        allowed.sort(key=self._exploration_sort_key)

        # Acciones peligrosas: se conservan todas salvo un límite defensivo.
        denied_limit = min(len(denied), max(10, self.max_candidates_per_screen // 3))
        review_limit = min(len(review), max(10, self.max_candidates_per_screen // 3))

        selected = denied[:denied_limit] + review[:review_limit]
        remaining = max(0, self.max_candidates_per_screen - len(selected))
        selected.extend(allowed[:remaining])
        return selected

    def discover_safe_candidates(self, screen_data: dict[str, Any]) -> list[EventCandidate]:
        candidates = [
            candidate
            for candidate in self._discover_all_candidates(screen_data)
            if candidate.is_allowed
        ]
        candidates.sort(key=self._exploration_sort_key)
        return candidates[: self.max_candidates_per_screen]

    def discover_review_candidates(
        self,
        screen_data: dict[str, Any],
    ) -> list[EventCandidate]:
        candidates = [
            candidate
            for candidate in self._discover_all_candidates(screen_data)
            if candidate.decision == EventDecision.REVIEW.value
        ]
        candidates.sort(key=self._audit_sort_key)
        return candidates[: self.max_candidates_per_screen]

    def _discover_all_candidates(
        self,
        screen_data: dict[str, Any],
    ) -> list[EventCandidate]:
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
        screen_path = str(screen_data.get("path") or "")
        for candidate in candidates:
            candidate.metadata.setdefault("screen_path", screen_path)
        return [
            candidate
            for candidate in candidates
            if (
                candidate.score >= self.min_candidate_score
                or candidate.dangerous
                or candidate.decision == EventDecision.REVIEW.value
            )
        ]

    def _from_links(self, links: list[dict[str, Any]]) -> list[EventCandidate]:
        candidates = []

        for item in links:
            candidate = EventCandidate(
                label=self._best_label(item),
                selector=item.get("selector", ""),
                tag=(item.get("tag") or "a").lower(),
                source="links",
                action_kind="link_navigation",
                metadata={
                    "href": item.get("href"),
                    "absolute_href": item.get("absolute_href"),
                    "region": item.get("region", "main_content"),
                },
            )
            self._finalize_candidate(candidate, item)
            candidates.append(candidate)

        return candidates

    def _from_buttons(self, buttons: list[dict[str, Any]]) -> list[EventCandidate]:
        candidates = []

        for item in buttons:
            button_type = item.get("type")
            candidate = EventCandidate(
                label=self._best_label(item),
                selector=item.get("selector", ""),
                tag=(item.get("tag") or "button").lower(),
                source="buttons",
                action_kind="button_click",
                metadata={
                    "type": button_type,
                    "role": item.get("role"),
                    "aria_label": item.get("aria_label"),
                    "title": item.get("title"),
                    "aria_expanded": item.get("aria_expanded"),
                    "aria_selected": item.get("aria_selected"),
                    "disabled": item.get("disabled"),
                    "region": item.get("region", "main_content"),
                },
            )

            self._score_candidate(candidate, item)

            if button_type == "submit" and not self.forms_allow_submit:
                # Una búsqueda puede usar submit; la clasificación posterior
                # distingue ese caso. El indicador se conserva para auditoría.
                candidate.reasons.append("submit_button_blocked_by_forms_policy")

            self._classify_and_apply_policy(candidate, item)
            candidates.append(candidate)

        return candidates

    def _from_custom_interactives(
        self,
        custom_interactives: list[dict[str, Any]],
    ) -> list[EventCandidate]:
        candidates = []

        for item in custom_interactives:
            candidate = EventCandidate(
                label=self._best_label(item),
                selector=item.get("selector", ""),
                tag=(item.get("tag") or "").lower(),
                source="custom_interactives",
                action_kind=self._infer_action_kind(item),
                metadata={
                    "role": item.get("role"),
                    "aria_expanded": item.get("aria_expanded"),
                    "aria_selected": item.get("aria_selected"),
                    "aria_controls": item.get("aria_controls"),
                    "onclick": item.get("onclick"),
                    "type": item.get("type"),
                    "disabled": item.get("disabled"),
                    "region": item.get("region", "main_content"),
                },
            )
            self._finalize_candidate(candidate, item)
            candidates.append(candidate)

        return candidates

    def _finalize_candidate(
        self,
        candidate: EventCandidate,
        raw_item: dict[str, Any],
    ) -> None:
        self._score_candidate(candidate, raw_item)
        self._classify_and_apply_policy(candidate, raw_item)

    def _classify_and_apply_policy(
        self,
        candidate: EventCandidate,
        raw_item: dict[str, Any],
    ) -> None:
        category = self._infer_event_category(candidate, raw_item)
        candidate.event_category = category.value

        policy_result = self.event_policy.evaluate(
            event_type=category,
            label=candidate.label,
            metadata=candidate.metadata,
            explicitly_dangerous=candidate.dangerous,
        )
        candidate.decision = policy_result.decision.value
        candidate.risk_level = policy_result.risk_level.value
        candidate.policy_reasons.extend(policy_result.reasons)

        if policy_result.decision == EventDecision.DENY:
            candidate.dangerous = True

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

        region = str(raw_item.get("region") or "main_content")
        candidate.metadata.setdefault("region", region)
        candidate.reasons.append(f"region:{region}")

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

    def _infer_event_category(
        self,
        candidate: EventCandidate,
        item: dict[str, Any],
    ) -> UIEventType:
        label_words = set(self._normalize_for_matching(candidate.label).split())
        selector = self._normalize_for_matching(candidate.selector)
        tag = self._normalize_for_matching(candidate.tag)
        role = self._normalize_for_matching(item.get("role"))
        aria_expanded = item.get("aria_expanded")
        button_type = self._normalize_for_matching(item.get("type"))
        # Usar la misma normalización que para las palabras de la etiqueta.
        # De lo contrario, textos como "Abrir menú" conservan la tilde en
        # ``combined`` y no coinciden con la señal ASCII "menu". Eso hacía que
        # una expansión claramente navegacional quedara como UNKNOWN/REVIEW.
        combined = self._normalize_for_matching(
            " ".join(
                str(value or "")
                for value in (
                    candidate.label,
                    candidate.selector,
                    candidate.tag,
                    item.get("role"),
                    item.get("title"),
                    item.get("aria_label"),
                )
            )
        )

        if candidate.dangerous or label_words.intersection(self.MUTATIVE_HINTS):
            return UIEventType.MUTATIVE_ACTION

        if candidate.source == "links" or candidate.action_kind == "link_navigation":
            return UIEventType.NAVIGATION_LINK

        if (
            "navigation-basic" in combined
            or "basic-item" in combined
            or tag == "a"
            or (role == "menuitem" and "collapsable" not in tag)
        ):
            return UIEventType.NAVIGATION_LINK

        if role == "tab" or "role='tab'" in selector or "[role=tab]" in selector:
            return UIEventType.ACTIVATE_TAB

        if role in {"combobox", "listbox"} or tag in {"select", "mat-select"}:
            if str(aria_expanded).lower() == "true":
                return UIEventType.CLOSE_DROPDOWN
            return UIEventType.OPEN_DROPDOWN

        if label_words.intersection(self.PAGINATION_WORDS) or any(
            word in combined for word in ("paginator", "pagination", "page-next", "page-prev")
        ):
            return UIEventType.CHANGE_PAGINATION

        if label_words.intersection(self.SEARCH_WORDS):
            return UIEventType.SUBMIT_SEARCH

        if button_type == "submit":
            return UIEventType.MUTATIVE_ACTION

        if aria_expanded is not None:
            normalized_expanded = str(aria_expanded).lower()
            if normalized_expanded == "true":
                return UIEventType.COLLAPSE_MENU
            return UIEventType.EXPAND_MENU

        if role == "tab" or "tab" in selector:
            return UIEventType.ACTIVATE_TAB

        if label_words.intersection(self.CLOSE_WORDS):
            if "drawer" in combined:
                return UIEventType.CLOSE_DRAWER
            return UIEventType.CLOSE_MODAL

        if "drawer" in combined:
            return UIEventType.OPEN_DRAWER

        if any(word in combined for word in ("dialog", "modal")):
            return UIEventType.OPEN_MODAL

        if self._looks_like_collapsable(tag, selector, aria_expanded) or "menu" in combined:
            return UIEventType.EXPAND_MENU

        if label_words.intersection(self.READONLY_WORDS):
            return UIEventType.OPEN_READONLY_VIEW

        if "row" in selector and ("expand" in combined or "detail" in combined):
            return UIEventType.EXPAND_ROW

        return UIEventType.UNKNOWN

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
        return re.sub(r"\s+", " ", str(value)).strip()

    def _normalize_for_matching(self, value: Any) -> str:
        text = self._clean_text(value).lower()
        translation = str.maketrans("áéíóúüñ", "aeiouun")
        return text.translate(translation)

    def _is_bad_container_text(self, label: str) -> bool:
        normalized = label.lower().strip()
        if not normalized:
            return False
        if normalized in self.GENERIC_CONTAINER_WORDS:
            return True
        if any(normalized.startswith(item) for item in self.GENERIC_CONTAINER_WORDS):
            return True
        return len(label) > self.max_text_length

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
            for word in (
                "collapse",
                "collapsable",
                "accordion",
                "expansion",
                "tree",
                "submenu",
            )
        )

    def _looks_like_high_value_navigation(self, selector: str) -> bool:
        return any(word in selector for word in self.HIGH_VALUE_SELECTOR_WORDS)

    def _looks_like_low_value_area(self, selector: str) -> bool:
        return any(word in selector for word in self.LOW_VALUE_SELECTOR_WORDS)

    def _looks_like_low_value_label(self, label: str) -> bool:
        return label.lower().strip() in self.LOW_VALUE_LABELS

    def _deduplicate(self, candidates: list[EventCandidate]) -> list[EventCandidate]:
        best_by_key: dict[str, EventCandidate] = {}
        for candidate in candidates:
            key = self._candidate_key(candidate)
            existing = best_by_key.get(key)
            if existing is None or candidate.score > existing.score:
                best_by_key[key] = candidate
        return list(best_by_key.values())

    def _candidate_key(self, candidate: EventCandidate) -> str:
        semantic_categories = {
            UIEventType.NAVIGATION_LINK.value,
            UIEventType.EXPAND_MENU.value,
            UIEventType.COLLAPSE_MENU.value,
            UIEventType.ACTIVATE_TAB.value,
        }
        if candidate.label and candidate.event_category in semantic_categories:
            href = str(candidate.metadata.get("href") or "").strip().lower()
            return (
                f"semantic::{candidate.event_category}::"
                f"{candidate.label.lower()}::{href}"
            )
        if candidate.selector:
            return f"selector::{candidate.selector}"
        if candidate.label:
            return f"label::{candidate.label.lower()}::{candidate.tag}"
        return f"unknown::{candidate.tag}::{candidate.source}"

    def _audit_sort_key(self, candidate: EventCandidate) -> tuple[int, int, str]:
        # Se priorizan elementos nativos/locales frente a contenedores globales.
        source_priority = {"buttons": 0, "links": 1, "custom_interactives": 2}
        return (
            source_priority.get(candidate.source, 3),
            -candidate.score,
            candidate.label.lower(),
        )

    def _exploration_sort_key(self, candidate: EventCandidate) -> tuple[int, int, int, str]:
        category_priority = {
            UIEventType.ACTIVATE_TAB.value: 0,
            UIEventType.OPEN_READONLY_VIEW.value: 1,
            UIEventType.SUBMIT_SEARCH.value: 2,
            UIEventType.OPEN_MODAL.value: 3,
            UIEventType.OPEN_DROPDOWN.value: 4,
            UIEventType.CHANGE_PAGINATION.value: 5,
            UIEventType.EXPAND_MENU.value: 6,
            UIEventType.CLOSE_MODAL.value: 7,
            UIEventType.NAVIGATION_LINK.value: 8,
        }
        return (
            self._region_priority(candidate),
            category_priority.get(candidate.event_category, 9),
            -candidate.score,
            candidate.label.lower(),
        )

    def _region_priority(self, candidate: EventCandidate) -> int:
        """Prioriza controles locales sin perder módulos en la pantalla raíz."""
        region = str(candidate.metadata.get("region") or "main_content")
        path = str(candidate.metadata.get("screen_path") or "")

        if region == "dialog":
            return 0
        if region == "main_content":
            return 1
        if region == "global_navigation":
            return 1 if path == self.home_route else 4
        if region == "header":
            return 5
        if region in {"footer", "volatile"}:
            return 6
        return 2
