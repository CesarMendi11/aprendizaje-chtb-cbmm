from src.crawler.state_frontier import StateFrontier, StateTarget
from src.models.crawl_path import CrawlPath
from src.models.ui_state import UIState


def make_state(state_id: str = "ui_state:abc") -> UIState:
    return UIState(
        state_id=state_id,
        route="/admin/home",
        title="ERP",
        exact_signature="exact",
        structural_signature=state_id.split(":", 1)[-1],
        summary={},
        path=CrawlPath(root_state_id=state_id),
    )


def test_state_frontier_distinguishes_states_in_same_route():
    frontier = StateFrontier()
    first = make_state("ui_state:first")
    second = make_state("ui_state:second")

    assert frontier.push_state(first) is True
    assert frontier.push_state(second) is True
    assert frontier.pending_count() == 2

    assert frontier.pop().state_id == first.state_id
    assert frontier.pop().state_id == second.state_id


def test_state_frontier_deduplicates_queued_and_explored_states():
    frontier = StateFrontier()
    state = make_state()

    assert frontier.push_state(state) is True
    assert frontier.push_state(state) is False

    target = frontier.pop()
    frontier.mark_explored(target.state_id)

    assert frontier.push_state(state) is False
    assert frontier.is_explored(state.state_id) is True


def test_state_frontier_rejects_negative_depth():
    frontier = StateFrontier()
    try:
        frontier.push(
            StateTarget(
                state_id="ui_state:x",
                path=CrawlPath(root_state_id="ui_state:x"),
                depth=-1,
            )
        )
    except ValueError as error:
        assert "negativo" in str(error)
    else:
        raise AssertionError("Se esperaba ValueError")
