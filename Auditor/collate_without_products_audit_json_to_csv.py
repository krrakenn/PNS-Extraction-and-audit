import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path


def serialize_value(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True)
    return value


def copy_dynamic_fields(target, payload, skip_keys):
    for key, value in payload.items():
        if key in skip_keys:
            continue
        target[key] = serialize_value(value)


def is_field_check_list(items):
    if not items:
        return True
    first = items[0]
    return isinstance(first, dict) and (
        "field_path" in first or "verdict" in first
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Collate without-products audit JSON (*_audit.json) into one CSV, "
            "plus optional usage_metadata collated CSV."
        )
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        default="without_products_audit_outputs_20260507_132902",
        help="Directory containing unit subfolders with *_audit.json files.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output checks CSV path. Defaults to <input_dir>/audit_checks_collated.csv",
    )
    parser.add_argument(
        "--usage-output",
        default=None,
        help=(
            "Output usage CSV path. Defaults to <input_dir>/usage_metadata_collated.csv; "
            "skipped if no usage_metadata.json files exist."
        ),
    )
    parser.add_argument(
        "--no-usage",
        action="store_true",
        help="Do not write usage_metadata_collated.csv.",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help=(
            "After writing checks CSV, run analyze_without_products_audit_report.py "
            "(writes without_products_analysis_report/ next to the checks CSV)."
        ),
    )
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")

    checks_out = (
        Path(args.output) if args.output else input_dir / "audit_checks_collated.csv"
    )
    usage_out = (
        Path(args.usage_output)
        if args.usage_output
        else input_dir / "usage_metadata_collated.csv"
    )
    run_id = input_dir.name

    json_files = sorted(input_dir.glob("**/*_audit.json"))
    if not json_files:
        raise FileNotFoundError(f"No *_audit.json files found under: {input_dir}")

    rows = []
    usage_rows = []

    for json_file in json_files:
        unit_id = json_file.parent.name
        rel = json_file.relative_to(input_dir)

        usage_path = json_file.parent / "usage_metadata.json"
        if usage_path.is_file():
            with usage_path.open("r", encoding="utf-8") as uf:
                usage_payload = json.load(uf)
            urow = {"run_id": run_id, "unit_id": unit_id, "source_file": str(rel)}
            copy_dynamic_fields(urow, usage_payload, skip_keys=set())
            usage_rows.append(urow)

        with json_file.open("r", encoding="utf-8") as f:
            payload = json.load(f)

        if not isinstance(payload, dict):
            continue

        for section, value in payload.items():
            if not isinstance(value, list) or not is_field_check_list(value):
                continue
            for field in value:
                if not isinstance(field, dict):
                    continue
                row = {
                    "run_id": run_id,
                    "unit_id": unit_id,
                    "source_file": str(rel),
                    "section": section,
                    "entity": section,
                }
                copy_dynamic_fields(row, field, skip_keys=set())
                rows.append(row)

    all_keys = set()
    for row in rows:
        all_keys.update(row.keys())

    preferred_order = [
        "run_id",
        "unit_id",
        "source_file",
        "section",
        "entity",
        "gt_index",
        "flash_index",
        "match_status",
        "alignment_note",
        "spec_name",
        "field_path",
        "ground_truth",
        "flash_output",
        "verdict",
        "reason",
        "gt_normalized",
        "flash_normalized",
        "context_note",
    ]
    remaining = sorted(key for key in all_keys if key not in preferred_order)
    header = [key for key in preferred_order if key in all_keys] + remaining

    checks_out.parent.mkdir(parents=True, exist_ok=True)
    with checks_out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            normalized_row = {k: serialize_value(v) for k, v in row.items()}
            writer.writerow(normalized_row)

    print(f"Audit JSON files: {len(json_files)}")
    print(f"Check rows written: {len(rows)}")
    print(f"Checks CSV: {checks_out}")

    if not args.no_usage and usage_rows:
        ukeys = set()
        for r in usage_rows:
            ukeys.update(r.keys())
        u_preferred = ["run_id", "unit_id", "source_file"]
        u_rest = sorted(k for k in ukeys if k not in u_preferred)
        u_header = [k for k in u_preferred if k in ukeys] + u_rest
        with usage_out.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=u_header, extrasaction="ignore")
            w.writeheader()
            for row in usage_rows:
                w.writerow({k: serialize_value(v) for k, v in row.items()})
        print(f"Usage rows written: {len(usage_rows)}")
        print(f"Usage CSV: {usage_out}")
    elif not args.no_usage:
        print("No usage_metadata.json found; skipped usage CSV.")

    if args.analyze:
        analyzer = Path(__file__).resolve().parent / "analyze_without_products_audit_report.py"
        if not analyzer.is_file():
            raise FileNotFoundError(f"Analyzer script not found: {analyzer}")
        subprocess.run(
            [sys.executable, str(analyzer), str(checks_out)],
            check=True,
        )


if __name__ == "__main__":
    main()
