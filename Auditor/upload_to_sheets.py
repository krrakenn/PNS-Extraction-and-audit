"""
upload_to_sheets.py
Appends today's overall_verdict_distribution to the Google Sheet.

Layout per tab (Prod / Non-Prod):
  Col A       | Col B        | Col C | Col D
  ----------- | ------------ | ----- | ----------
  2026-05-14  | MATCH        | 272   | 51.91
  (blank)     | HALLUCINATED | 172   | 32.82
  (blank)     | MISSING      | 37    | 7.06
  (blank)     | PARTIAL      | 23    | 4.39
  (blank)     | MISMATCH     | 20    | 3.82
  (blank row) |              |       |            <- separator
  2026-05-15  | MATCH        | ...   | ...
  ...

Each date block occupies exactly 6 rows (5 data + 1 blank separator).
All values are written in a single API call per tab (no cell-by-cell writes).
"""

import csv
import json
import os
import sys
from datetime import date
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

# ── Configuration ──────────────────────────────────────────────────────────────
SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "1d3EOdH1a2eDqWJeA9P_L8OWyXGxKww2md_pSkXhifYU")
CREDS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")

# These must match the exact tab names in your Google Sheet
PROD_TAB     = "Prod"
NON_PROD_TAB = "Non-Prod"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

ROOT          = Path(__file__).parent
AUDIT_OUTPUTS = ROOT / "Auditor" / "audit_outputs"


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_credentials() -> Credentials:
    """Load Google service account credentials from env or local file."""
    if CREDS_JSON:
        creds_dict = json.loads(CREDS_JSON)
    else:
        # Local dev fallback: place google_credentials.json in project root
        creds_path = ROOT / "google_credentials.json"
        if not creds_path.exists():
            print("ERROR: GOOGLE_CREDENTIALS_JSON env var not set and google_credentials.json not found.")
            sys.exit(1)
        with open(creds_path, encoding="utf-8") as f:
            creds_dict = json.load(f)
    return Credentials.from_service_account_info(creds_dict, scopes=SCOPES)


def find_latest_verdict_csv(glob_pattern: str, sub_path: str) -> Path | None:
    """Return the overall_verdict_distribution.csv from the most recent audit output dir."""
    dirs = sorted(AUDIT_OUTPUTS.glob(glob_pattern), reverse=True)
    if not dirs:
        return None
    return dirs[0] / sub_path / "overall_verdict_distribution.csv"


def read_verdict_csv(path: Path) -> list[dict]:
    """
    Parse overall_verdict_distribution.csv.
    Returns list of {verdict, count, percentage} in source order.
    """
    rows = []
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        # Strip whitespace from header names
        reader.fieldnames = [h.strip() for h in (reader.fieldnames or [])]
        for row in reader:
            verdict = row.get("verdict", "").strip()
            if not verdict:
                continue
            rows.append({
                "verdict":    verdict,
                "count":      row.get("count", "").strip(),
                "percentage": row.get("percentage", "").strip(),
            })
    return rows


def get_next_start_row(worksheet: gspread.Worksheet) -> int:
    """
    Scan column A to find where the next date block should start.
    Returns a 1-based row index.
    """
    col_a = worksheet.col_values(1)  # single API read
    # Find last non-empty cell in column A
    last_non_empty = 0
    for i, val in enumerate(col_a):
        if val.strip():
            last_non_empty = i + 1  # 1-based

    if last_non_empty == 0:
        return 1  # sheet is empty, start at row 1

    # Each block is 5 data rows + 1 blank = 6 rows.
    # The blank separator row is at last_non_empty + 5 (0-indexed from block start).
    # We just need the row AFTER the last block ends.
    # Safe approach: scan all values to find last row with any content,
    # then add 2 (1 for blank separator + 1 for next block start).
    all_vals = worksheet.get_all_values()
    last_row_with_data = 0
    for i, row in enumerate(all_vals):
        if any(cell.strip() for cell in row):
            last_row_with_data = i + 1

    # Leave one blank separator row after the last data row
    return last_row_with_data + 2


def upload_to_tab(worksheet: gspread.Worksheet, verdict_rows: list[dict], run_date: str) -> None:
    """
    Write one date block (5 data rows + 1 blank) to the worksheet
    in a SINGLE API call using worksheet.update().
    """
    start_row = get_next_start_row(worksheet)

    # Build the 2D value array
    values: list[list] = []
    for i, row in enumerate(verdict_rows):
        date_col = run_date if i == 0 else ""  # date only in first row of block
        values.append([date_col, row["verdict"], row["count"], row["percentage"]])
    values.append(["", "", "", ""])  # blank separator row

    end_row  = start_row + len(values) - 1
    cell_range = f"A{start_row}:D{end_row}"

    # Single write — no cell-by-cell iteration
    worksheet.update(cell_range, values, value_input_option="RAW")
    print(f"  ✓ [{worksheet.title}] wrote {len(verdict_rows)} verdicts at rows {start_row}–{start_row + len(verdict_rows) - 1}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    run_date = str(date.today())  # e.g. "2026-05-14"
    print(f"Uploading audit results for {run_date}...\n")

    # ── Locate CSVs ───────────────────────────────────────────────────────────
    prod_csv = find_latest_verdict_csv(
        "products_audit_outputs_*",
        "analysis_report",
    )
    non_prod_csv = find_latest_verdict_csv(
        "without_products_audit_outputs_*",
        "without_products_analysis_report",
    )

    if not prod_csv or not prod_csv.exists():
        print("ERROR: Could not find Prod verdict CSV.\n"
              "Expected: audit_outputs/products_audit_outputs_*/analysis_report/overall_verdict_distribution.csv")
        sys.exit(1)

    if not non_prod_csv or not non_prod_csv.exists():
        print("ERROR: Could not find Non-Prod verdict CSV.\n"
              "Expected: audit_outputs/without_products_audit_outputs_*/without_products_analysis_report/overall_verdict_distribution.csv")
        sys.exit(1)

    print(f"Prod CSV     : {prod_csv}")
    print(f"Non-Prod CSV : {non_prod_csv}\n")

    prod_verdicts     = read_verdict_csv(prod_csv)
    non_prod_verdicts = read_verdict_csv(non_prod_csv)

    # ── Connect to Google Sheets ──────────────────────────────────────────────
    creds = get_credentials()
    gc    = gspread.authorize(creds)
    spreadsheet = gc.open_by_key(SHEET_ID)

    prod_ws     = spreadsheet.worksheet(PROD_TAB)
    non_prod_ws = spreadsheet.worksheet(NON_PROD_TAB)

    # ── Upload (one API call per tab) ─────────────────────────────────────────
    upload_to_tab(prod_ws,     prod_verdicts,     run_date)
    upload_to_tab(non_prod_ws, non_prod_verdicts, run_date)

    print("\n✅ Google Sheets upload complete!")


if __name__ == "__main__":
    main()
