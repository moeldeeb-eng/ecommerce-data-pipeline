from pathlib import Path
from datetime import datetime, timezone
import csv
import re

from bs4 import BeautifulSoup


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROBE_DIR = PROJECT_ROOT / "data/raw/source_probe"
chrome_files = list(PROBE_DIR.glob("chrome_*.html"))

if not chrome_files:
    raise FileNotFoundError("No saved Chrome HTML files found.")

INPUT_FILE = max(chrome_files, key=lambda path: path.stat().st_mtime)
OUTPUT_FILE = PROJECT_ROOT / "data/raw/products_sample.csv"


def get_text(card, selector):
    element = card.select_one(selector)
    return element.get_text(" ", strip=True) if element else None


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Missing input file: {INPUT_FILE}")

    soup = BeautifulSoup(
        INPUT_FILE.read_text(encoding="utf-8"),
        "html.parser",
    )

    cards = soup.select(
        '[data-component-type="s-search-result"][data-asin]'
    )

    rows = []
    seen = set()
    parsed_at = datetime.now(timezone.utc).isoformat()

    for card in cards:
        asin = card.get("data-asin", "").strip()

        if not asin or asin in seen:
            continue

        seen.add(asin)

        price_raw = get_text(
            card, ".a-price:not(.a-text-price) .a-offscreen"
        )
        rating_raw = get_text(card, ".a-icon-alt")

        count_element = card.select_one(
            'a[aria-label$=" ratings"]'
        )
        count_raw = (
            count_element.get("aria-label")
            if count_element else None
        )

        rating_match = re.search(
            r"([\d.]+) out of 5", rating_raw or ""
        )
        count_match = re.search(
            r"([\d,]+) ratings", count_raw or ""
        )

        rows.append({
            "asin": asin,
            "product_name": get_text(card, "h2"),
            "price_raw": price_raw,
            "rating": (
                float(rating_match.group(1))
                if rating_match else None
            ),
            "ratings_count": (
                int(count_match.group(1).replace(",", ""))
                if count_match else None
            ),
            "product_url": f"https://www.amazon.com/dp/{asin}",
            "source_file": INPUT_FILE.name,
            "parsed_at_utc": parsed_at,
        })

    if not rows:
        raise ValueError("No products found. Check the input HTML.")

    with OUTPUT_FILE.open(
        "w", newline="", encoding="utf-8-sig"
    ) as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Products extracted: {len(rows)}")
    print(f"Missing prices: {sum(not r['price_raw'] for r in rows)}")
    print(f"Saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()