from erp_assistant.acquisition.discovery.event_candidate_discovery import EventCandidate
from erp_assistant.acquisition.policy.sandbox_policy import SandboxExplorationPolicy


def build_policy(*, environment="test", strategy="test_full"):
    return SandboxExplorationPolicy(
        {
            "crawl_mode": {
                "environment": environment,
                "strategy": strategy,
            },
            "sandbox_exploration": {
                "enabled": True,
                "root_states_only": True,
                "max_openers_per_root_state": 2,
                "allowed_regions": ["main_content"],
                "opener_label_prefixes": ["nuevo", "nueva", "crear", "edit"],
                "blocked_http_methods": ["POST", "PUT", "PATCH", "DELETE"],
            },
        }
    )


def candidate(label="Nueva retención", *, button_type=None, region="main_content"):
    return EventCandidate(
        label=label,
        selector="button.test",
        tag="button",
        source="buttons",
        event_category="mutative_action",
        decision="deny",
        risk_level="critical",
        dangerous=True,
        metadata={"type": button_type, "region": region},
    )


def test_sandbox_policy_allows_denied_non_submit_opener_only_in_test_full_root():
    policy = build_policy()

    result = policy.evaluate(
        candidate(),
        source_state_depth=0,
        is_home_route=False,
    )

    assert result.allowed is True
    assert "sandbox_root_opener_match" in result.reasons


def test_sandbox_policy_keeps_submit_blocked():
    policy = build_policy()

    result = policy.evaluate(
        candidate(button_type="submit"),
        source_state_depth=0,
        is_home_route=False,
    )

    assert result.allowed is False
    assert result.reasons == ("sandbox_submit_blocked",)


def test_sandbox_policy_does_not_activate_in_safe_strategy():
    policy = build_policy(strategy="safe")

    result = policy.evaluate(
        candidate(),
        source_state_depth=0,
        is_home_route=False,
    )

    assert result.allowed is False
    assert result.reasons == ("sandbox_not_active",)


def test_sandbox_policy_blocks_nested_state_opener():
    policy = build_policy()

    result = policy.evaluate(
        candidate(label="Editar"),
        source_state_depth=1,
        is_home_route=False,
    )

    assert result.allowed is False
    assert result.reasons == ("sandbox_root_state_only",)


def replay_event(*, label="Nueva retención", button_type=None, region="main_content"):
    from erp_assistant.acquisition.models.ui_event import (
        EventDecision,
        RiskLevel,
        UIEvent,
        UIEventType,
    )

    return UIEvent(
        event_type=UIEventType.MUTATIVE_ACTION,
        label=label,
        selector="button.test",
        decision=EventDecision.DENY,
        risk_level=RiskLevel.CRITICAL,
        metadata={
            "type": button_type,
            "region": region,
            "sandbox_override": {
                "authorized": True,
                "environment": "test",
                "strategy": "test_full",
                "base_decision": "deny",
                "base_risk_level": "critical",
                "reasons": [
                    "sandbox_test_environment",
                    "sandbox_test_full_strategy",
                    "sandbox_root_opener_match",
                    "sandbox_submit_not_present",
                ],
            },
        },
    )


def test_sandbox_policy_reauthorizes_persisted_opener_for_test_full_replay():
    policy = build_policy()

    result = policy.evaluate_replay_event(
        replay_event(),
        source_state_depth=0,
        is_home_route=False,
    )

    assert result.allowed is True
    assert "sandbox_replay_original_override_present" in result.reasons


def test_sandbox_policy_refuses_persisted_override_when_current_strategy_is_safe():
    policy = build_policy(strategy="safe")

    result = policy.evaluate_replay_event(
        replay_event(),
        source_state_depth=0,
        is_home_route=False,
    )

    assert result.allowed is False
    assert result.reasons == ("sandbox_not_active",)


def test_sandbox_policy_refuses_replay_when_original_override_is_missing():
    policy = build_policy()
    event = replay_event()
    event.metadata.pop("sandbox_override")

    result = policy.evaluate_replay_event(
        event,
        source_state_depth=0,
        is_home_route=False,
    )

    assert result.allowed is False
    assert result.reasons == ("sandbox_missing_original_authorization",)
