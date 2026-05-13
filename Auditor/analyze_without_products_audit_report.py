"""
Analysis for without-products audit CSVs (buyer_details, seller_details,
metadata, payment, moq, next_steps). Not for product / spec / price schema.
"""

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List

import re

from analyze_audit_report import (
    build_field_examples,
    load_rows_from_csv,
    normalize_enum,
    shorten,
    to_percent,
    write_field_distribution_csv,
    write_overall_distribution_csv,
)


def row_section(row: dict) -> str:
    s = (row.get("section") or row.get("entity") or "").strip()
    return s if s else "UNKNOWN"


def generic_field_path(field_path: str) -> str:
    path = (field_path or "").strip()
    if path == "":
        return "<EMPTY_FIELD_PATH>"
    return re.sub(r"\[[^\]]+\]", "[*]", path)


def field_group_key(row: dict) -> str:
    sec = row_section(row)
    g = generic_field_path(row.get("field_path", ""))
    return f"{sec} / {g}"


def write_section_verdict_distribution_csv(
    output_path: Path,
    rows_by_section: Dict[str, List[dict]],
    total_audit_rows: int,
):
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "section",
                "checks_in_section",
                "verdict",
                "count",
                "percentage_within_section",
                "section_checks_percent_of_all_rows",
            ],
        )
        writer.writeheader()
        for section in sorted(rows_by_section.keys()):
            sec_rows = rows_by_section[section]
            n_sec = len(sec_rows)
            share = to_percent(n_sec, total_audit_rows)
            counts = Counter(normalize_enum(r.get("verdict")) for r in sec_rows)
            for verdict, count in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
                writer.writerow(
                    {
                        "section": section,
                        "checks_in_section": n_sec,
                        "verdict": verdict,
                        "count": count,
                        "percentage_within_section": f"{to_percent(count, n_sec):.2f}",
                        "section_checks_percent_of_all_rows": f"{share:.2f}",
                    }
                )


def write_markdown_report(
    output_path: Path,
    source_label: str,
    all_rows: List[dict],
    verdict_counts: Counter,
    rows_by_section: Dict[str, List[dict]],
    grouped_field_rows: Dict[str, List[dict]],
    max_examples_per_verdict: int,
):
    total_rows = len(all_rows)
    with output_path.open("w", encoding="utf-8") as f:
        f.write("# Without-products audit verdict analysis\n\n")
        f.write(
            "This report is for **non-product** audit checks "
            "(sections such as `buyer_details`, `seller_details`, `metadata`, "
            "`payment`, `moq`, `next_steps`). "
            "It does **not** use product alignment fields (`gt_index`, `flash_index`, "
            "`match_status`, specifications, etc.).\n\n"
        )
        f.write(f"- Source: `{source_label}`\n")
        f.write(f"- Total check rows: **{total_rows}**\n")
        f.write(f"- Audit sections present: **{len(rows_by_section)}**\n")
        f.write(f"- Distinct field groups (section / field): **{len(grouped_field_rows)}**\n\n")

        f.write("## Overall verdict distribution\n\n")
        f.write("| Verdict | Count | Percentage |\n")
        f.write("|---|---:|---:|\n")
        for verdict, count in sorted(verdict_counts.items(), key=lambda x: (-x[1], x[0])):
            f.write(f"| {verdict} | {count} | {to_percent(count, total_rows):.2f}% |\n")
        f.write("\n")

        f.write("## Verdicts by audit section\n\n")
        f.write(
            "Each subsection is one JSON top-level array from the without-products "
            "audit (e.g. `metadata`, `next_steps`).\n\n"
        )
        for section in sorted(rows_by_section.keys()):
            sec_rows = rows_by_section[section]
            n = len(sec_rows)
            counts = Counter(normalize_enum(r.get("verdict")) for r in sec_rows)
            dominant, dom_n = sorted(counts.items(), key=lambda x: (-x[1], x[0]))[0]
            match_n = counts.get("MATCH", 0)
            partial_n = counts.get("PARTIAL", 0)

            f.write(f"### `{section}`\n\n")
            f.write(f"- Checks: **{n}** ({to_percent(n, total_rows):.2f}% of all rows)\n")
            f.write(
                f"- Dominant verdict: **{dominant}** ({to_percent(dom_n, n):.2f}% within section)\n"
            )
            f.write(f"- Match rate: **{to_percent(match_n, n):.2f}%**\n")
            f.write(
                f"- Match + partial rate: **{to_percent(match_n + partial_n, n):.2f}%**\n\n"
            )
            f.write("| Verdict | Count | % within section |\n")
            f.write("|---|---:|---:|\n")
            for verdict, count in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
                f.write(f"| {verdict} | {count} | {to_percent(count, n):.2f}% |\n")
            f.write("\n")

        f.write("## Field-level summary\n\n")
        f.write(
            "Groups are named **`section` / `field_path`** (after bracket generalisation). "
            f"Up to **{max_examples_per_verdict}** examples per verdict per field.\n\n"
        )

        for group in sorted(grouped_field_rows.keys()):
            rows = grouped_field_rows[group]
            total = len(rows)
            counts = Counter(normalize_enum(r.get("verdict")) for r in rows)
            dominant_verdict, dominant_count = sorted(
                counts.items(), key=lambda x: (-x[1], x[0])
            )[0]
            match_count = counts.get("MATCH", 0)
            partial_count = counts.get("PARTIAL", 0)

            f.write(f"### `{group}`\n\n")
            f.write(f"- Total rows: **{total}**\n")
            f.write(
                f"- Dominant verdict: **{dominant_verdict}** "
                f"({to_percent(dominant_count, total):.2f}%)\n"
            )
            f.write(f"- Match rate: **{to_percent(match_count, total):.2f}%**\n")
            f.write(
                f"- Match + partial rate: **{to_percent(match_count + partial_count, total):.2f}%**\n\n"
            )
            f.write("| Verdict | Count | Percentage |\n")
            f.write("|---|---:|---:|\n")
            for verdict, count in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
                f.write(f"| {verdict} | {count} | {to_percent(count, total):.2f}% |\n")
            f.write("\n")

            examples_by_verdict = build_field_examples(rows, max_examples_per_verdict)
            for verdict in sorted(examples_by_verdict.keys()):
                f.write(f"**Examples — {verdict}**\n\n")
                for ex in examples_by_verdict[verdict]:
                    f.write(
                        "- "
                        f"`{ex.get('unit_id', '')}` "
                        f"(section: `{row_section(ex)}`) "
                        f"GT=`{shorten(ex.get('ground_truth', ''))}` | "
                        f"FLASH=`{shorten(ex.get('flash_output', ''))}` | "
                        f"Reason: {shorten(ex.get('reason', ''), 220)}\n"
                    )
                f.write("\n")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Build without-products audit analysis: overall verdicts, "
            "per-section breakdown, and per-field examples (not product schema)."
        )
    )
    parser.add_argument(
        "input_csv",
        nargs="?",
        default="without_products_audit_outputs_20260507_132902/audit_checks_collated.csv",
        help="Path to audit_checks_collated.csv from collate_without_products_audit_json_to_csv.py",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Output folder. Default: <csv_parent>/without_products_analysis_report"
        ),
    )
    parser.add_argument(
        "--examples-per-verdict",
        type=int,
        default=2,
        help="Max examples per verdict in each field section.",
    )
    args = parser.parse_args()

    input_csv = Path(args.input_csv)
    if not input_csv.is_file():
        raise FileNotFoundError(f"CSV not found: {input_csv}")

    all_rows = load_rows_from_csv(input_csv)
    if not all_rows:
        raise ValueError(f"No rows in {input_csv}")

    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = input_csv.parent / "without_products_analysis_report"
    output_dir.mkdir(parents=True, exist_ok=True)

    verdict_counts = Counter(normalize_enum(r.get("verdict")) for r in all_rows)

    rows_by_section: Dict[str, List[dict]] = defaultdict(list)
    for row in all_rows:
        rows_by_section[row_section(row)].append(row)

    grouped_field_rows: Dict[str, List[dict]] = defaultdict(list)
    for row in all_rows:
        grouped_field_rows[field_group_key(row)].append(row)

    overall_csv = output_dir / "overall_verdict_distribution.csv"
    section_csv = output_dir / "section_verdict_distribution.csv"
    by_field_csv = output_dir / "field_verdict_distribution.csv"
    report_md = output_dir / "audit_analysis_report.md"

    write_overall_distribution_csv(overall_csv, len(all_rows), verdict_counts)
    write_section_verdict_distribution_csv(section_csv, dict(rows_by_section), len(all_rows))
    write_field_distribution_csv(by_field_csv, dict(grouped_field_rows), len(all_rows))
    write_markdown_report(
        report_md,
        str(input_csv),
        all_rows,
        verdict_counts,
        dict(rows_by_section),
        dict(grouped_field_rows),
        max_examples_per_verdict=max(1, args.examples_per_verdict),
    )

    print(f"Input: {input_csv}")
    print(f"Rows: {len(all_rows)}")
    print(f"Sections: {len(rows_by_section)}")
    print(f"Field groups: {len(grouped_field_rows)}")
    print(f"Report: {report_md}")
    print(f"CSV: {overall_csv}, {section_csv}, {by_field_csv}")


if __name__ == "__main__":
    main()
