from __future__ import annotations

from typing import Any

from playwright.sync_api import Page

from src.extraction.screen_title_resolver import ScreenTitleResolver


class ScreenExtractor:
    """Extrae estructura observable sin decidir navegación ni negocio."""

    DEFAULT_REGION_SELECTORS = {
        "global_navigation": [
            "fuse-vertical-navigation",
            "[role='navigation']",
            "nav",
            "aside",
            "app-sidebar",
            ".sidebar",
            ".sidenav",
            ".side-nav",
            ".main-navigation",
        ],
        "header": [
            "header",
            "[role='banner']",
            "mat-toolbar",
            ".app-header",
            ".topbar",
        ],
        "main_content": [
            "main",
            "[role='main']",
            "app-home",
            "router-outlet + *",
        ],
        "footer": ["footer", "[role='contentinfo']", ".app-footer"],
        "dialog": [
            "dialog",
            "[role='dialog']",
            "[role='alertdialog']",
            "mat-dialog-container",
            ".modal.show",
        ],
        "volatile": [],
    }

    def __init__(self, page: Page, profile: dict[str, Any]):
        self.page = page
        self.profile = profile

        extraction_config = profile.get("extraction", {})
        self.max_visible_text_chars = int(
            extraction_config.get("max_visible_text_chars", 8000)
        )
        self.max_region_text_chars = int(
            extraction_config.get("max_region_text_chars", 5000)
        )
        self.region_selectors = self._build_region_selectors(extraction_config)
        self.title_resolver = ScreenTitleResolver(profile)

    def extract(self, title_hint: str | None = None) -> dict[str, Any]:
        data = self.page.evaluate(
            self._evaluation_script(),
            {
                "regionSelectors": self.region_selectors,
                "maxRegionTextChars": self.max_region_text_chars,
            },
        )

        visible_text = data.get("visible_text") or ""
        data["visible_text"] = visible_text[: self.max_visible_text_chars]
        data["visible_text_truncated"] = (
            len(visible_text) > self.max_visible_text_chars
        )

        resolved = self.title_resolver.resolve(data, title_hint=title_hint)
        data["functional_title"] = resolved.title
        data["title_source"] = resolved.source
        data["title_confidence"] = resolved.confidence

        data["main_visible_text"] = (
            data.get("regions", {})
            .get("main_content", {})
            .get("visible_text", "")
        )
        data["global_links"] = self._items_in_region(
            data.get("links", []), "global_navigation"
        )
        data["local_links"] = self._local_items(data.get("links", []))
        data["global_interactives"] = self._items_in_region(
            data.get("custom_interactives", []), "global_navigation"
        )
        data["local_interactives"] = self._local_items(
            data.get("custom_interactives", [])
        )

        return data

    def _build_region_selectors(
        self,
        extraction_config: dict[str, Any],
    ) -> dict[str, list[str]]:
        configured = extraction_config.get("regions", {})
        result: dict[str, list[str]] = {}
        for region, defaults in self.DEFAULT_REGION_SELECTORS.items():
            values = configured.get(region, defaults)
            if values is None:
                values = []
            result[region] = [
                str(value).strip()
                for value in values
                if str(value).strip()
            ]
        return result

    def _items_in_region(
        self,
        items: list[dict[str, Any]],
        region: str,
    ) -> list[dict[str, Any]]:
        return [item for item in items if item.get("region") == region]

    def _local_items(
        self,
        items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            item
            for item in items
            if item.get("region")
            not in {"global_navigation", "header", "footer", "volatile"}
        ]

    def _evaluation_script(self) -> str:
        return r"""
        (config) => {
            const regionSelectors = config.regionSelectors || {};
            const maxRegionTextChars = config.maxRegionTextChars || 5000;

            const isVisible = (element) => {
                if (!element) return false;
                const style = window.getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return Boolean(
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
                return String(value).replace(/\s+/g, " ").trim();
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
                            : current.id.replace(/([^a-zA-Z0-9_-])/g, "\\$1");
                        selector += "#" + escapedId;
                        parts.unshift(selector);
                        break;
                    }
                    const parent = current.parentElement;
                    if (parent) {
                        const siblings = Array.from(parent.children)
                            .filter((child) => child.tagName === current.tagName);
                        if (siblings.length > 1) {
                            selector += `:nth-of-type(${siblings.indexOf(current) + 1})`;
                        }
                    }
                    parts.unshift(selector);
                    current = current.parentElement;
                }
                return parts.join(" > ");
            };

            const safeClosest = (element, selectors) => {
                for (const selector of selectors || []) {
                    try {
                        const match = element.closest(selector);
                        if (match) return match;
                    } catch (_) {
                        // Un selector de perfil inválido no debe romper el crawler.
                    }
                }
                return null;
            };

            const regionOf = (element) => {
                if (safeClosest(element, regionSelectors.volatile)) return "volatile";
                if (safeClosest(element, regionSelectors.dialog)) return "dialog";
                if (safeClosest(element, regionSelectors.global_navigation)) {
                    return "global_navigation";
                }
                if (safeClosest(element, regionSelectors.header)) return "header";
                if (safeClosest(element, regionSelectors.footer)) return "footer";
                if (safeClosest(element, regionSelectors.main_content)) {
                    return "main_content";
                }
                return "main_content";
            };

            const visibleRoots = (selectors) => {
                const roots = [];
                for (const selector of selectors || []) {
                    try {
                        for (const element of document.querySelectorAll(selector)) {
                            if (isVisible(element)) roots.push(element);
                        }
                    } catch (_) {}
                }
                return roots.filter((element, index, all) =>
                    !all.some((other, otherIndex) =>
                        otherIndex !== index && other.contains(element)
                    )
                );
            };

            const regionText = (region) => {
                if (region === "main_content") {
                    const roots = visibleRoots(regionSelectors.main_content);
                    if (roots.length) {
                        return normalizeText(roots.map(textOf).filter(Boolean).join(" "))
                            .slice(0, maxRegionTextChars);
                    }
                    let bodyText = document.body
                        ? normalizeText(document.body.innerText || "")
                        : "";
                    const excludedRegions = [
                        "global_navigation", "header", "footer", "volatile"
                    ];
                    for (const excludedRegion of excludedRegions) {
                        const excludedText = normalizeText(
                            visibleRoots(regionSelectors[excludedRegion] || [])
                                .map(textOf)
                                .filter(Boolean)
                                .join(" ")
                        );
                        if (excludedText) {
                            bodyText = normalizeText(bodyText.replace(excludedText, " "));
                        }
                    }
                    return bodyText.slice(0, maxRegionTextChars);
                }
                return normalizeText(
                    visibleRoots(regionSelectors[region] || [])
                        .map(textOf)
                        .filter(Boolean)
                        .join(" ")
                ).slice(0, maxRegionTextChars);
            };

            const limit = (items, max = 300) => items.slice(0, max);
            const baseItem = (element) => {
                const form = element.closest ? element.closest("form") : null;
                return {
                    selector: cssPath(element),
                    tag: element.tagName.toLowerCase(),
                    region: regionOf(element),
                    within_table: Boolean(element.closest && element.closest("table")),
                    within_form: Boolean(form),
                    form_method: form
                        ? normalizeText(form.getAttribute("method") || "get").toLowerCase()
                        : null,
                    form_action: form
                        ? form.getAttribute("action") || window.location.pathname
                        : null,
                };
            };

            const links = limit(
                Array.from(document.querySelectorAll("a[href]"))
                    .filter(isVisible)
                    .map((element) => ({
                        ...baseItem(element),
                        text: textOf(element),
                        href: element.getAttribute("href"),
                        absolute_href: element.href,
                    }))
            );

            const buttons = limit(
                Array.from(document.querySelectorAll(
                    "button, [role='button'], input[type='button'], input[type='submit']"
                ))
                    .filter(isVisible)
                    .map((element) => ({
                        ...baseItem(element),
                        text: textOf(element),
                        type: element.getAttribute("type"),
                        role: element.getAttribute("role"),
                        aria_label: element.getAttribute("aria-label"),
                        aria_expanded: element.getAttribute("aria-expanded"),
                        aria_selected: element.getAttribute("aria-selected"),
                        aria_controls: element.getAttribute("aria-controls"),
                        title: element.getAttribute("title"),
                        disabled: Boolean(
                            element.disabled || element.getAttribute("aria-disabled") === "true"
                        ),
                    }))
            );

            const labelForInput = (input) => {
                const id = input && input.getAttribute("id");
                if (id) {
                    try {
                        const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                        if (label) return textOf(label);
                    } catch (_) {}
                }
                const parentLabel = input && input.closest("label");
                if (parentLabel) return textOf(parentLabel);
                const parent = input && input.parentElement;
                if (parent) {
                    const label = parent.querySelector("label");
                    if (label) return textOf(label);
                }
                return "";
            };

            const inputs = limit(
                Array.from(document.querySelectorAll("input, textarea, select"))
                    .filter(isVisible)
                    .map((element) => ({
                        ...baseItem(element),
                        name: element.getAttribute("name"),
                        id: element.getAttribute("id"),
                        type: element.getAttribute("type"),
                        placeholder: element.getAttribute("placeholder"),
                        label: labelForInput(element),
                        aria_label: element.getAttribute("aria-label"),
                        role: element.getAttribute("role"),
                        required: Boolean(
                            element.required || element.getAttribute("aria-required") === "true"
                        ),
                        disabled: Boolean(
                            element.disabled || element.getAttribute("aria-disabled") === "true"
                        ),
                        readonly: Boolean(element.readOnly),
                        value_present: Boolean(element.value),
                    }))
            );

            const tables = limit(
                Array.from(document.querySelectorAll("table"))
                    .filter(isVisible)
                    .map((table) => ({
                        ...baseItem(table),
                        headers: Array.from(table.querySelectorAll("th"))
                            .map(textOf)
                            .filter(Boolean),
                        rows_count: table.querySelectorAll("tbody tr").length ||
                            table.querySelectorAll("tr").length,
                    })),
                50
            );

            const customSelectors = [
                "[onclick]", "[tabindex]", "[role='menuitem']", "[role='tab']",
                "[role='option']", "[role='combobox']", "[role='listbox']",
                "[aria-expanded]", "[aria-selected]", "[aria-controls]", "select",
                "mat-select", "fuse-vertical-navigation-item",
                "fuse-vertical-navigation-basic-item",
                "fuse-vertical-navigation-collapsable-item", "mat-expansion-panel",
                "mat-tab", "app-menu", "app-sidebar", "[class*='menu']",
                "[class*='nav']", "[class*='sidebar']", "[class*='collapse']",
                "[class*='accordion']"
            ].join(",");

            const custom_interactives = limit(
                Array.from(document.querySelectorAll(customSelectors))
                    .filter(isVisible)
                    .map((element) => ({
                        ...baseItem(element),
                        text: textOf(element),
                        role: element.getAttribute("role"),
                        aria_expanded: element.getAttribute("aria-expanded"),
                        aria_selected: element.getAttribute("aria-selected"),
                        aria_controls: element.getAttribute("aria-controls"),
                        aria_hidden: element.getAttribute("aria-hidden"),
                        aria_label: element.getAttribute("aria-label"),
                        title: element.getAttribute("title"),
                        href: element.getAttribute("href"),
                        absolute_href: element.href || null,
                        type: element.getAttribute("type"),
                        disabled: Boolean(
                            element.disabled || element.getAttribute("aria-disabled") === "true"
                        ),
                        onclick: Boolean(element.getAttribute("onclick")),
                    }))
                    .filter((item) => item.text || item.onclick || item.aria_expanded !== null)
            );

            const dialogs = limit(
                Array.from(document.querySelectorAll(
                    "dialog, [role='dialog'], [role='alertdialog'], " +
                    "mat-dialog-container, .modal.show"
                ))
                    .filter(isVisible)
                    .map((element) => ({
                        ...baseItem(element),
                        title: textOf(element.querySelector(
                            "h1, h2, h3, [role='heading'], .modal-title, [mat-dialog-title]"
                        )),
                        role: element.getAttribute("role") || "dialog",
                        open: element.hasAttribute("open") || isVisible(element),
                    })),
                50
            );

            const titleCandidates = [];
            const addTitleCandidates = (selector, source, score, withinMain = false) => {
                let roots = [document];
                if (withinMain) {
                    const mainRoots = visibleRoots(regionSelectors.main_content);
                    if (mainRoots.length) roots = mainRoots;
                }
                for (const root of roots) {
                    try {
                        for (const element of root.querySelectorAll(selector)) {
                            if (!isVisible(element)) continue;
                            const text = textOf(element);
                            if (!text || text.length > 120 || text.split(/\s+/).length > 14) continue;
                            titleCandidates.push({
                                text,
                                source,
                                score,
                                selector: cssPath(element),
                                region: regionOf(element),
                            });
                        }
                    } catch (_) {}
                }
            };

            addTitleCandidates("h1", "main_heading", 100, true);
            addTitleCandidates("h2", "main_heading", 96, true);
            addTitleCandidates(
                ".page-title, .screen-title, .card-title, mat-card-title, [data-page-title]",
                "page_title",
                94,
                true
            );
            addTitleCandidates(
                "[aria-label*='breadcrumb' i] a, [aria-label*='breadcrumb' i] li, " +
                ".breadcrumb a, .breadcrumb li",
                "breadcrumb",
                90,
                false
            );
            addTitleCandidates(
                "a[aria-current='page'], [role='menuitem'][aria-current='page'], " +
                ".router-link-active, .active-route",
                "active_navigation",
                78,
                false
            );

            const regionNames = [
                "global_navigation", "header", "main_content", "footer", "dialog", "volatile"
            ];
            const regions = {};
            for (const region of regionNames) {
                regions[region] = {
                    visible_text: regionText(region),
                    elements_count: [links, buttons, inputs, tables, custom_interactives]
                        .flat()
                        .filter((item) => item.region === region).length,
                };
            }

            return {
                url: window.location.href,
                path: window.location.pathname + window.location.search,
                document_title: document.title || "",
                title: document.title || "",
                title_candidates: titleCandidates,
                visible_text: document.body
                    ? normalizeText(document.body.innerText || "")
                    : "",
                regions,
                links,
                buttons,
                inputs,
                tables,
                custom_interactives,
                dialogs,
            };
        }
        """
