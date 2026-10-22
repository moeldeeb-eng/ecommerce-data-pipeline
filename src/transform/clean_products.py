from pathlib import Path
from decimal import Decimal
import re
import argparse
import json

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]

OUTPUT_DIR = PROJECT_ROOT / "data/processed"
REJECTED_DIR = PROJECT_ROOT / "data/rejected"


def parse_price(value):
    if pd.isna(value) or not str(value).strip():
        return None, None, "missing"

    text = " ".join(str(value).split())

    # الصيغة الموجودة في العينة: EGP 1,617.66
    match = re.fullmatch(
        r"([A-Z]{3})\s+(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?",
        text,
    )

    if not match:
        return None, None, "invalid"

    currency, whole, fraction = match.groups()
    price = Decimal(
        whole.replace(",", "") + "." + (fraction or "00")
    )

    if price <= 0:
        return None, currency, "invalid"

    return format(price, ".2f"), currency, "valid"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch-id', required=True)
    args = parser.parse_args()
    batch_id = args.batch_id
    if not re.fullmatch(r"[A-Za-z0-9_-]+", batch_id):
        raise ValueError("Invalid batch ID")
    batch_dir = PROJECT_ROOT / "data/raw/amazon_batches" / batch_id
    manifest = json.loads((batch_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "complete" or manifest.get("batch_id") != batch_id:
        raise ValueError("Only completed, matching batches may be cleaned")
    df = pd.read_csv(
        batch_dir / "products.csv",
        dtype="string",
        keep_default_na=False,
    )

    required = {
        "asin", "product_name", "price_raw",
        "rating", "ratings_count", "product_url", "source_file",
        "parsed_at_utc", "collected_at_utc", "source_url", "page_number", "batch_id",
    }
    missing_columns = required - set(df.columns)

    if missing_columns:
        raise ValueError(f"Missing columns: {sorted(missing_columns)}")

    input_count = len(df)
    if input_count == 0:
        raise ValueError("Empty batch")
    if not df["batch_id"].eq(batch_id).all():
        raise ValueError("CSV contains a different batch ID")
    for name in ("collected_at_utc", "parsed_at_utc"):
        parsed = pd.to_datetime(df[name], utc=True, errors="coerce", format="ISO8601")
        aware = df[name].str.contains(r"(?:Z|[+-]\d{2}:\d{2})$", regex=True)
        if parsed.isna().any() or not aware.all():
            raise ValueError(f"Invalid or timezone-naive timestamp: {name}")
        df[name] = parsed.map(lambda value: value.isoformat())

    df["asin"] = df["asin"].str.strip().str.upper()
    df["product_name"] = (
        df["product_name"]
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )

    parsed_prices = pd.DataFrame(
        df["price_raw"].map(parse_price).tolist(),
        columns=["price", "currency", "price_status"],
        index=df.index,
    )
    df = pd.concat([df, parsed_prices], axis=1)

    rating = pd.to_numeric(df["rating"], errors="coerce")
    count = pd.to_numeric(df["ratings_count"], errors="coerce")

    reasons = pd.Series("", index=df.index, dtype="string")

    checks = [
        (
    df["rating"].str.strip().ne("") & ~rating.between(1, 5),
    "invalid_rating",
),
(
    df["ratings_count"].str.strip().ne("")
    & ~(count.ge(0) & count.mod(1).eq(0)),
    "invalid_ratings_count",
),
    ]

    for invalid, reason in checks:
        invalid = invalid.fillna(True)
        reasons.loc[invalid] = reasons.loc[invalid] + reason + ";"

    rejected = df.loc[reasons.ne("")].copy()
    rejected["rejection_reason"] = reasons.loc[
        reasons.ne("")
    ].str.rstrip(";")

    clean = df.loc[reasons.eq("")].copy()
    clean["rating"] = rating.loc[clean.index]
    clean["ratings_count"] = count.loc[clean.index].astype("Int64")

    output_dir = OUTPUT_DIR / batch_id
    rejected_dir = REJECTED_DIR / batch_id
    output_dir.mkdir(parents=True, exist_ok=True)
    rejected_dir.mkdir(parents=True, exist_ok=True)

    clean.to_csv(
        output_dir / "products_clean.csv",
        index=False,
        encoding="utf-8-sig",
    )
    rejected.to_csv(
        rejected_dir / "products_rejected.csv",
        index=False,
        encoding="utf-8-sig",
    )

    assert input_count == len(clean) + len(rejected)

    summary = {
        "batch_id": batch_id, "input_rows": input_count,
        "clean_rows": len(clean), "rejected_rows": len(rejected),
        "missing_prices": int(clean["price_status"].eq("missing").sum()),
    }
    (output_dir / "quality_report.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Clean CSV: {output_dir / 'products_clean.csv'}")
    print(f"Input rows: {input_count}")
    print(f"Clean rows: {len(clean)}")
    print(f"Rejected rows: {len(rejected)}")
    print(f"Missing prices: {clean['price_status'].eq('missing').sum()}")
    print("\nPrice sample:")
    print(
        clean[
            ["asin", "price_raw", "price", "currency", "price_status"]
        ].head().to_string(index=False)
    )


if __name__ == "__main__":
    main()