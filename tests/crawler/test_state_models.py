from src.models import (
    CrawlPath,
    CrawlPathStep,
    EventDecision,
    RiskLevel,
    Transition,
    UIEvent,
    UIEventType,
    UIState,
)


def test_state_models_create_reproducible_serializable_path():
    event = UIEvent(
        event_type=UIEventType.EXPAND_MENU,
        label="Cuentas por cobrar",
        selector="button.accounts",
        decision=EventDecision.ALLOW,
        risk_level=RiskLevel.LOW,
    )
    step = CrawlPathStep(
        source_state_id="state_root",
        target_state_id="state_accounts_open",
        event=event,
    )
    path = CrawlPath(root_state_id="state_root").append(step)
    state = UIState(
        state_id="state_accounts_open",
        route="/admin/home",
        title="Dashboard",
        exact_signature="exact",
        structural_signature="structural",
        summary={"expanded_menus": ["Cuentas por cobrar"]},
        path=path,
    )
    transition = Transition(
        source_state_id="state_root",
        target_state_id="state_accounts_open",
        event=event,
    )

    assert path.depth == 1
    assert path.target_state_id == "state_accounts_open"
    assert state.to_dict()["path"]["steps"][0]["event"]["event_type"] == "expand_menu"
    assert transition.to_dict()["event"]["decision"] == "allow"
