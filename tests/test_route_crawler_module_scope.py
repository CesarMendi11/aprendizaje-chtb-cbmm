from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from src.crawler.frontier import Frontier
from src.crawler.module_scope import ModuleCrawlBoundary
from src.crawler.route_crawler import RouteCrawler
from src.crawler.state_signature import StateSignature
from src.crawler.state_registry import StateRegistry
from src.crawler.state_frontier import StateFrontier
from src.graph.routes_graph_builder import RoutesGraphBuilder
from src.graph.state_flow_graph_builder import StateFlowGraphBuilder
from src.models.crawl_path import CrawlPath, CrawlPathStep
from src.models.ui_event import EventDecision, RiskLevel, UIEvent, UIEventType
from src.models.ui_state import UIState


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
    crawler.state_frontier = StateFrontier()
    crawler._emit_progress = lambda *_args, **_kwargs: None
    crawler._checkpoint_outputs = lambda: None
    fake_state = UIState(
        state_id='ui_state:tracking',
        route='/admin/home',
        title='Tracking',
        exact_signature='exact-tracking',
        structural_signature='tracking',
        summary={},
        path=CrawlPath(root_state_id='ui_state:home'),
    )
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


class FakeDiscovery:
    def discover_allowed_links(self, screen_data):
        return list(screen_data.get('links', []))


class FakeRoutesGraph:
    def __init__(self):
        self.screens = set()
        self.transitions = []

    def has_screen(self, route):
        return route in self.screens

    def add_screen(self, route, **_kwargs):
        self.screens.add(route)

    def add_transition(self, **kwargs):
        self.transitions.append(kwargs)


def menu_event(label: str, selector: str) -> UIEvent:
    return UIEvent(
        event_type=UIEventType.EXPAND_MENU,
        label=label,
        selector=selector,
        decision=EventDecision.ALLOW,
        risk_level=RiskLevel.LOW,
    )


def module_menu_state(*selectors: str) -> UIState:
    path = CrawlPath(root_state_id='ui_state:home')
    source = 'ui_state:home'
    for index, selector in enumerate(selectors, start=1):
        target = f'ui_state:menu-{index}'
        path = path.append(
            CrawlPathStep(
                source_state_id=source,
                event=menu_event(f'Menu {index}', selector),
                target_state_id=target,
            )
        )
        source = target
    return UIState(
        state_id=source,
        route='/admin/home',
        title='Menu state',
        exact_signature=f'exact-{source}',
        structural_signature=source,
        summary={},
        path=path,
    )


def test_module_event_delta_admits_only_new_route_revealed_inside_branch():
    crawler = object.__new__(RouteCrawler)
    crawler.route_scope = {
        '/admin/tracking',
        '/admin/integrations/external',
    }
    crawler.module_boundary = boundary()
    crawler._module_dynamic_routes = set()
    crawler.policy = FakePolicy()
    crawler.discovery = FakeDiscovery()
    crawler.routes_graph = FakeRoutesGraph()
    crawler.frontier = Frontier()
    crawler.home_route = '/admin/home'

    source_screen = {
        'links': [
            {
                'route': '/admin/orders',
                'text': 'Orders',
                'region': 'global_navigation',
                'selector': '#orders',
                'href': '/admin/orders',
            },
        ],
    }
    after_screen = {
        'links': [
            *source_screen['links'],
            {
                'route': '/admin/integrations/new-screen',
                'text': 'New integration screen',
                'region': 'global_navigation',
                'selector': '#new-screen',
                'href': '/admin/integrations/new-screen',
            },
        ],
    }
    target_state = module_menu_state('#sales', '#tracking', '#integrations')
    result = SimpleNamespace(
        candidate={'event_category': 'expand_menu'},
        event=menu_event('Integrations', '#integrations'),
    )

    crawler._register_ui_event_discovered_links(
        source_route='/admin/home#state:integrations',
        source_screen_data=source_screen,
        after_screen_data=after_screen,
        target_state=target_state,
        result=result,
        depth=0,
    )

    assert '/admin/integrations/new-screen' in crawler.route_scope
    assert '/admin/integrations/new-screen' in crawler._module_dynamic_routes
    assert '/admin/orders' not in crawler.route_scope

    queued = []
    while crawler.frontier.has_pending():
        queued.append(crawler.frontier.pop().route)
    assert queued == ['/admin/integrations/new-screen']


def test_module_event_delta_does_not_admit_unknown_route_without_expand_menu():
    crawler = object.__new__(RouteCrawler)
    crawler.route_scope = {'/admin/tracking'}
    crawler.module_boundary = boundary()
    crawler._module_dynamic_routes = set()
    crawler.policy = FakePolicy()
    crawler.discovery = FakeDiscovery()
    crawler.routes_graph = FakeRoutesGraph()
    crawler.frontier = Frontier()
    crawler.home_route = '/admin/home'

    target_state = module_menu_state('#sales', '#tracking')
    result = SimpleNamespace(
        candidate={'event_category': 'open_readonly_view'},
        event=UIEvent(
            event_type=UIEventType.OPEN_READONLY_VIEW,
            label='Details',
            selector='#details',
            decision=EventDecision.ALLOW,
            risk_level=RiskLevel.LOW,
        ),
    )

    crawler._register_ui_event_discovered_links(
        source_route='/admin/home#state:details',
        source_screen_data={'links': []},
        after_screen_data={
            'links': [
                {
                    'route': '/admin/tracking/unknown-report',
                    'text': 'Unknown report',
                    'region': 'main_content',
                    'selector': '#report',
                    'href': '/admin/tracking/unknown-report',
                },
            ],
        },
        target_state=target_state,
        result=result,
        depth=0,
    )

    assert '/admin/tracking/unknown-report' not in crawler.route_scope
    assert not crawler.frontier.has_pending()


def test_module_event_budget_is_relative_to_selected_module_depth():
    crawler = object.__new__(RouteCrawler)
    crawler.module_boundary = boundary()
    crawler._module_entry_depth = 2
    crawler.max_event_depth = 2
    crawler.recursive_state_exploration = True

    entry = module_menu_state('#sales', '#tracking')
    first_descendant = module_menu_state('#sales', '#tracking', '#integrations')
    second_descendant = module_menu_state(
        '#sales', '#tracking', '#integrations', '#providers'
    )

    assert crawler._state_event_depth(entry.path) == 0
    assert crawler._state_event_depth(first_descendant.path) == 1
    assert crawler._state_event_depth(second_descendant.path) == 2
    assert crawler._should_queue_dynamic_state(True, first_descendant)
    assert not crawler._should_queue_dynamic_state(True, second_descendant)


def test_module_home_branch_keeps_expand_menu_categories_after_pinned_entry():
    crawler = object.__new__(RouteCrawler)
    crawler.module_boundary = boundary()
    crawler.home_route = '/admin/home'
    crawler.home_event_categories = {'expand_menu'}
    crawler.local_event_categories = {'activate_tab'}

    state = module_menu_state('#sales', '#tracking', '#integrations')

    assert crawler._categories_for_state(state) == {'expand_menu'}
