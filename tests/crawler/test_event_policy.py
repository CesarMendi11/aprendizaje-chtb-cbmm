from src.models.ui_event import EventDecision, UIEventType
from src.policy.event_policy import EventPolicy
from src.policy.route_policy import RoutePolicy


def build_profile():
    return {
        "erp": {"base_url": "http://localhost:8080"},
        "exploration": {"allowed_routes": ["/admin/"], "blocked_routes": []},
        "safety": {
            "default_decision": "deny",
            "dangerous_keywords": ["Eliminar", "Guardar"],
            "safe_keywords": ["Buscar", "Ver"],
            "allowed_event_categories": ["expand_menu", "submit_search"],
            "review_event_categories": ["unknown"],
            "forbidden_event_categories": ["mutative_action"],
        },
        "forms": {"allow_submit": False},
    }


def test_event_policy_is_deny_by_default():
    profile = build_profile()
    route_policy = RoutePolicy(profile)
    policy = EventPolicy(profile, route_policy)

    result = policy.evaluate(
        event_type=UIEventType.OPEN_MODAL,
        label="Abrir",
    )

    assert result.decision == EventDecision.DENY


def test_event_policy_keeps_unknown_for_human_review_without_execution():
    profile = build_profile()
    route_policy = RoutePolicy(profile)
    policy = EventPolicy(profile, route_policy)

    result = policy.evaluate(
        event_type=UIEventType.UNKNOWN,
        label="Operación especial",
    )

    assert result.decision == EventDecision.REVIEW


def test_event_policy_allows_explicit_search_submit():
    profile = build_profile()
    route_policy = RoutePolicy(profile)
    policy = EventPolicy(profile, route_policy)

    result = policy.evaluate(
        event_type=UIEventType.SUBMIT_SEARCH,
        label="Buscar",
        metadata={"type": "submit"},
    )

    assert result.decision == EventDecision.ALLOW


def test_event_policy_never_clicks_disabled_element():
    profile = build_profile()
    route_policy = RoutePolicy(profile)
    policy = EventPolicy(profile, route_policy)

    result = policy.evaluate(
        event_type=UIEventType.SUBMIT_SEARCH,
        label="Buscar",
        metadata={"disabled": True},
    )

    assert result.decision == EventDecision.DENY
    assert "element_disabled" in result.reasons
