from pathlib import Path
from datetime import datetime, timezone
import json

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "data/raw/source_probe"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    url = "https://www.amazon.com/s?k=computer+mouse"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)

        try:
            page = browser.new_page()
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=45000,
            )

            selector = (
                '[data-component-type="s-search-result"][data-asin]'
            )

            if response and response.ok:
                try:
                    page.locator(selector).first.wait_for(
                        state="attached",
                        timeout=15000,
                    )
                except PlaywrightTimeoutError:
                    pass

            html_path = OUTPUT_DIR / f"browser_{timestamp}.html"
            html_path.write_text(page.content(), encoding="utf-8")

            asins = page.locator(selector).evaluate_all(
                "(cards) => cards.map(card => card.getAttribute('data-asin'))"
            )

            report = {
                "requested_url": url,
                "final_url": page.url,
                "http_status": response.status if response else None,
                "page_title": page.title(),
                "unique_product_ids_found": len(
                    {asin for asin in asins if asin}
                ),
                "html_file": html_path.name,
            }

            report_path = OUTPUT_DIR / f"browser_report_{timestamp}.json"
            report_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            print(json.dumps(report, indent=2, ensure_ascii=False))
            input("\nPress Enter to close the browser...")

        finally:
            browser.close()


if __name__ == "__main__":
    main()