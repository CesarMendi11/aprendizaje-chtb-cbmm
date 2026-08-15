from __future__ import annotations

from pathlib import Path

from src.crawler.network_evidence import NetworkEvidenceCollector
from src.crawler.route_crawler import CrawlSummary
from src.database.enums import PipelineJobScope
from src.pipeline.crawl_job_executor import CrawlJobExecutor


class FakePage:
    def __init__(self, url: str):
        self.url = url
        self.listeners = {}

    def on(self, event: str, callback):
        self.listeners[event] = callback


class FakeRequest:
    def __init__(self, *, method="GET", resource_type="xhr"):
        self.method = method
        self.resource_type = resource_type

    @property
    def headers(self):  # pragma: no cover - must never be accessed
        raise AssertionError("headers must not be captured")

    @property
    def post_data(self):  # pragma: no cover - must never be accessed
        raise AssertionError("body must not be captured")


class FakeResponse:
    def __init__(self, url: str, *, status=200, method="GET", resource_type="xhr"):
        self.url = url
        self.status = status
        self.request = FakeRequest(method=method, resource_type=resource_type)

    @property
    def headers(self):  # pragma: no cover - must never be accessed
        raise AssertionError("headers must not be captured")

    def body(self):  # pragma: no cover - must never be accessed
        raise AssertionError("body must not be captured")


def profile():
    return {
        "erp": {"base_url": "https://erp.example.test"},
        "network_evidence": {
            "enabled": True,
            "resource_types": ["xhr", "fetch"],
            "include_query_keys": True,
            "max_records": 20,
        },
    }


def test_collector_sanitizes_ids_query_values_and_never_reads_bodies_or_headers():
    page = FakePage("https://erp.example.test/app/orders?session=secret")
    collector = NetworkEvidenceCollector(page, profile())

    collector._on_response(
        FakeResponse(
            "https://erp.example.test/api/orders/1234567890/items"
            "?status=open&token=super-secret&email=owner@example.test",
            status=200,
        )
    )
    collector._on_response(
        FakeResponse(
            "https://erp.example.test/api/orders/1234567890/items?status=closed",
            status=304,
        )
    )

    payload = collector.to_dict()
    assert payload["capture_policy"] == {
        "bodies_captured": False,
        "headers_captured": False,
        "query_values_captured": False,
        "resource_types": ["fetch", "xhr"],
    }
    assert payload["statistics"]["unique_observations"] == 1
    assert payload["statistics"]["total_observations"] == 2

    observation = payload["observations"][0]
    assert observation["screen_route"] == "/app/orders"
    assert observation["endpoint_path"] == "/api/orders/{id}/items"
    assert observation["query_keys"] == ["status"]
    assert observation["status_codes"] == [200, 304]
    assert observation["observed_count"] == 2

    serialized = str(payload).casefold()
    assert "1234567890" not in serialized
    assert "super-secret" not in serialized
    assert "owner@example.test" not in serialized
    assert "closed" not in serialized
    assert "open" not in serialized


def test_collector_hashes_external_origins_and_ignores_non_api_resource_types():
    page = FakePage("https://erp.example.test/app/products")
    collector = NetworkEvidenceCollector(page, profile())

    collector._on_response(
        FakeResponse(
            "https://api.partner.test/v2/products/550e8400-e29b-41d4-a716-446655440000",
            status=201,
            method="POST",
            resource_type="fetch",
        )
    )
    collector._on_response(
        FakeResponse(
            "https://cdn.example.test/logo.png",
            status=200,
            resource_type="image",
        )
    )

    observations = collector.to_dict()["observations"]
    assert len(observations) == 1
    observation = observations[0]
    assert observation["origin_kind"] == "external"
    assert observation["origin_id"].startswith("external:")
    assert observation["endpoint_path"] == "/v2/products/{id}"
    assert "api.partner.test" not in str(observation)


def test_crawl_result_exposes_network_evidence_artifact(tmp_path: Path):
    run_root = tmp_path / "run"
    network_path = (
        run_root / "processed" / "structural" / "network_evidence.json"
    )
    summary = CrawlSummary(
        visited_count=1,
        pending_count=0,
        nodes_count=1,
        edges_count=0,
        routes_graph_path=str(
            run_root / "processed" / "structural" / "routes_graph.json"
        ),
        screen_index_path=str(
            run_root / "processed" / "structural" / "screen_index.json"
        ),
        network_evidence_count=4,
        network_evidence_path=str(network_path),
    )

    result = CrawlJobExecutor._result(
        summary,
        run_root,
        PipelineJobScope.SCREEN,
        "/app/products",
    )

    assert result["network_evidence"] == 4
    assert result["network_evidence_path"].endswith("network_evidence.json")
