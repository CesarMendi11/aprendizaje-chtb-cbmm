from src.discovery.link_normalizer import LinkNormalizer
from src.policy.route_policy import RoutePolicy


class LinkDiscovery:
    def __init__(self, route_policy: RoutePolicy):
        self.route_policy = route_policy

    def extract_allowed_links(self, screen_data: dict) -> list[str]:
        links = []

        for link in screen_data.get("links", []):
            href = link.get("href")

            normalized = LinkNormalizer.normalize(href)

            if normalized == "/":
                continue

            if normalized and self.route_policy.is_allowed(normalized):
                links.append(normalized)

        return list(dict.fromkeys(links))