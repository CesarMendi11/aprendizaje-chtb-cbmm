from src.graph.state_flow_graph_builder import StateFlowGraphBuilder
from src.models.crawl_path import CrawlPath
from src.models.transition import Transition
from src.models.ui_event import EventDecision, RiskLevel, UIEvent, UIEventType
from src.models.ui_state import UIState


def state(state_id: str) -> UIState:
    return UIState(
        state_id=state_id,
        route="/admin/home",
        title="ERP",
        exact_signature=f"exact-{state_id}",
        structural_signature=f"struct-{state_id}",
        summary={},
        path=CrawlPath(root_state_id=state_id),
    )


def event() -> UIEvent:
    return UIEvent(
        event_type=UIEventType.EXPAND_MENU,
        label="Abrir menú",
        selector="button.menu",
        decision=EventDecision.ALLOW,
        risk_level=RiskLevel.LOW,
    )


def test_state_flow_graph_deduplicates_transitions():
    graph = StateFlowGraphBuilder()
    source = state("source")
    target = state("target")
    transition = Transition(source.state_id, target.state_id, event())

    graph.add_state(source)
    graph.add_state(target)

    assert graph.add_transition(transition) is True
    assert graph.add_transition(transition) is False

    payload = graph.to_dict()
    assert payload["summary"] == {
        "states_count": 2,
        "transitions_count": 1,
    }
