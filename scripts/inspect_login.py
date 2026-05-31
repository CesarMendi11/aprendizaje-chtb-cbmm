from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

from src.config.profile_loader import ProfileLoader


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspecciona la pantalla de login del ERP."
    )

    parser.add_argument(
        "--profile",
        default="configs/cbmm.yaml",
        help="Ruta del perfil YAML.",
    )

    parser.add_argument(
        "--slow-mo",
        type=int,
        default=200,
        help="Milisegundos entre acciones de Playwright.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    profile = ProfileLoader(args.profile).load()

    base_url = profile["erp"]["base_url"].rstrip("/")
    login_path = profile["login"]["url"].lstrip("/")
    login_url = urljoin(base_url + "/", login_path)

    cache_dir = Path("data/cache")
    cache_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 80)
    print("INSPECCIONANDO LOGIN")
    print("=" * 80)
    print(f"URL configurada: {login_url}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=False,
            slow_mo=args.slow_mo,
        )

        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            ignore_https_errors=True,
        )

        page = context.new_page()

        try:
            page.goto(login_url, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

            print("-" * 80)
            print(f"URL actual: {page.url}")
            print(f"Título: {page.title()}")

            html_path = cache_dir / "login_debug.html"
            screenshot_path = cache_dir / "login_debug.png"

            html_path.write_text(page.content(), encoding="utf-8")
            page.screenshot(path=str(screenshot_path), full_page=True)

            print("-" * 80)
            print(f"HTML guardado en: {html_path}")
            print(f"Screenshot guardado en: {screenshot_path}")

            data = page.evaluate(
                """
                () => {
                    const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return (
                            style.display !== "none" &&
                            style.visibility !== "hidden" &&
                            rect.width > 0 &&
                            rect.height > 0
                        );
                    };

                    const text = (el) => {
                        return (el.innerText || el.textContent || el.value || "")
                            .replace(/\\s+/g, " ")
                            .trim();
                    };

                    const attrs = (el) => ({
                        tag: el.tagName.toLowerCase(),
                        type: el.getAttribute("type"),
                        name: el.getAttribute("name"),
                        id: el.getAttribute("id"),
                        placeholder: el.getAttribute("placeholder"),
                        class: el.getAttribute("class"),
                        role: el.getAttribute("role"),
                        aria_label: el.getAttribute("aria-label"),
                        text: text(el)
                    });

                    return {
                        inputs: Array.from(document.querySelectorAll("input, textarea, select"))
                            .filter(visible)
                            .map(attrs),

                        buttons: Array.from(document.querySelectorAll("button, [role='button'], input[type='submit'], input[type='button']"))
                            .filter(visible)
                            .map(attrs),

                        links: Array.from(document.querySelectorAll("a"))
                            .filter(visible)
                            .map(attrs),

                        iframes: Array.from(document.querySelectorAll("iframe"))
                            .map((el) => ({
                                src: el.getAttribute("src"),
                                id: el.getAttribute("id"),
                                name: el.getAttribute("name"),
                                class: el.getAttribute("class")
                            }))
                    };
                }
                """
            )

            print("-" * 80)
            print("INPUTS VISIBLES:")
            for index, item in enumerate(data["inputs"], start=1):
                print(f"{index}. {item}")

            print("-" * 80)
            print("BOTONES VISIBLES:")
            for index, item in enumerate(data["buttons"], start=1):
                print(f"{index}. {item}")

            print("-" * 80)
            print("LINKS VISIBLES:")
            for index, item in enumerate(data["links"], start=1):
                print(f"{index}. {item}")

            print("-" * 80)
            print("IFRAMES:")
            for index, item in enumerate(data["iframes"], start=1):
                print(f"{index}. {item}")

            print("-" * 80)
            print("Cierra la ventana del navegador cuando termines de revisar.")
            page.wait_for_timeout(15000)

            return 0

        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())