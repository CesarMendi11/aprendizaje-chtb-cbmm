from pathlib import Path
from datetime import datetime
import json

from playwright.sync_api import Page
from src.utils.text_utils import slugify

class ScreenExtractor:
    def __init__(self, page: Page, profile: dict):
        self.page = page
        self.profile = profile

    def extract_screen_data(self) -> dict:
        return {
            "url": self.page.url,
            "title": self.page.title(),
            "visible_text": self._safe_body_text(),
            "buttons": self._extract_buttons(),
            "inputs": self._extract_inputs(),
            "links": self._extract_links(),
            "tables": self._extract_tables(),
            "interactive_elements": self._extract_interactive_elements(),
        }

    def save_raw_json(self, data: dict, prefix: str | None = None) -> Path:
        output_dir = Path(self.profile["output"]["raw_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if prefix:
            safe_prefix = slugify(prefix)
        else:
            safe_prefix = slugify(data.get("title") or data.get("url") or "screen")

        file_path = output_dir / f"{safe_prefix}_{timestamp}.json"

        with open(file_path, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)

        return file_path

    def _safe_body_text(self) -> str:
        try:
            text = self.page.locator("body").inner_text()
            max_chars = self.profile["extraction"].get("max_visible_text_chars", 8000)

            if len(text) > max_chars:
                return text[:max_chars] + "\n...[TRUNCATED]"

            return text

        except Exception:
            return ""

    def _extract_buttons(self) -> list[dict]:
        buttons = []

        for button in self.page.locator("button").all():
            try:
                text = button.inner_text().strip()
                if text:
                    buttons.append({
                        "text": text,
                        "disabled": button.is_disabled(),
                    })
            except Exception:
                continue

        return buttons

    def _extract_inputs(self) -> list[dict]:
        inputs = []

        for input_el in self.page.locator("input").all():
            try:
                inputs.append({
                    "id": input_el.get_attribute("id"),
                    "name": input_el.get_attribute("name"),
                    "placeholder": input_el.get_attribute("placeholder"),
                    "type": input_el.get_attribute("type"),
                    "required": input_el.get_attribute("required") is not None,
                })
            except Exception:
                continue

        return inputs

    def _extract_links(self) -> list[dict]:
        links = []

        for link in self.page.locator("a[href]").all():
            try:
                text = link.inner_text().strip()
                href = link.get_attribute("href")

                if href:
                    links.append({
                        "text": text,
                        "href": href,
                    })
            except Exception:
                continue

        return links

    def _extract_tables(self) -> list[dict]:
        tables = []

        for table in self.page.locator("table").all():
            try:
                headers = table.locator("th").all_inner_texts()
                rows_count = table.locator("tr").count()

                tables.append({
                    "headers": headers,
                    "rows_count": rows_count,
                })
            except Exception:
                continue

        return tables

    def _extract_interactive_elements(self) -> list[dict]:
        selectors = self.profile["navigation"].get("interactive_selectors", [
            "a[href]",
            "button",
            "input",
            "select",
            "textarea",
            "[role='button']",
            "[role='menuitem']",
        ])

        return self.page.evaluate(
            """
            (selectors) => {
                const joinedSelectors = selectors.join(",");
                const nodes = Array.from(document.querySelectorAll(joinedSelectors));

                const getText = (el) => {
                    return (el.innerText || el.textContent || "").trim();
                };

                const getCssPath = (el) => {
                    if (!(el instanceof Element)) return null;

                    const path = [];

                    while (el && el.nodeType === Node.ELEMENT_NODE) {
                        let selector = el.nodeName.toLowerCase();

                        if (el.id) {
                            selector += "#" + el.id;
                            path.unshift(selector);
                            break;
                        }

                        let sibling = el;
                        let nth = 1;

                        while (sibling.previousElementSibling) {
                            sibling = sibling.previousElementSibling;
                            if (sibling.nodeName.toLowerCase() === selector) nth++;
                        }

                        selector += `:nth-of-type(${nth})`;
                        path.unshift(selector);
                        el = el.parentElement;
                    }

                    return path.join(" > ");
                };

                return nodes
                    .map((el, index) => {
                        const rect = el.getBoundingClientRect();

                        return {
                            index,
                            tag: el.tagName.toLowerCase(),
                            text: getText(el),
                            href: el.getAttribute("href"),
                            role: el.getAttribute("role"),
                            aria_label: el.getAttribute("aria-label"),
                            id: el.id || null,
                            name: el.getAttribute("name"),
                            type: el.getAttribute("type"),
                            placeholder: el.getAttribute("placeholder"),
                            classes: typeof el.className === "string" ? el.className : "",
                            css_path: getCssPath(el),
                            visible: !!(rect.width && rect.height),
                            position: {
                                x: rect.x,
                                y: rect.y,
                                width: rect.width,
                                height: rect.height
                            }
                        };
                    })
                    .filter(item =>
                        item.visible &&
                        (
                            item.text ||
                            item.href ||
                            item.placeholder ||
                            item.aria_label ||
                            item.id ||
                            item.name
                        )
                    );
            }
            """,
            selectors
        )
        
    def save_screenshot(self, prefix: str | None = None) -> Path:
        output_dir = Path(self.profile["output"]["screenshots_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_prefix = slugify(prefix or self.page.title() or "screen")

        file_path = output_dir / f"{safe_prefix}_{timestamp}.png"
        self.page.screenshot(path=file_path, full_page=True)

        return file_path

    def save_html(self, prefix: str | None = None) -> Path:
        output_dir = Path(self.profile["output"]["html_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_prefix = slugify(prefix or self.page.title() or "screen")

        file_path = output_dir / f"{safe_prefix}_{timestamp}.html"

        with open(file_path, "w", encoding="utf-8") as file:
            file.write(self.page.content())

        return file_path