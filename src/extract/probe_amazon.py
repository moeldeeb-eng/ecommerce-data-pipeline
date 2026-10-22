from pathlib import Path
from datetime import datetime, timezone
import json

import requests
from bs4 import BeautifulSoup


# الوصول لفولدر المشروع مهما كان مكان تشغيل الملف
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "data" / "raw" / "source_probe"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    url = "https://www.amazon.com/s?k=computer+mouse"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")

    try:
        response = requests.get(url, timeout=30)
    except requests.RequestException as error:
        print(f"Connection failed: {error}")
        return

    html_path = OUTPUT_DIR / f"amazon_{timestamp}.html"
    html_path.write_text(response.text, encoding="utf-8")

    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""

    # مؤشر مبدئي فقط لوجود نتائج منتجات
    cards = soup.select(
        '[data-component-type="s-search-result"][data-asin]'
    )
    product_ids = {
        card.get("data-asin")
        for card in cards
        if card.get("data-asin")
    }

    report = {
        "requested_url": url,
        "final_url": response.url,
        "collected_at_utc": timestamp,
        "http_status": response.status_code,
        "page_title": title,
        "unique_product_ids_found": len(product_ids),
        "html_file": html_path.name,
    }

    report_path = OUTPUT_DIR / f"report_{timestamp}.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()