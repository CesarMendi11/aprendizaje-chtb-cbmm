from __future__ import annotations

from dataclasses import dataclass

from src.crawler.frontier import Frontier
from src.crawler.module_scope import ModuleCrawlBoundary
from src.crawler.route_crawler import RouteCrawler
from src.crawler.state_signature import StateSignature
from src.crawler.state_registry import StateRegistry
from src.graph.routes_graph_builder import RoutesGraphBuilder
from src.graph.state_flow_graph_builder import StateFlowGraphBuilder


class FakePolicy:
    def is_allowed_route(self, route):
        return bool(route and str(route).startswith('/admin/'))


@dataclass
class FakeObservation:
    signature: StateSignature
    screen_data: dict

    def diagnostics(self):
        return {'stable': True, 'final_route': self.signature.route}


class FakeNavigator:
    def __init__(self):
        self.home_calls = 0

    def goto_home(self):
        self.home_calls += 1

    def current_path(self):
        return '/admin/home'


class FakePage:
    def wait_for_timeout(self, _milliseconds):
        return None


class FakeInteractionExecutor:
    def __init__(self):
        self.clicked = []

    def click(self, selector):
        self.clicked.append(selector)
        return type('Result', (), {'success': True, 'error': None})()


class FakeUIEventExplorer:
    def __init__(self):
        self.interaction_executor = FakeInteractionExecutor()
        self.event_wait_ms = 0


def signature(name: str, title: str) -> StateSignature:
    return StateSignature(
        fingerprint=name,
        exact_fingerprint=f'exact-{name}',
        structural_fingerprint=name,
        route='/admin/home',
        title=title,
        summary={'title': title},
        exact_summary={'title': title},
    )


def boundary():
    return ModuleCrawlBoundary.from_payload(
        {
            'root_module_id': 'module:tracking',
            'module_ids': ['module:tracking', 'module:integrations'],
            'known_screen_routes': [
                '/admin/tracking',
                '/admin/integrations/external',
            ],
            'navigation_path': ['Sales', 'Tracking'],
            'navigation_origin_path': ['#sales', '#tracking'],
        }
    )


def test_module_route_scope_remains_exact_even_when_empty():
    crawler = object.__new__(RouteCrawler)
    crawler.route_scope = set()
    crawler.policy = FakePolicy()

    assert not crawler._is_allowed_route('/admin/tracking')


def test_enter_module_branch_builds_reproducible_navigation_path():
    crawler = object.__new__(RouteCrawler)
    crawler.navigator = FakeNavigator()
    crawler.page = FakePage()
    crawler.page_wait_ms = 0
    crawler.policy = FakePolicy()
    crawler.state_registry = StateRegistry()
    crawler.state_flow_graph = StateFlowGraphBuilder()
    crawler.routes_graph = RoutesGraphBuilder()
    crawler.ui_event_explorer = FakeUIEventExplorer()

    observations = iter(
        [
            FakeObservation(signature('home', 'Home'), {'path': '/admin/home'}),
            FakeObservation(signature('sales', 'Sales'), {'path': '/admin/home'}),
            FakeObservation(signature('tracking', 'Tracking'), {'path': '/admin/home'}),
        ]
    )
    crawler._observe_screen = lambda **_kwargs: next(observations)

    state, node_id = crawler._enter_module_branch(boundary())

    assert crawler.navigator.home_calls == 1
    assert crawler.ui_event_explorer.interaction_executor.clicked == ['#sales', '#tracking']
    assert state.path is not None
    assert [step.event.label for step in state.path.steps] == ['Sales', 'Tracking']
    assert [step.event.selector for step in state.path.steps] == ['#sales', '#tracking']
    assert state.path.metadata['target_module_id'] == 'module:tracking'
    assert node_id.startswith('/admin/home#state:')

    graph = crawler.routes_graph.to_dict()
    ui_nodes = [node for node in graph['nodes'] if node['metadata'].get('kind') == 'ui_state']
    assert len(ui_nodes) == 2
    assert ui_nodes[-1]['metadata']['path']['depth'] == 2


def test_crawl_module_seeds_only_pinned_known_routes():
    crawler = object.__new__(RouteCrawler)
    crawler.route_scope = {
        '/admin/tracking',
        '/admin/integrations/external',
    }
    crawler.frontier = Frontier()
    crawler.policy = FakePolicy()
    crawler.state_frontier = type('StateFrontier', (), {'mark_explored': lambda self, _id: None})()
    crawler._emit_progress = lambda *_args, **_kwargs: None
    crawler._checkpoint_outputs = lambda: None
    fake_state = type('State', (), {'state_id': 'ui_state:tracking'})()
    crawler._enter_module_branch = lambda _boundary: (fake_state, '/admin/home#state:tracking')

    captured = {}

    def consume():
        captured['routes'] = []
        while crawler.frontier.has_pending():
            captured['routes'].append(crawler.frontier.pop().route)

    crawler._crawl_until_fixed_point = consume
    crawler._save_outputs = lambda: 'summary'

    result = crawler.crawl_module(boundary())

    assert result == 'summary'
    assert captured['routes'] == [
        '/admin/tracking',
        '/admin/integrations/external',
    ]
    assert '/admin/orders' not in captured['routes']
