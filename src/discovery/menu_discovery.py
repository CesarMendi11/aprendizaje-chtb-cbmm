from playwright.sync_api import Page


class MenuDiscovery:
    def __init__(self, page: Page, profile: dict):
        self.page = page
        self.profile = profile

    def extract_menu_items(self) -> list[dict]:
        menu_selector = self.profile["navigation"].get("menu_container_selector")

        if not menu_selector:
            return []

        return self.page.evaluate(
            """
            (menuSelector) => {
                const menu = document.querySelector(menuSelector);

                if (!menu) {
                    return [];
                }

                const nodes = Array.from(
                    menu.querySelectorAll("a[href], button, [role='button'], [role='menuitem'], fuse-vertical-navigation-basic-item, fuse-vertical-navigation-collapsable-item")
                );

                return nodes.map((el, index) => {
                    const rect = el.getBoundingClientRect();

                    return {
                        index,
                        tag: el.tagName.toLowerCase(),
                        text: (el.innerText || el.textContent || "").trim(),
                        href: el.getAttribute("href"),
                        role: el.getAttribute("role"),
                        aria_label: el.getAttribute("aria-label"),
                        id: el.id || null,
                        classes: typeof el.className === "string" ? el.className : "",
                        visible: !!(rect.width && rect.height),
                        position: {
                            x: rect.x,
                            y: rect.y,
                            width: rect.width,
                            height: rect.height
                        }
                    };
                }).filter(item =>
                    item.visible &&
                    (
                        item.text ||
                        item.href ||
                        item.aria_label ||
                        item.id
                    )
                );
            }
            """,
            menu_selector,
        )