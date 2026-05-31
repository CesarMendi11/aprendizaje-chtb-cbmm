from src.crawler.frontier import CrawlTarget, Frontier


def test_frontier_pushes_and_pops_targets_fifo():
    frontier = Frontier()

    frontier.push(CrawlTarget(route="/admin/home", source="root", depth=0))
    frontier.push(CrawlTarget(route="/admin/facturas", source="/admin/home", depth=1))

    first = frontier.pop()
    second = frontier.pop()

    assert first is not None
    assert second is not None

    assert first.route == "/admin/home"
    assert second.route == "/admin/facturas"


def test_frontier_avoids_duplicate_pending_routes():
    frontier = Frontier()

    added_first = frontier.push(CrawlTarget(route="/admin/home", source="root"))
    added_second = frontier.push(CrawlTarget(route="/admin/home", source="root"))

    assert added_first is True
    assert added_second is False
    assert frontier.pending_count() == 1


def test_frontier_does_not_requeue_visited_routes():
    frontier = Frontier()

    frontier.mark_visited("/admin/home")

    added = frontier.push(CrawlTarget(route="/admin/home", source="root"))

    assert added is False
    assert frontier.pending_count() == 0
    assert frontier.is_visited("/admin/home")


def test_frontier_marks_route_as_visited():
    frontier = Frontier()

    frontier.push(CrawlTarget(route="/admin/home", source="root"))
    target = frontier.pop()

    assert target is not None

    frontier.mark_visited(target.route)

    assert frontier.is_visited("/admin/home")
    assert frontier.visited_count() == 1


def test_frontier_reports_pending_state():
    frontier = Frontier()

    assert not frontier.has_pending()

    frontier.push(CrawlTarget(route="/admin/home", source="root"))

    assert frontier.has_pending()
    assert frontier.pending_count() == 1


def test_frontier_clear_removes_everything():
    frontier = Frontier()

    frontier.push(CrawlTarget(route="/admin/home", source="root"))
    frontier.mark_visited("/admin/facturas")

    frontier.clear()

    assert frontier.pending_count() == 0
    assert frontier.visited_count() == 0
    assert not frontier.has_pending()