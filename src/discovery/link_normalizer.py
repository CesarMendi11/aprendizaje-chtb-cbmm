from urllib.parse import urlparse


class LinkNormalizer:
    @staticmethod
    def normalize(href: str | None) -> str | None:
        if not href:
            return None

        if href.startswith("http"):
            parsed = urlparse(href)
            return parsed.path

        if href.startswith("/"):
            return href

        return None