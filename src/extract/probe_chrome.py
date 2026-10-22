from pathlib import Path
from datetime import datetime, timezone
import json

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "data/raw/source_probe"


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=False,
        )

        try:
            context = browser.new_context()
            page = context.new_page()

            home = page.goto(
                "https://www.amazon.com/",
                wait_until="domcontentloaded",
                timeout=45000,
            )
            print("Homepage status:", home.status if home else None)
            print("Homepage title:", page.title())

            if not home or not home.ok:
                print("Homepage failed; stopping the test.")
            else:
                page.wait_for_timeout(5000)

                response = page.goto(
                    "https://www.amazon.com/s?k=computer+mouse",
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

                asins = page.locator(selector).evaluate_all(
                    "(cards) => cards.map(c => c.getAttribute('data-asin'))"
                )

                print(json.dumps({
                    "http_status": response.status if response else None,
                    "page_title": page.title(),
                    "final_url": page.url,
                    "unique_products": len({a for a in asins if a}),
                }, indent=2, ensure_ascii=False))

            (OUTPUT / f"chrome_{stamp}.html").write_text(
                page.content(),
                encoding="utf-8",
            )
            page.screenshot(path=str(OUTPUT / f"chrome_{stamp}.png"))
            input("Press Enter to close...")

        finally:
            browser.close()


if __name__ == "__main__":
    main()