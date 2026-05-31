from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from src.browser.navigator import ERPNavigator
from src.config.profile_loader import ProfileLoader
from src.crawler.route_crawler import RouteCrawler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ejecuta el descubrimiento estructural de un ERP."
    )

    parser.add_argument(
        "--profile",
        default="configs/cbmm.yaml",
        help="Ruta del archivo YAML de perfil del ERP.",
    )

    parser.add_argument(
        "--headless",
        action="store_true",
        help="Ejecuta el navegador en modo oculto.",
    )

    parser.add_argument(
        "--slow-mo",
        type=int,
        default=None,
        help="Tiempo en milisegundos entre acciones de Playwright.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    profile_path = Path(args.profile)

    print("=" * 80)
    print("ASISTENTE ERP - DESCUBRIMIENTO ESTRUCTURAL")
    print("=" * 80)
    print(f"Perfil YAML: {profile_path}")

    profile = ProfileLoader(profile_path).load()

    browser_config = profile.get("browser", {})
    viewport = browser_config.get(
        "viewport",
        {
            "width": 1366,
            "height": 768,
        },
    )

    headless = args.headless or browser_config.get("headless", False)
    slow_mo = args.slow_mo
    if slow_mo is None:
        slow_mo = browser_config.get("slow_mo", 0)

    print(f"ERP: {profile['erp']['name']}")
    print(f"Base URL: {profile['erp']['base_url']}")
    print(f"Headless: {headless}")
    print(f"Slow motion: {slow_mo} ms")
    print("-" * 80)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=headless,
            slow_mo=slow_mo,
        )

        context = browser.new_context(
            viewport=viewport,
            ignore_https_errors=True,
        )

        page = context.new_page()

        try:
            print("1. Iniciando login...")
            navigator = ERPNavigator(page, profile)
            navigator.login()
            print("   Login confirmado.")
            print(f"   URL actual: {navigator.current_url()}")

            print("-" * 80)
            print("2. Iniciando crawler estructural...")
            crawler = RouteCrawler(page, profile)
            summary = crawler.crawl()

            print("-" * 80)
            print("3. Descubrimiento finalizado.")
            print(f"   Pantallas visitadas: {summary.visited_count}")
            print(f"   Rutas pendientes restantes: {summary.pending_count}")
            print(f"   Nodos estructurales: {summary.nodes_count}")
            print(f"   Relaciones estructurales: {summary.edges_count}")
            print(f"   Grafo guardado en: {summary.routes_graph_path}")
            print(f"   Índice guardado en: {summary.screen_index_path}")

            print("=" * 80)
            print("PROCESO COMPLETADO CORRECTAMENTE")
            print("=" * 80)

            return 0

        except KeyboardInterrupt:
            print("\nProceso interrumpido por el usuario.")
            return 130

        except Exception as error:
            print("=" * 80)
            print("ERROR DURANTE EL DESCUBRIMIENTO")
            print("=" * 80)
            print(str(error))
            return 1

        finally:
            try:
                context.close()
            except KeyboardInterrupt:
                pass
            except Exception:
                pass

            try:
                browser.close()
            except KeyboardInterrupt:
                pass
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())