import os
import csv
import glob
from pathlib import Path
import gspread
from google.oauth2.service_account import Credentials
import json
import datetime

def get_latest_dir(parent_dir, prefix):
    """Find the most recent directory matching a prefix."""
    try:
        dirs = [d for d in Path(parent_dir).glob(f"{prefix}*") if d.is_dir()]
        if not dirs:
            return None
        return max(dirs, key=os.path.getmtime)
    except Exception as e:
        print(f"Warning: Could not find directory for {prefix} - {e}")
        return None

def get_metrics_from_csv(csv_path):
    """Parse the overall_verdict_distribution.csv to get the counts."""
    metrics = {"MATCH": 0, "HALLUCINATED": 0, "MISMATCH": 0, "PARTIAL": 0, "MISSING": 0, "Total": 0}
    if not os.path.exists(csv_path):
        print(f"Warning: CSV not found at {csv_path}")
        return metrics
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            verdict = row.get("verdict", "").strip().upper()
            count = int(row.get("count", 0))
            if verdict in metrics:
                metrics[verdict] = count
                metrics["Total"] += count
    return metrics

def append_to_sheet(sheet_id, worksheet_name, data_row, creds_dict):
    """Connect to Google Sheets and append a row."""
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    
    try:
        sheet = client.open_by_key(sheet_id)
        # Try to select the worksheet, or default to the first one
        try:
            worksheet = sheet.worksheet(worksheet_name)
        except gspread.exceptions.WorksheetNotFound:
            print(f"Worksheet '{worksheet_name}' not found. Falling back to the first sheet.")
            worksheet = sheet.sheet1

        worksheet.append_row(data_row)
        print(f"Successfully appended row to Google Sheet: {data_row}")
    except Exception as e:
        print(f"Error appending to Google Sheet: {e}")

def main():
    print("Starting Google Sheets Upload Process...")
    
    # 1. Load Credentials from Environment
    creds_json_str = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json_str:
        print("Error: GOOGLE_CREDENTIALS_JSON environment variable not set.")
        return
        
    try:
        creds_dict = json.loads(creds_json_str)
    except json.JSONDecodeError:
        print("Error: GOOGLE_CREDENTIALS_JSON is not a valid JSON string.")
        return
    
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        print("Error: GOOGLE_SHEET_ID environment variable not set.")
        return

    # 2. Find the latest reports
    base_audit_dir = Path("Auditor/audit_outputs")
    gen_audit_dir = get_latest_dir(base_audit_dir, "without_products_audit_outputs_")
    prod_audit_dir = get_latest_dir(base_audit_dir, "products_audit_outputs_")
    
    run_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 3. Process General Audit
    if gen_audit_dir:
        gen_csv = gen_audit_dir / "without_products_analysis_report" / "overall_verdict_distribution.csv"
        metrics = get_metrics_from_csv(gen_csv)
        row = [
            run_date, 
            gen_audit_dir.name,
            "General Audit",
            metrics["Total"],
            metrics["MATCH"],
            metrics["HALLUCINATED"],
            metrics["MISMATCH"],
            metrics["PARTIAL"],
            metrics["MISSING"]
        ]
        append_to_sheet(sheet_id, "Audit Results", row, creds_dict)
        
    # 4. Process Product Audit
    if prod_audit_dir:
        prod_csv = prod_audit_dir / "analysis_report" / "overall_verdict_distribution.csv"
        metrics = get_metrics_from_csv(prod_csv)
        row = [
            run_date, 
            prod_audit_dir.name,
            "Product Audit",
            metrics["Total"],
            metrics["MATCH"],
            metrics["HALLUCINATED"],
            metrics["MISMATCH"],
            metrics["PARTIAL"],
            metrics["MISSING"]
        ]
        append_to_sheet(sheet_id, "Audit Results", row, creds_dict)

if __name__ == "__main__":
    main()
