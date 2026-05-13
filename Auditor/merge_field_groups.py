#!/usr/bin/env python3
"""
merge_field_groups.py
=====================
Merges field_verdict_distribution.csv from BOTH the products and
without-products analysis reports into a single deduplicated CSV with:

    Source | Field Group | Path

Rules:
  - Without-products rows: field_group = "section / dotted.path"
      → Field Group = part before " / "
      → Path        = part after  " / "
  - Products rows: field_group has no " / " separator
      → Field Group = entire field_group value
      → Path        = (empty)

Usage:
  # Auto-detect latest analysis report dirs inside audit_outputs/
  python merge_field_groups.py

  # Or supply explicit paths
  python merge_field_groups.py \\
    --prod     "audit_outputs/audit_outputs_20260507_111414/analysis_report/field_verdict_distribution.csv" \\
    --nonprod  "audit_outputs/without_products_audit_outputs_20260506_144150/without_products_analysis_report/field_verdict_distribution.csv" \\
    --output   "merged_field_groups.csv"
"""

import argparse
import csv
from pathlib import Path


AUDIT_BASE = Path(__file__).parent / "audit_outputs"
SEPARATOR = " / "


# ── Auto-detection helpers ────────────────────────────────────────────────────

def find_latest(pattern: str) -> Path | None:
    candidates = sorted(AUDIT_BASE.glob(pattern), reverse=True)
    return candidates[0] if candidates else None


def auto_detect_prod_csv() -> Path | None:
    # audit_outputs_<ts>/analysis_report/field_verdict_distribution.csv
    return find_latest("audit_outputs_*/analysis_report/field_verdict_distribution.csv")


def auto_detect_nonprod_csv() -> Path | None:
    # without_products_audit_outputs_<ts>/without_products_analysis_report/field_verdict_distribution.csv
    return find_latest(
        "without_products_audit_outputs_*/without_products_analysis_report/field_verdict_distribution.csv"
    )


# ── Core extraction ───────────────────────────────────────────────────────────

def extract_rows(csv_path: Path, source_label: str, has_separator: bool) -> list[tuple[str, str, str]]:
    """Read a field_verdict_distribution.csv and return deduplicated (source, group, path) tuples."""
    seen: set[tuple[str, str, str]] = set()
    rows: list[tuple[str, str, str]] = []

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Space-padded CSVs have keys like "field_group   " — normalize all keys
            stripped_row = {k.strip(): v.strip() for k, v in row.items() if k}
            raw = stripped_row.get("field_group", "")
            if not raw:
                continue

            if has_separator and SEPARATOR in raw:
                left, right = raw.split(SEPARATOR, 1)
                group = left.strip()
                path = right.strip()
            else:
                group = raw
                path = ""

            key = (source_label, group, path)
            if key not in seen:
                seen.add(key)
                rows.append(key)

    return rows


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Merge field group lists from both audit analysis reports.")
    parser.add_argument("--prod",    help="Path to products field_verdict_distribution.csv")
    parser.add_argument("--nonprod", help="Path to without-products field_verdict_distribution.csv")
    parser.add_argument(
        "--output",
        default=str(AUDIT_BASE.parent / "merged_field_groups.csv"),
        help="Output CSV path (default: Auditor/merged_field_groups.csv)",
    )
    args = parser.parse_args()

    # ── Resolve input paths ────────────────────────────────────────────────────
    prod_path = Path(args.prod) if args.prod else auto_detect_prod_csv()
    nonprod_path = Path(args.nonprod) if args.nonprod else auto_detect_nonprod_csv()

    if prod_path is None:
        print("ERROR: Could not find products field_verdict_distribution.csv. Use --prod to supply it.")
        raise SystemExit(1)
    if nonprod_path is None:
        print("ERROR: Could not find without-products field_verdict_distribution.csv. Use --nonprod to supply it.")
        raise SystemExit(1)

    print(f"Products CSV   : {prod_path}")
    print(f"Non-products CSV: {nonprod_path}")

    # Products → no " / " separator
    prod_rows = extract_rows(prod_path, source_label="prod", has_separator=False)

    # Without-products → has " / " separator
    nonprod_rows = extract_rows(nonprod_path, source_label="non prod", has_separator=True)

    # Merge: non-prod first, then prod (matches the display order requested)
    all_rows = nonprod_rows + prod_rows

    # ── Write output ──────────────────────────────────────────────────────────
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Source", "Field Group", "Path"])
        for source, group, path in all_rows:
            writer.writerow([source, group, path])

    print(f"\nRows written : {len(all_rows)}  ({len(nonprod_rows)} non-prod + {len(prod_rows)} prod)")
    print(f"Output       : {out_path}")


if __name__ == "__main__":
    main()
