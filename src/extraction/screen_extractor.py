from __future__ import annotations

from playwright.sync_api import Page


class ScreenExtractor:
    """
    Extrae información estructural de la pantalla actual.

    Responsabilidad:
    - Leer texto visible.
    - Extraer enlaces.
    - Extraer botones.
    - Extraer inputs, selects y textareas.
    - Extraer tablas.
    - Detectar elementos interactivos personalizados.

    Este componente NO navega.
    Este componente NO decide qué hacer.
    Este componente NO guarda archivos.
    """

    def __init__(self, page: Page, profile: dict):
        self.page = page
        self.profile = profile

        extraction_config = profile.get("extraction", {})
        self.max_visible_text_chars = extraction_config.get(
            "max_visible_text_chars",
            8000,
        )

    def extract(self) -> dict:
        data = self.page.evaluate(
            """
            () => {
                const isVisible = (element) => {
                    if (!element) return false;

                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();

                    return (
                        style &&
                        style.display !== "none" &&
                        style.visibility !== "hidden" &&
                        style.opacity !== "0" &&
                        rect.width > 0 &&
                        rect.height > 0
                    );
                };

                const normalizeText = (value) => {
                    if (!value) return "";
                    return String(value).replace(/\\s+/g, " ").trim();
                };

                const textOf = (element) => {
                    if (!element) return "";

                    const directText =
                        element.innerText ||
                        element.textContent ||
                        element.value ||
                        element.getAttribute("aria-label") ||
                        element.getAttribute("title") ||
                        "";

                    return normalizeText(directText);
                };

                const cssPath = (element) => {
                    if (!element || !element.tagName) return "";

                    const parts = [];
                    let current = element;

                    while (
                        current &&
                        current.nodeType === Node.ELEMENT_NODE &&
                        parts.length < 8
                    ) {
                        let selector = current.tagName.toLowerCase();

                        if (current.id) {
                            const escapedId = window.CSS && window.CSS.escape
                                ? window.CSS.escape(current.id)
                                : current.id.replace(/([^a-zA-Z0-9_-])/g, "\\\\$1");

                            selector += "#" + escapedId;
                            parts.unshift(selector);
                            break;
                        }

                        const parent = current.parentElement;

                        if (parent) {
                            const siblings = Array.from(parent.children)
                                .filter((child) => child.tagName === current.tagName);

                            if (siblings.length > 1) {
                                const index = siblings.indexOf(current) + 1;
                                selector += `:nth-of-type(${index})`;
                            }
                        }

                        parts.unshift(selector);
                        current = current.parentElement;
                    }

                    return parts.join(" > ");
                };

                const limit = (items, max = 300) => items.slice(0, max);

                const links = limit(
                    Array.from(document.querySelectorAll("a[href]"))
                        .filter(isVisible)
                        .map((element) => ({
                            text: textOf(element),
                            href: element.getAttribute("href"),
                            absolute_href: element.href,
                            selector: cssPath(element),
                            tag: element.tagName.toLowerCase()
                        }))
                );

                const buttons = limit(
                    Array.from(
                        document.querySelectorAll(
                            "button, [role='button'], input[type='button'], input[type='submit']"
                        )
                    )
                        .filter(isVisible)
                        .map((element) => ({
                            text: textOf(element),
                            type: element.getAttribute("type"),
                            role: element.getAttribute("role"),
                            aria_label: element.getAttribute("aria-label"),
                            title: element.getAttribute("title"),
                            selector: cssPath(element),
                            tag: element.tagName.toLowerCase()
                        }))
                );

                const labelForInput = (input) => {
                    if (!input) return "";

                    const id = input.getAttribute("id");

                    if (id) {
                        const label = document.querySelector(`label[for="${id}"]`);
                        if (label) return textOf(label);
                    }

                    const parentLabel = input.closest("label");
                    if (parentLabel) return textOf(parentLabel);

                    const parent = input.parentElement;
                    if (parent) {
                        const possibleLabel = parent.querySelector("label");
                        if (possibleLabel) return textOf(possibleLabel);
                    }

                    return "";
                };

                const inputs = limit(
                    Array.from(document.querySelectorAll("input, textarea, select"))
                        .filter(isVisible)
                        .map((element) => ({
                            name: element.getAttribute("name"),
                            id: element.getAttribute("id"),
                            type: element.getAttribute("type"),
                            placeholder: element.getAttribute("placeholder"),
                            label: labelForInput(element),
                            value_present: Boolean(element.value),
                            selector: cssPath(element),
                            tag: element.tagName.toLowerCase()
                        }))
                );

                const tables = limit(
                    Array.from(document.querySelectorAll("table"))
                        .filter(isVisible)
                        .map((table) => {
                            const headers = Array.from(table.querySelectorAll("th"))
                                .map(textOf)
                                .filter(Boolean);

                            const rows_count = table.querySelectorAll("tbody tr").length ||
                                table.querySelectorAll("tr").length;

                            return {
                                headers,
                                rows_count,
                                selector: cssPath(table)
                            };
                        }),
                    50
                );

                const customSelectors = [
                    "[onclick]",
                    "[tabindex]",
                    "[role='menuitem']",
                    "[role='tab']",
                    "[role='option']",
                    "[aria-expanded]",
                    "fuse-vertical-navigation-item",
                    "fuse-vertical-navigation-basic-item",
                    "fuse-vertical-navigation-collapsable-item",
                    "mat-expansion-panel",
                    "mat-tab",
                    "app-menu",
                    "app-sidebar",
                    "[class*='menu']",
                    "[class*='nav']",
                    "[class*='sidebar']",
                    "[class*='collapse']",
                    "[class*='accordion']"
                ].join(",");

                const custom_interactives = limit(
                    Array.from(document.querySelectorAll(customSelectors))
                        .filter(isVisible)
                        .map((element) => ({
                            text: textOf(element),
                            tag: element.tagName.toLowerCase(),
                            role: element.getAttribute("role"),
                            aria_expanded: element.getAttribute("aria-expanded"),
                            onclick: Boolean(element.getAttribute("onclick")),
                            selector: cssPath(element)
                        }))
                        .filter((item) => item.text || item.onclick || item.aria_expanded !== null)
                );

                return {
                    url: window.location.href,
                    path: window.location.pathname,
                    title: document.title || "",
                    visible_text: document.body ? normalizeText(document.body.innerText || "") : "",
                    links,
                    buttons,
                    inputs,
                    tables,
                    custom_interactives
                };
            }
            """
        )

        visible_text = data.get("visible_text") or ""

        data["visible_text"] = visible_text[: self.max_visible_text_chars]
        data["visible_text_truncated"] = len(visible_text) > self.max_visible_text_chars

        return data