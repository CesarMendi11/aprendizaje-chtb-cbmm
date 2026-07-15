from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.models.ui_event import EventDecision, RiskLevel, UIEventType
from src.policy.route_policy import RoutePolicy


@dataclass(frozen=True)
class EventPolicyResult:
    """Resultado explicable de evaluar una interacción antes de ejecutarla."""

    decision: EventDecision
    risk_level: RiskLevel
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "risk_level": self.risk_level.value,
            "reasons": list(self.reasons),
        }


class EventPolicy:
    """
    Política de seguridad de eventos con denegación predeterminada.

    La ausencia de una palabra peligrosa no convierte una acción en segura.
    Solo las categorías permitidas explícitamente pueden ejecutarse.
    """

    DEFAULT_ALLOWED = {
        UIEventType.NAVIGATION_LINK,
        UIEventType.EXPAND_MENU,
        UIEventType.COLLAPSE_MENU,
        UIEventType.ACTIVATE_TAB,
        UIEventType.OPEN_READONLY_VIEW,
        UIEventType.OPEN_MODAL,
        UIEventType.CLOSE_MODAL,
        UIEventType.OPEN_DRAWER,
        UIEventType.CLOSE_DRAWER,
        UIEventType.OPEN_DROPDOWN,
        UIEventType.CLOSE_DROPDOWN,
        UIEventType.SUBMIT_SEARCH,
        UIEventType.CHANGE_PAGINATION,
    }

    DEFAULT_REVIEW = {
        UIEventType.EXPAND_ROW,
        UIEventType.UNKNOWN,
    }

    DEFAULT_FORBIDDEN = {
        UIEventType.MUTATIVE_ACTION,
    }

    def __init__(self, profile: dict[str, Any], route_policy: RoutePolicy):
        self.profile = profile
        self.route_policy = route_policy

        safety = profile.get("safety", {})
        forms = profile.get("forms", {})

        self.default_decision = self._parse_decision(
            safety.get("default_decision", EventDecision.DENY.value)
        )
        self.forms_allow_submit = bool(forms.get("allow_submit", False))

        self.allowed = self._parse_event_types(
            safety.get("allowed_event_categories"),
            fallback=self.DEFAULT_ALLOWED,
        )
        self.review = self._parse_event_types(
            safety.get("review_event_categories"),
            fallback=self.DEFAULT_REVIEW,
        )
        self.forbidden = self._parse_event_types(
            safety.get("forbidden_event_categories"),
            fallback=self.DEFAULT_FORBIDDEN,
        )

    def evaluate(
        self,
        event_type: UIEventType,
        label: str,
        metadata: dict[str, Any] | None = None,
        explicitly_dangerous: bool = False,
    ) -> EventPolicyResult:
        metadata = metadata or {}
        reasons: list[str] = []

        if bool(metadata.get("disabled")):
            reasons.append("element_disabled")
            return EventPolicyResult(
                decision=EventDecision.DENY,
                risk_level=RiskLevel.LOW,
                reasons=tuple(reasons),
            )

        if explicitly_dangerous or self.route_policy.is_dangerous_action_label(label):
            reasons.append("dangerous_text_or_signal_detected")
            return EventPolicyResult(
                decision=EventDecision.DENY,
                risk_level=RiskLevel.CRITICAL,
                reasons=tuple(reasons),
            )

        if event_type in self.forbidden or event_type == UIEventType.MUTATIVE_ACTION:
            reasons.append("event_category_forbidden")
            return EventPolicyResult(
                decision=EventDecision.DENY,
                risk_level=RiskLevel.HIGH,
                reasons=tuple(reasons),
            )

        button_type = str(metadata.get("type") or "").lower()
        if (
            button_type == "submit"
            and not self.forms_allow_submit
            and event_type != UIEventType.SUBMIT_SEARCH
        ):
            reasons.append("submit_blocked_by_forms_policy")
            return EventPolicyResult(
                decision=EventDecision.DENY,
                risk_level=RiskLevel.HIGH,
                reasons=tuple(reasons),
            )

        if event_type in self.allowed:
            reasons.append("event_category_explicitly_allowed")
            return EventPolicyResult(
                decision=EventDecision.ALLOW,
                risk_level=RiskLevel.LOW,
                reasons=tuple(reasons),
            )

        if event_type in self.review:
            reasons.append("event_category_requires_review")
            return EventPolicyResult(
                decision=EventDecision.REVIEW,
                risk_level=RiskLevel.MEDIUM,
                reasons=tuple(reasons),
            )

        reasons.append("default_policy_applied")
        risk = RiskLevel.MEDIUM
        if self.default_decision == EventDecision.DENY:
            risk = RiskLevel.HIGH

        return EventPolicyResult(
            decision=self.default_decision,
            risk_level=risk,
            reasons=tuple(reasons),
        )

    def _parse_event_types(
        self,
        values: Any,
        fallback: set[UIEventType],
    ) -> set[UIEventType]:
        if values is None:
            return set(fallback)

        parsed: set[UIEventType] = set()
        for value in values:
            try:
                parsed.add(UIEventType(str(value)))
            except ValueError:
                # Un valor desconocido nunca amplía permisos.
                continue
        return parsed

    def _parse_decision(self, value: Any) -> EventDecision:
        try:
            return EventDecision(str(value).lower())
        except ValueError:
            return EventDecision.DENY
