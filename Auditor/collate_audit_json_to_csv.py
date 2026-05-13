import argparse
import csv
import json
import subprocess
import sys
import csv
import json
from pathlib import Path


def serialize_value(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True)
    return value


def build_row_base(run_id, unit_id, product, source_file):
    return {
        "run_id": run_id,
        "unit_id": unit_id,
        "source_file": str(source_file),
        "gt_index": product.get("gt_index"),
        "flash_index": product.get("flash_index"),
        "match_status": product.get("match_status"),
        "alignment_note": product.get("alignment_note"),
        "split_merge_note": product.get("split_merge_note"),
        "context_group_id": product.get("context_group_id"),
        "name_embedded_attributes": serialize_value(product.get("name_embedded_attributes")),
    }


def copy_dynamic_fields(target, payload, skip_keys):
    for key, value in payload.items():
        if key in skip_keys:
            continue
        target[key] = serialize_value(value)


def main():
    parser = argparse.ArgumentParser(
        description="Collate audit JSON outputs into one CSV."
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        default="audit_outputs_20260507_142907",
        help="Directory containing U*/U*_audit.json files.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output CSV path. Defaults to <input_dir>/audit_checks_collated.csv",
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
            "After writing checks CSV, run analyze_audit_report.py "
            "(writes analysis_report/ next to the checks CSV)."
        ),
    )
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")

    checks_out = (
        Path(args.output) if args.output else input_dir / "audit_checks_collated.csv"
    )
    usage_out = input_dir / "usage_metadata_collated.csv"
    run_id = input_dir.name

    json_files = sorted(input_dir.glob("**/*_audit.json"))
    if not json_files:
        raise FileNotFoundError(f"No *_audit.json files found under: {input_dir}")

    rows = []
    usage_rows = []

    for json_file in json_files:
        unit_id = json_file.parent.name

        usage_path = json_file.parent / "usage_metadata.json"
        if usage_path.is_file():
            with usage_path.open("r", encoding="utf-8") as uf:
                usage_payload = json.load(uf)
            urow = {"run_id": run_id, "unit_id": unit_id, "source_file": str(json_file.relative_to(input_dir))}
            copy_dynamic_fields(urow, usage_payload, skip_keys=set())
            usage_rows.append(urow)

        with json_file.open("r", encoding="utf-8") as f:
            payload = json.load(f)

        if not isinstance(payload, dict):
            continue

        products = payload.get("products", [])
        for product in products:
            base = build_row_base(run_id, unit_id, product, json_file.relative_to(input_dir))

            # Product field comparisons.
            for field in product.get("fields", []):
                row = dict(base)
                row["entity"] = "product"
                row["spec_name"] = None
                copy_dynamic_fields(
                    row,
                    field,
                    skip_keys=set(),
                )
                rows.append(row)

            # Price field comparisons.
            price = product.get("price") or {}
            for field in price.get("fields", []):
                row = dict(base)
                row["entity"] = "product_price"
                row["spec_name"] = None
                row["price_unit_equivalence_note"] = price.get("unit_equivalence_note")
                copy_dynamic_fields(
                    row,
                    field,
                    skip_keys=set(),
                )
                rows.append(row)

            # Specification field comparisons.
            for spec in product.get("specifications", []):
                spec_name = spec.get("name")
                for field in spec.get("fields", []):
                    row = dict(base)
                    row["entity"] = "specification"
                    row["spec_name"] = spec_name
                    copy_dynamic_fields(
                        row,
                        field,
                        skip_keys=set(),
                    )
                    rows.append(row)

    # Build dynamic CSV header from all observed keys.
    all_keys = set()
    for row in rows:
        all_keys.update(row.keys())

    preferred_order = [
        "run_id",
        "unit_id",
        "source_file",
        "gt_index",
        "flash_index",
        "match_status",
        "alignment_note",
        "entity",
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

    if args.analyze:
        analyzer = Path(__file__).resolve().parent / "analyze_audit_report.py"
        if not analyzer.is_file():
            raise FileNotFoundError(f"Analyzer script not found: {analyzer}")
        subprocess.run(
            [sys.executable, str(analyzer), str(checks_out)],
            check=True,
        )


if __name__ == "__main__":
    main()
