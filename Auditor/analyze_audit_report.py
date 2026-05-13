import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List


def normalize_enum(value: str) -> str:
    if value is None:
        return "UNKNOWN"
    value = str(value).strip()
    if value == "":
        return "UNKNOWN"
    return value.upper()


def to_percent(part: int, total: int) -> float:
    if total == 0:
        return 0.0
    return (part * 100.0) / total


def shorten(text: str, max_len: int = 140) -> str:
    if text is None:
        return ""
    text = str(text).strip().replace("\n", " ")
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def serialize_value(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True)
    return value


def normalize_row_values(row: dict) -> dict:
    cleaned = {}
    for key, value in row.items():
        clean_key = (key or "").strip()
        if isinstance(value, str):
            cleaned[clean_key] = value.strip()
        else:
            cleaned[clean_key] = serialize_value(value)
    return cleaned


def generic_field_path(entity: str, field_path: str) -> str:
    path = (field_path or "").strip()
    if path == "":
        return "<EMPTY_FIELD_PATH>"

    # Make index/spec labels generic so field-level analysis is stable across calls.
    # Example: specifications[Brand].value -> specifications[*].value
    #          related_categories[0].name -> related_categories[*].name
    path = re.sub(r"\[[^\]]+\]", "[*]", path)

    # Normalize short spec leaf paths to avoid split buckets like "value" vs
    # "specifications[*].value" across different calls.
    if entity == "product_spec":
        if path in {"value", "unit", "name", "buyer_requested", "seller_mentioned"}:
            return f"specifications[*].{path}"
        if not path.startswith("specifications[*]."):
            return f"specifications[*].{path}"
    return path


def load_rows_from_csv(input_csv: Path) -> List[dict]:
    rows: List[dict] = []
    with input_csv.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, skipinitialspace=True)
        if reader.fieldnames:
            reader.fieldnames = [(name or "").strip() for name in reader.fieldnames]
        for raw_row in reader:
            rows.append(normalize_row_values(raw_row))
    return rows


def load_rows_from_json_folder(input_dir: Path) -> List[dict]:
    json_files = sorted(input_dir.glob("**/*_audit.json"))
    if not json_files:
        raise FileNotFoundError(f"No *_audit.json files found under: {input_dir}")

    rows: List[dict] = []
    run_id = input_dir.name

    for json_file in json_files:
        unit_id = json_file.parent.name
        with json_file.open("r", encoding="utf-8") as f:
            payload = json.load(f)

        for product in payload.get("products", []):
            base = {
                "run_id": run_id,
                "unit_id": unit_id,
                "source_file": str(json_file.relative_to(input_dir)),
                "gt_index": product.get("gt_index"),
                "flash_index": product.get("flash_index"),
                "match_status": product.get("match_status"),
                "alignment_note": product.get("alignment_note"),
                "split_merge_note": product.get("split_merge_note"),
            }

            for field in product.get("fields", []):
                row = dict(base)
                row["entity"] = "product"
                row["spec_name"] = ""
                row.update(field)
                rows.append(normalize_row_values(row))

            price = product.get("price") or {}
            for field in price.get("fields", []):
                row = dict(base)
                row["entity"] = "product_price"
                row["spec_name"] = ""
                row["price_unit_equivalence_note"] = price.get("unit_equivalence_note")
                row.update(field)
                rows.append(normalize_row_values(row))

            for spec in product.get("specifications", []):
                spec_meta = {
                    "spec_name": spec.get("spec_name"),
                    "resolved_spec_name": spec.get("resolved_spec_name"),
                    "spec_match_status": spec.get("match_status"),
                    "derived_from_gt_product_name": spec.get("derived_from_gt_product_name"),
                    "synonym_resolved": spec.get("synonym_resolved"),
                    "dimension_split": spec.get("dimension_split"),
                }
                for field in spec.get("fields", []):
                    row = dict(base)
                    row["entity"] = "product_spec"
                    row.update(spec_meta)
                    row.update(field)
                    rows.append(normalize_row_values(row))

    return rows


def write_overall_distribution_csv(
    output_path: Path,
    total_rows: int,
    verdict_counts: Counter,
):
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["verdict", "count", "percentage"],
        )
        writer.writeheader()
        for verdict, count in sorted(
            verdict_counts.items(), key=lambda x: (-x[1], x[0])
        ):
            writer.writerow(
                {
                    "verdict": verdict,
                    "count": count,
                    "percentage": f"{to_percent(count, total_rows):.2f}",
                }
            )


def write_entity_verdict_distribution_csv(
    output_path: Path,
    rows_by_entity: Dict[str, List[dict]],
    total_audit_rows: int,
) -> None:
    """Per-entity (section) breakdown: entity → verdict → count / %. 
    Mirrors write_section_verdict_distribution_csv from analyze_without_products_audit_report.py."""
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "entity",
                "checks_in_entity",
                "verdict",
                "count",
                "percentage_within_entity",
                "entity_checks_percent_of_all_rows",
            ],
        )
        writer.writeheader()
        for entity in sorted(rows_by_entity.keys()):
            ent_rows = rows_by_entity[entity]
            n_ent = len(ent_rows)
            share = to_percent(n_ent, total_audit_rows)
            counts = Counter(normalize_enum(r.get("verdict")) for r in ent_rows)
            for verdict, count in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
                writer.writerow(
                    {
                        "entity": entity,
                        "checks_in_entity": n_ent,
                        "verdict": verdict,
                        "count": count,
                        "percentage_within_entity": f"{to_percent(count, n_ent):.2f}",
                        "entity_checks_percent_of_all_rows": f"{share:.2f}",
                    }
                )


def write_field_distribution_csv(
    output_path: Path,
    grouped_field_rows: Dict[str, List[dict]],
    total_audit_rows: int,
):
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "field_group",
                "original_field_paths_count",
                "original_field_paths_examples",
                "field_occurrences",
                "field_coverage_percent_of_all_rows",
                "verdict",
                "count",
                "percentage_within_field_group",
                "example_unit_ids",
                "example_ground_truth",
                "example_flash_output",
                "example_reason",
            ],
        )
        writer.writeheader()
        for field_group in sorted(grouped_field_rows.keys()):
            rows = grouped_field_rows[field_group]
            total = len(rows)
            counts = Counter(normalize_enum(r.get("verdict")) for r in rows)
            original_paths = sorted(
                {
                    (r.get("field_path") or "").strip()
                    for r in rows
                    if (r.get("field_path") or "").strip() != ""
                }
            )
            original_examples = " | ".join(original_paths[:3])
            for verdict, count in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
                verdict_examples = [
                    r for r in rows if normalize_enum(r.get("verdict")) == verdict
                ][:2]
                example_unit_ids = " | ".join(
                    shorten(ex.get("unit_id", ""), 40) for ex in verdict_examples
                )
                example_ground_truth = " | ".join(
                    shorten(ex.get("ground_truth", ""), 80) for ex in verdict_examples
                )
                example_flash_output = " | ".join(
                    shorten(ex.get("flash_output", ""), 80) for ex in verdict_examples
                )
                example_reason = " | ".join(
                    shorten(ex.get("reason", ""), 120) for ex in verdict_examples
                )
                writer.writerow(
                    {
                        "field_group": field_group,
                        "original_field_paths_count": len(original_paths),
                        "original_field_paths_examples": original_examples,
                        "field_occurrences": total,
                        "field_coverage_percent_of_all_rows": f"{to_percent(total, total_audit_rows):.2f}",
                        "verdict": verdict,
                        "count": count,
                        "percentage_within_field_group": f"{to_percent(count, total):.2f}",
                        "example_unit_ids": example_unit_ids,
                        "example_ground_truth": example_ground_truth,
                        "example_flash_output": example_flash_output,
                        "example_reason": example_reason,
                    }
                )


def build_field_examples(
    rows: List[dict],
    max_examples_per_verdict: int,
) -> Dict[str, List[dict]]:
    verdict_buckets: Dict[str, List[dict]] = defaultdict(list)
    for row in rows:
        verdict = normalize_enum(row.get("verdict"))
        if len(verdict_buckets[verdict]) < max_examples_per_verdict:
            verdict_buckets[verdict].append(row)
    return verdict_buckets


def write_markdown_report(
    output_path: Path,
    source_label: str,
    all_rows: List[dict],
    verdict_counts: Counter,
    grouped_field_rows: Dict[str, List[dict]],
    max_examples_per_verdict: int,
):
    total_rows = len(all_rows)
    with output_path.open("w", encoding="utf-8") as f:
        f.write("# Audit Verdict Analysis Report\n\n")
        f.write(f"- Source: `{source_label}`\n")
        f.write(f"- Total audit rows analyzed: **{total_rows}**\n")
        f.write(f"- Unique generic field groups: **{len(grouped_field_rows)}**\n\n")

        f.write("## Overall Verdict Distribution\n\n")
        f.write("| Verdict | Count | Percentage |\n")
        f.write("|---|---:|---:|\n")
        for verdict, count in sorted(verdict_counts.items(), key=lambda x: (-x[1], x[0])):
            f.write(f"| {verdict} | {count} | {to_percent(count, total_rows):.2f}% |\n")
        f.write("\n")

        f.write("## Field-wise Summary\n\n")
        f.write(
            "Each field includes verdict distribution and up to "
            f"{max_examples_per_verdict} examples per verdict.\n\n"
        )

        for field_path in sorted(grouped_field_rows.keys()):
            rows = grouped_field_rows[field_path]
            total = len(rows)
            counts = Counter(normalize_enum(r.get("verdict")) for r in rows)
            dominant_verdict, dominant_count = sorted(
                counts.items(), key=lambda x: (-x[1], x[0])
            )[0]

            f.write(f"### `{field_path}`\n\n")
            f.write(f"- Total rows: **{total}**\n")
            f.write(
                f"- Dominant verdict: **{dominant_verdict}** "
                f"({to_percent(dominant_count, total):.2f}%)\n"
            )

            match_count = counts.get("MATCH", 0)
            partial_count = counts.get("PARTIAL", 0)
            f.write(f"- Match rate: **{to_percent(match_count, total):.2f}%**\n")
            f.write(
                f"- Match+Partial rate: **{to_percent(match_count + partial_count, total):.2f}%**\n\n"
            )

            f.write("| Verdict | Count | Percentage |\n")
            f.write("|---|---:|---:|\n")
            for verdict, count in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
                f.write(f"| {verdict} | {count} | {to_percent(count, total):.2f}% |\n")
            f.write("\n")

            examples_by_verdict = build_field_examples(rows, max_examples_per_verdict)
            for verdict in sorted(examples_by_verdict.keys()):
                f.write(f"**Examples - {verdict}**\n\n")
                for ex in examples_by_verdict[verdict]:
                    f.write(
                        "- "
                        f"`{ex.get('unit_id', '')}` "
                        f"(entity: `{ex.get('entity', '')}`, gt_index: `{ex.get('gt_index', '')}`, flash_index: `{ex.get('flash_index', '')}`) "
                        f"GT=`{shorten(ex.get('ground_truth', ''))}` | "
                        f"FLASH=`{shorten(ex.get('flash_output', ''))}` | "
                        f"Reason: {shorten(ex.get('reason', ''), 220)}\n"
                    )
                f.write("\n")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate audit analysis report: overall verdict %, "
            "field-wise verdict %, and examples."
        )
    )
    parser.add_argument(
        "input_path",
        nargs="?",
        default="audit_outputs_20260507_142907",
        help="Audit run folder containing U*/U*_audit.json (or a CSV file path).",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Output directory for analysis files. Defaults to "
            "<input-folder>/analysis_report."
        ),
    )
    parser.add_argument(
        "--examples-per-verdict",
        type=int,
        default=2,
        help="Maximum examples to include per verdict inside each field summary.",
    )
    args = parser.parse_args()

    input_path = Path(args.input_path)
    source_label = str(input_path)

    if input_path.is_dir():
        all_rows = load_rows_from_json_folder(input_path)
        output_parent = input_path
    elif input_path.is_file():
        all_rows = load_rows_from_csv(input_path)
        output_parent = input_path.parent
    else:
        raise FileNotFoundError(f"Input path not found: {input_path}")

    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = output_parent / "analysis_report"
    output_dir.mkdir(parents=True, exist_ok=True)

    if not all_rows:
        raise ValueError(f"No data rows found in {input_path}")

    verdict_counts = Counter(normalize_enum(r.get("verdict")) for r in all_rows)
    grouped_field_rows: Dict[str, List[dict]] = defaultdict(list)
    rows_by_entity: Dict[str, List[dict]] = defaultdict(list)
    for row in all_rows:
        entity = row.get("entity", "") or "UNKNOWN"
        field_path = row.get("field_path", "")
        field_group = generic_field_path(entity, field_path)
        grouped_field_rows[field_group].append(row)
        rows_by_entity[entity].append(row)

    overall_csv = output_dir / "overall_verdict_distribution.csv"
    entity_csv = output_dir / "entity_verdict_distribution.csv"
    by_field_csv = output_dir / "field_verdict_distribution.csv"
    report_md = output_dir / "audit_analysis_report.md"

    write_overall_distribution_csv(overall_csv, len(all_rows), verdict_counts)
    write_entity_verdict_distribution_csv(entity_csv, dict(rows_by_entity), len(all_rows))
    write_field_distribution_csv(by_field_csv, grouped_field_rows, len(all_rows))
    write_markdown_report(
        report_md,
        source_label,
        all_rows,
        verdict_counts,
        grouped_field_rows,
        max_examples_per_verdict=max(1, args.examples_per_verdict),
    )

    print(f"Input source: {source_label}")
    print(f"Total rows analyzed: {len(all_rows)}")
    print(f"Unique entity types: {len(rows_by_entity)}")
    print(f"Unique generic field groups: {len(grouped_field_rows)}")
    print(f"Output report: {report_md}")
    print(f"Output overall CSV: {overall_csv}")
    print(f"Output entity CSV: {entity_csv}")
    print(f"Output by-field CSV: {by_field_csv}")


if __name__ == "__main__":
    main()
