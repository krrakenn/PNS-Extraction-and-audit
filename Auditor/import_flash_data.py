import csv
import json
import ast
import argparse
import sys
from pathlib import Path

def normalize_json(raw_json: str) -> str:
    """Transforms Flash JSON to match the new Pro schema hierarchy and key order."""
    try:
        # 1. Parse (handle standard JSON and Python dict strings)
        try:
            data = json.loads(raw_json)
        except:
            data = ast.literal_eval(raw_json)
        
        if not isinstance(data, dict):
            return raw_json

        # 2. Map old flat structure to new nested metadata structure
        if "metadata" not in data or not isinstance(data["metadata"], dict):
            data["metadata"] = {}
        
        # Move specific fields to metadata if they are outside
        for key in ["buyer_intent", "buyer_conclusion", "call_type", "additional_details", "intended_application", "primary_language", "all_languages", "call_purpose"]:
            if key in data and key not in data["metadata"]:
                data["metadata"][key] = data.pop(key)

        # 3. Define the "Master" key order (to match Pro)
        master_order = [
            "buyer_details", "seller_details", "products", "minimum_order_quantity",
            "lead_tag", "payment", "next_steps", "metadata"
        ]
        
        # Recursive sort function for deterministic comparison
        def sort_dict(d, keys_priority=[]):
            sorted_d = {}
            # First, add prioritized keys in order
            for k in keys_priority:
                if k in d:
                    val = d[k]
                    if isinstance(val, dict):
                        sorted_d[k] = sort_dict(val)
                    elif isinstance(val, list):
                        sorted_d[k] = [sort_dict(i) if isinstance(i, dict) else i for i in val]
                    else:
                        sorted_d[k] = val
            # Then add remaining keys alphabetically
            for k in sorted(d.keys()):
                if k not in sorted_d:
                    val = d[k]
                    if isinstance(val, dict):
                        sorted_d[k] = sort_dict(val)
                    elif isinstance(val, list):
                        sorted_d[k] = [sort_dict(i) if isinstance(i, dict) else i for i in val]
                    else:
                        sorted_d[k] = val
            return sorted_d

        normalized_data = sort_dict(data, master_order)
        return json.dumps(normalized_data, ensure_ascii=False)
    except:
        return raw_json

def main():
    parser = argparse.ArgumentParser(description="Import and normalize Redash JSON data for PNS Auditor.")
    parser.add_argument("--source-csv", required=True, help="Path to the Redash CSV (daily_input.csv)")
    parser.add_argument("--output-csv", required=True, help="Path to output standardized CSV")
    args = parser.parse_args()

    source_path = Path(args.source_csv)
    if not source_path.exists():
        print(f"Error: Source CSV not found at {source_path}")
        sys.exit(1)

    standardized_rows = []
    
    with open(source_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            # Normalize keys to be case insensitive
            row_keys = {k.lower(): k for k in row.keys() if k}
            
            # Extract fields safely
            file_id = row.get(row_keys.get("file_id", ""), "")
            llm_json = row.get(row_keys.get("llm_extracted_json", ""), "")
            file_url = row.get(row_keys.get("file_url", ""), row.get(row_keys.get("recording_url", ""), ""))
            seller_glid = row.get(row_keys.get("seller_glid", ""), row.get(row_keys.get("receiver_glid", ""), ""))
            buyer_glid = row.get(row_keys.get("buyer_glid", ""), row.get(row_keys.get("sender_glid", ""), ""))
            
            if not file_id:
                continue
                
            # Need to reference the normalize_json function which is still in the file
            clean_json = normalize_json(llm_json) if llm_json else "{}"
            
            standardized_rows.append({
                "seller id": seller_glid,
                "Buyer id": buyer_glid,
                "recording_url": file_url,
                "file id": file_id,
                "user details": "{}",
                "mcat_id": "",
                "mcat_name": "",
                "categories": "[]",
                "Thinking JSON": clean_json,
                "Thinking summary": "Imported and Normalized Flash Data from Redash",
                "Thinking Input Tokens": "0",
                "Thinking Output Tokens": "0"
            })

    if not standardized_rows:
        print("Error: No valid rows imported.")
        sys.exit(1)

    output_path = Path(args.output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(args.output_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=standardized_rows[0].keys())
        writer.writeheader()
        for row in standardized_rows:
            writer.writerow(row)

    print(f"✓ Successfully imported and normalized {len(standardized_rows)} records from Redash CSV.")
    print(f"  -> {args.output_csv}")

if __name__ == '__main__':
    main()
