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
    parser = argparse.ArgumentParser(description="Import provided Flash data and join with source CSV.")
    parser.add_argument("--flash-json-csv", required=True, help="Path to CSV with 'file id' and 'llm extracted json'")
    parser.add_argument("--source-csv", required=True, help="Path to original Kibana/Source CSV (with RECEIVER_GLID, etc.)")
    parser.add_argument("--output-csv", required=True, help="Where to save the standardized flash output.csv")
    args = parser.parse_args()

    # 1. Load source data (indexed by FILE_ID)
    source_data = {}
    print(f"Reading source metadata from: {args.source_csv}")
    with open(args.source_csv, encoding="utf-8-sig") as f: # Use utf-8-sig for potential BOM
        reader = csv.DictReader(f)
        headers = [h.strip() for h in (reader.fieldnames or [])]
        print(f"Source headers found: {headers}")
        for row in reader:
            # Try various case versions of FILE_ID
            fid = None
            for k in row.keys():
                if k and k.strip().upper() == "FILE_ID":
                    fid = row[k]
                    break
            
            if fid:
                source_data[str(fid).strip()] = row
    
    print(f"Loaded {len(source_data)} rows of metadata. Sample IDs: {list(source_data.keys())[:5]}")

    # 2. Load and process Flash JSONs
    standardized_rows = []
    print(f"Reading Flash JSONs from: {args.flash_json_csv}")
    with open(args.flash_json_csv, encoding="utf-8-sig") as f:
        content = f.read()
        first_line = content.split('\n')[0]
        delimiter = ',' if ',' in first_line else '\t'
        print(f"Detected delimiter: {repr(delimiter)} for first line: {repr(first_line)}")
        
        f.seek(0)
        reader = csv.DictReader(f, delimiter=delimiter)
        # Clean up headers
        reader.fieldnames = [fn.strip().lower() for fn in (reader.fieldnames or [])]
        print(f"Flash headers found: {reader.fieldnames}")
        
        for i, row in enumerate(reader):
            fid = (row.get("file id") or row.get("file_id") or "").strip()
            raw_json = (
                row.get("llm extracted json") or 
                row.get("llm_extracted_json") or 
                row.get("json_output") or 
                row.get("json") or 
                ""
            )
            
            if not fid:
                continue
                
            fid_str = str(fid).strip()
            
            if i < 3:
                print(f"Checking Flash ID {repr(fid_str)}... JSON length: {len(raw_json)}")

            if not raw_json:
                if i < 3:
                    print(f"  Warning: No JSON content found for ID {repr(fid_str)}")
                continue

            # NORMALIZE the JSON to match Pro structure
            clean_json = normalize_json(raw_json)

            source_info = source_data.get(fid_str)
            if not source_info:
                if i < 5:
                    print(f"  Warning: No metadata found for ID {repr(fid_str)}")
                continue
            
            standardized_rows.append({
                "seller id": source_info.get("RECEIVER_GLID", ""),
                "Buyer id": source_info.get("SENDER_GLID", ""),
                "recording_url": source_info.get("RECORDING_URL", ""),
                "file id": fid_str,
                "user details": source_info.get("USER_DETAILS", "{}"),
                "mcat_id": "",
                "mcat_name": "",
                "categories": "[]",
                "Thinking JSON": clean_json,
                "Thinking summary": "Imported & Normalized Flash Data",
                "Thinking Input Tokens": "0",
                "Thinking Output Tokens": "0"
            })

    # 3. Write standardized output
    if not standardized_rows:
        print("Error: No valid rows imported.")
        sys.exit(1)

    # Ensure output directory exists
    output_path = Path(args.output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(args.output_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=standardized_rows[0].keys())
        writer.writeheader()
        writer.writerows(standardized_rows)

    print(f"✓ Imported {len(standardized_rows)} Flash rows to {args.output_csv}")

if __name__ == "__main__":
    main()
