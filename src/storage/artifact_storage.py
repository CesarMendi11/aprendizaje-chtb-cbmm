import json
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page

from src.utils.text_utils import slugify


class ArtifactStorage:
    def __init__(self, profile: dict):
        self.profile = profile

    def save_json(self, data: dict, prefix: str | None = None) -> Path:
        output_dir = Path(self.profile["output"]["raw_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_prefix = slugify(prefix or data.get("title") or data.get("url") or "screen")

        file_path = output_dir / f"{safe_prefix}_{timestamp}.json"

        with open(file_path, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)

        return file_path

    def save_screenshot(self, page: Page, prefix: str | None = None) -> Path:
        output_dir = Path(self.profile["output"]["screenshots_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_prefix = slugify(prefix or page.title() or "screen")

        file_path = output_dir / f"{safe_prefix}_{timestamp}.png"
        page.screenshot(path=file_path, full_page=True)

        return file_path

    def save_html(self, page: Page, prefix: str | None = None) -> Path:
        output_dir = Path(self.profile["output"]["html_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_prefix = slugify(prefix or page.title() or "screen")

        file_path = output_dir / f"{safe_prefix}_{timestamp}.html"

        with open(file_path, "w", encoding="utf-8") as file:
            file.write(page.content())

        return file_path