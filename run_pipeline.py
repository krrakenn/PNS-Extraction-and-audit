#!/usr/bin/env python3
"""
PNS Audit Pipeline Orchestrator
================================
Runs the full end-to-end audit pipeline or any individual step.

Usage:
  python run_pipeline.py                    # run all steps
  python run_pipeline.py --step filter      # Step 1: filter Kibana CSV
  python run_pipeline.py --step extract     # Step 2: run Go extraction (pro + flash)
  python run_pipeline.py --step audit       # Step 3+4: run both audit scripts
  python run_pipeline.py --step collate     # Step 5+6: collate audit JSONs to CSV
  python run_pipeline.py --step analyze     # Step 7+8: run analysis reports

  # After extraction, supply existing output paths for audit:
  python run_pipeline.py --step audit --pro-csv path/to/pro/output.csv --flash-csv path/to/flash/output.csv

  # After audit, supply existing audit dirs for collate:
  python run_pipeline.py --step collate --audit-dir-noproducts path/to/without_products_dir --audit-dir-products path/to/products_dir

Config files (edit these, not source code):
  pipeline_config.json   — paths, models, settings
  prompts_config.json    — extraction prompt + system role
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from dotenv import load_dotenv
from langfuse import Langfuse

# ─── Resolve paths relative to this script ───────────────────────────────────
ROOT = Path(__file__).parent.resolve()
CONFIG_FILE = ROOT / "pipeline_config.json"
PROMPTS_FILE = ROOT / "prompts_config.json"
CSV_BATCH_TOOL = ROOT / "csv_batch_tool"
AUDITOR = ROOT / "Auditor"

# Load .env from csv_batch_tool
load_dotenv(ROOT / "csv_batch_tool" / ".env")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def banner(title: str) -> None:
    bar = "=" * 60
    print(f"\n{bar}\n  {title}\n{bar}", flush=True)


def run(cmd: list[str], cwd: Path, env: dict = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a subprocess, streaming output live."""
    merged_env = {**os.environ, **(env or {})}
    log(f"CMD: {' '.join(str(c) for c in cmd)}")
    log(f"CWD: {cwd}")
    result = subprocess.run(cmd, cwd=str(cwd), env=merged_env)
    if check and result.returncode != 0:
        log(f"ERROR: command failed with exit code {result.returncode}")
        sys.exit(result.returncode)
    return result


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        log(f"ERROR: {CONFIG_FILE} not found")
        sys.exit(1)
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_prompts() -> dict:
    try:
        lf = Langfuse()
        prompt_obj = lf.get_prompt("extraction_prompt")
        log("Successfully loaded extraction_prompt from Langfuse.")
        return {"system_role": prompt_obj.prompt}
    except Exception as e:
        log(f"WARNING: Failed to load extraction_prompt from Langfuse: {e} — Go tool will use its built-in prompts")
        return {}


def write_prompts_override(prompts: dict) -> None:
    """Write prompts_override.json inside csv_batch_tool so the Go tool picks them up."""
    override_path = CSV_BATCH_TOOL / "prompts_override.json"
    payload = {}
    if "prompt" in prompts:
        payload["prompt"] = prompts["prompt"]
    if "system_role" in prompts:
        payload["system_role"] = prompts["system_role"]
    if payload:
        with open(override_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        log(f"Wrote prompts override → {override_path}")


def find_latest_output(base_dir: Path, model_name: str) -> Path | None:
    """Find the most recent timestamped output.csv for a given model."""
    model_slug = model_name.replace("/", "_").replace(":", "_")
    model_dir = base_dir / model_slug
    if not model_dir.exists():
        return None
    candidates = sorted(model_dir.glob("*/output.csv"), reverse=True)
    return candidates[0] if candidates else None


# ─── Pipeline Steps ───────────────────────────────────────────────────────────

def step_filter(cfg: dict) -> None:
    banner("STEP 1 — Filter Kibana CSV")
    input_csv = ROOT / cfg["input_csv"]
    keep = cfg["keep_columns"]
    output_path = CSV_BATCH_TOOL / "input" / "filtered_output_20-04.csv"

    if not input_csv.exists():
        log(f"ERROR: Input CSV not found: {input_csv}")
        sys.exit(1)

    run(
        ["go", "run", "kibana_toinput.go",
         "-input", str(input_csv),
         "-output", str(output_path),
         "-keep", keep],
        cwd=ROOT,
    )
    log(f"✓ Filtered CSV written → {output_path}")


def step_extract(cfg: dict, pro_model: str = None, flash_model: str = None, flash_json_csv: Path = None, skip_flash: bool = False) -> tuple[Path, Path]:
    """Run Go extraction. Returns (pro_output_path, flash_output_path).
    
    Pass skip_flash=True to run only the Pro model (when Flash data comes from Redash).
    """
    banner("STEP 2 — LLM Extraction")
    prompts = load_prompts()
    write_prompts_override(prompts)

    extraction_base = CSV_BATCH_TOOL / cfg["extraction_output_dir"]
    pro_m = pro_model or cfg["pro_model"]
    
    # Run PRO extraction
    banner(f"STEP 2a — Extraction: {pro_m}")
    run(
        ["go", "run", "."],
        cwd=CSV_BATCH_TOOL,
        env={"PNS_EXTRACTION_MODEL": pro_m},
    )
    pro_output_path = find_latest_output(extraction_base, pro_m)
    if not pro_output_path:
        log(f"ERROR: Could not locate output.csv for model {pro_m}")
        sys.exit(1)
    log(f"✓ PRO extraction complete → {pro_output_path}")

    # Handle FLASH (either skip, run model, or import provided)
    flash_output_path: Path | None = None
    if skip_flash:
        banner("STEP 2b — Skipping Flash extraction (using Redash-normalized CSV)")
        log("  --skip-flash set: Flash Go extraction skipped.")
        # Fix: explicitly set the path so step_clean_errors can clean it!
        flash_output_path = CSV_BATCH_TOOL / "input" / "daily_flash_normalized.csv"
    elif flash_json_csv:
        banner("STEP 2b — Importing Provided Flash Data")
        flash_output_path = extraction_base / "imported_flash" / time.strftime("%Y%m%d_%H%M%S") / "output.csv"
        flash_output_path.parent.mkdir(parents=True, exist_ok=True)
        run(
            [sys.executable, str(AUDITOR / "import_flash_data.py"),
             "--source-csv", str(flash_json_csv),
             "--output-csv", str(flash_output_path)],
            cwd=ROOT
        )
    else:
        flash_m = flash_model or cfg["flash_model"]
        banner(f"STEP 2b — Extraction: {flash_m}")
        run(
            ["go", "run", "."],
            cwd=CSV_BATCH_TOOL,
            env={"PNS_EXTRACTION_MODEL": flash_m},
        )
        flash_output_path = find_latest_output(extraction_base, flash_m)
        if not flash_output_path:
            log(f"ERROR: Could not locate output.csv for model {flash_m}")
            sys.exit(1)
        log(f"✓ FLASH extraction complete → {flash_output_path}")

    return pro_output_path, flash_output_path


def step_clean_errors(pro_csv: Path, flash_csv: Path) -> tuple[Path, Path]:
    """Remove rows that have ERROR in Thinking JSON from both Pro and Flash CSVs.
    
    Reads both CSVs, identifies file IDs with ERROR rows in the Pro output,
    then drops those rows from both CSVs. Overwrites both files in-place.
    Returns the same paths so they can be passed directly to step_audit.
    """
    import csv as _csv

    banner("STEP 2c — Cleaning ERROR Rows")

    def _read(path: Path):
        with open(path, encoding="utf-8", newline="") as fh:
            reader = _csv.DictReader(fh)
            rows = list(reader)
            fieldnames = reader.fieldnames or []
        return rows, fieldnames

    def _write(path: Path, rows: list, fieldnames: list):
        with open(path, "w", encoding="utf-8", newline="") as fh:
            writer = _csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def _norm(col: str) -> str:
        import re
        return re.sub(r"[^a-z0-9]+", "", col.strip().lower())

    def _find_col(fieldnames: list, candidates: list) -> str | None:
        norm_map = {_norm(c): c for c in fieldnames}
        for c in candidates:
            if _norm(c) in norm_map:
                return norm_map[_norm(c)]
        return None

    # ── Read Pro CSV ───────────────────────────────────────────────────────────
    pro_rows, pro_fields = _read(pro_csv)
    pro_json_col = _find_col(pro_fields, ["Thinking JSON", "thinking_json", "ThinkingJSON"])
    pro_id_col   = _find_col(pro_fields, ["file id", "file_id", "FILE_ID", "fileid"])

    if not pro_json_col or not pro_id_col:
        log("WARNING: Could not detect Thinking JSON / file id columns in Pro CSV — skipping error cleanup.")
        return pro_csv, flash_csv

    # ── Find error file IDs ────────────────────────────────────────────────────
    error_ids: set[str] = set()
    for row in pro_rows:
        thinking = (row.get(pro_json_col) or "").strip()
        if thinking.upper().startswith("ERROR"):
            fid = (row.get(pro_id_col) or "").strip()
            if fid:
                error_ids.add(fid)

    if not error_ids:
        log("✓ No ERROR rows found — Pro CSV is clean.")
        return pro_csv, flash_csv

    log(f"⚠ Found {len(error_ids)} ERROR row(s) in Pro CSV — file IDs: {sorted(error_ids)}")
    log(f"  These rows will be removed from both CSVs before auditing.")

    # ── Clean Pro CSV ──────────────────────────────────────────────────────────
    clean_pro = [r for r in pro_rows if (r.get(pro_id_col) or "").strip() not in error_ids]
    dropped_pro = len(pro_rows) - len(clean_pro)
    _write(pro_csv, clean_pro, pro_fields)
    log(f"✓ Pro CSV  — removed {dropped_pro} row(s) → {pro_csv}")

    # ── Clean Flash CSV (only if a Flash CSV path was provided) ───────────────
    if flash_csv is None:
        log("  Flash CSV is None (--skip-flash mode) — skipping Flash cleanup.")
        return pro_csv, flash_csv

    flash_rows, flash_fields = _read(flash_csv)
    flash_id_col = _find_col(flash_fields, ["file id", "file_id", "FILE_ID", "fileid"])

    if not flash_id_col:
        log("WARNING: Could not detect file id column in Flash CSV — skipping Flash cleanup.")
        return pro_csv, flash_csv

    clean_flash = [r for r in flash_rows if (r.get(flash_id_col) or "").strip() not in error_ids]
    dropped_flash = len(flash_rows) - len(clean_flash)
    _write(flash_csv, clean_flash, flash_fields)
    log(f"✓ Flash CSV — removed {dropped_flash} row(s) → {flash_csv}")

    return pro_csv, flash_csv


def step_audit(cfg: dict, pro_csv: Path, flash_csv: Path) -> tuple[Path, Path]:
    """Run both audit scripts. Returns (without_products_dir, products_dir)."""
    banner("STEP 3 — Audit: Without-Products (31-03_audit.py)")
    audit_base = AUDITOR / "audit_outputs"
    audit_model = cfg.get("audit_model", "google/gemini-2.5-pro")
    max_urls = str(cfg.get("max_urls", 210))

    env_audit = {
        "PNS_CSV_1": str(pro_csv),
        "PNS_CSV_2": str(flash_csv),
        "PNS_AUDIT_OUTPUT_DIR": str(audit_base),
        "PNS_AUDIT_MODEL": audit_model,
        "PNS_MAX_URLS": max_urls,
    }

    # Run without-products audit
    run(
        [sys.executable, "31-03_audit.py"],
        cwd=AUDITOR,
        env=env_audit,
    )

    # Find the output dir (most recently created)
    noproducts_dirs = sorted(
        audit_base.glob("without_products_audit_outputs_*"), reverse=True
    )
    if not noproducts_dirs:
        log("ERROR: Could not find without_products_audit_outputs_* dir")
        sys.exit(1)
    noproducts_dir = noproducts_dirs[0]
    log(f"✓ Without-products audit → {noproducts_dir}")

    banner("STEP 4 — Audit: Products (31-03_audit_products.py)")
    run(
        [sys.executable, "31-03_audit_products.py"],
        cwd=AUDITOR,
        env=env_audit,
    )

    products_dirs = sorted(
        audit_base.glob("audit_outputs_*"), reverse=True
    )
    if not products_dirs:
        log("ERROR: Could not find audit_outputs_* dir")
        sys.exit(1)
    products_dir = products_dirs[0]
    log(f"✓ Products audit → {products_dir}")

    return noproducts_dir, products_dir


def step_collate(noproducts_dir: Path, products_dir: Path) -> tuple[Path, Path]:
    """Run both collate scripts. Returns (noproducts_csv, products_csv)."""
    banner("STEP 5 — Collate: Without-Products")
    run(
        [sys.executable, "collate_without_products_audit_json_to_csv.py", str(noproducts_dir)],
        cwd=AUDITOR,
    )
    noproducts_csv = noproducts_dir / "audit_checks_collated.csv"
    log(f"✓ Collated (without-products) → {noproducts_csv}")

    banner("STEP 6 — Collate: Products")
    run(
        [sys.executable, "collate_audit_json_to_csv.py", str(products_dir)],
        cwd=AUDITOR,
    )
    products_csv = products_dir / "audit_checks_collated.csv"
    log(f"✓ Collated (products) → {products_csv}")

    return noproducts_csv, products_csv


def step_analyze(noproducts_csv: Path, products_csv: Path) -> None:
    """Run both analyze scripts."""
    banner("STEP 7 — Analyze: Without-Products")
    run(
        [sys.executable, "analyze_without_products_audit_report.py", str(noproducts_csv)],
        cwd=AUDITOR,
    )
    log(f"✓ Analysis (without-products) → {noproducts_csv.parent / 'without_products_analysis_report'}")

    banner("STEP 8 — Analyze: Products")
    run(
        [sys.executable, "analyze_audit_report.py", str(products_csv)],
        cwd=AUDITOR,
    )
    log(f"✓ Analysis (products) → {products_csv.parent / 'analysis_report'}")


# ─── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="PNS Audit Pipeline Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Steps:
  filter    Step 1  — Filter raw Kibana CSV
  extract   Step 2  — Run LLM extraction (pro + flash models)
  audit     Step 3+4 — Run both audit scripts
  collate   Step 5+6 — Collate audit JSONs to CSV
  analyze   Step 7+8 — Run analysis reports
  all       Run all steps in sequence (default)

Examples:
  python run_pipeline.py
  python run_pipeline.py --step extract
  python run_pipeline.py --step audit --pro-csv csv_batch_tool/extraction_output_30-04/google_gemini-2.5-pro/20260506_123154/output.csv --flash-csv csv_batch_tool/extraction_output_30-04/google_gemini-2.5-flash/20260506_120903/output.csv
  python run_pipeline.py --step collate --audit-dir-noproducts Auditor/audit_outputs/without_products_audit_outputs_20260507_120000 --audit-dir-products Auditor/audit_outputs/audit_outputs_20260507_120100
  python run_pipeline.py --step analyze --collated-noproducts Auditor/audit_outputs/without_products_audit_outputs_20260507_120000/audit_checks_collated.csv --collated-products Auditor/audit_outputs/audit_outputs_20260507_120100/audit_checks_collated.csv
""",
    )

    parser.add_argument(
        "--step",
        choices=["all", "filter", "extract", "audit", "collate", "analyze"],
        default="all",
        help="Which step(s) to run (default: all)",
    )

    # Optional overrides for individual steps
    parser.add_argument("--pro-csv", help="Path to pro model output.csv (for --step audit)")
    parser.add_argument("--flash-csv", help="Path to flash model output.csv (for --step audit)")
    parser.add_argument("--flash-json-csv", help="Path to user-provided Flash JSON CSV (to skip Flash extraction)")
    parser.add_argument("--skip-flash", action="store_true", help="Skip Flash Go extraction (use when Flash data comes from Redash via import_flash_data.py)")
    parser.add_argument("--audit-dir-noproducts", help="Path to without_products_audit dir (for --step collate)")
    parser.add_argument("--audit-dir-products", help="Path to products audit dir (for --step collate)")
    parser.add_argument("--collated-noproducts", help="Path to without-products audit_checks_collated.csv (for --step analyze)")
    parser.add_argument("--collated-products", help="Path to products audit_checks_collated.csv (for --step analyze)")

    args = parser.parse_args()
    cfg = load_config()
    step = args.step

    # ── Resolve output state tracking vars ────────────────────────────────────
    pro_csv: Path | None = None
    flash_csv: Path | None = None
    noproducts_dir: Path | None = None
    products_dir: Path | None = None
    noproducts_csv: Path | None = None
    products_csv: Path | None = None

    banner(f"PNS Audit Pipeline  —  step: {step.upper()}")
    log(f"Config: {CONFIG_FILE}")
    log(f"Prompts: {PROMPTS_FILE}")

    # ── Run selected step(s) ──────────────────────────────────────────────────
    if step in ("all", "filter"):
        step_filter(cfg)

    if step in ("all", "extract"):
        flash_json_csv = Path(args.flash_json_csv).resolve() if args.flash_json_csv else None
        pro_csv, flash_csv = step_extract(cfg, flash_json_csv=flash_json_csv, skip_flash=args.skip_flash)
        pro_csv, flash_csv = step_clean_errors(pro_csv, flash_csv)

    if step in ("all", "audit"):
        # If running standalone, require explicit paths or auto-detect latest
        if pro_csv is None:
            if args.pro_csv:
                pro_csv = Path(args.pro_csv).resolve()
            else:
                extraction_base = CSV_BATCH_TOOL / cfg["extraction_output_dir"]
                pro_csv = find_latest_output(extraction_base, cfg["pro_model"])
                if not pro_csv:
                    log("ERROR: --pro-csv not provided and no extraction output found. Run extract first.")
                    sys.exit(1)
                log(f"Auto-detected pro CSV: {pro_csv}")

        if flash_csv is None:
            if args.flash_csv:
                flash_csv = Path(args.flash_csv).resolve()
            else:
                # Default to the daily normalized Flash data
                flash_csv = CSV_BATCH_TOOL / "input" / "daily_flash_normalized.csv"

        noproducts_dir, products_dir = step_audit(cfg, pro_csv, flash_csv)

    if step in ("all", "collate"):
        if noproducts_dir is None:
            if args.audit_dir_noproducts:
                noproducts_dir = Path(args.audit_dir_noproducts).resolve()
            else:
                audit_base = AUDITOR / "audit_outputs"
                dirs = sorted(audit_base.glob("without_products_audit_outputs_*"), reverse=True)
                if not dirs:
                    log("ERROR: --audit-dir-noproducts not provided and no audit output found. Run audit first.")
                    sys.exit(1)
                noproducts_dir = dirs[0]
                log(f"Auto-detected without-products dir: {noproducts_dir}")

        if products_dir is None:
            if args.audit_dir_products:
                products_dir = Path(args.audit_dir_products).resolve()
            else:
                audit_base = AUDITOR / "audit_outputs"
                dirs = sorted(audit_base.glob("audit_outputs_*"), reverse=True)
                if not dirs:
                    log("ERROR: --audit-dir-products not provided and no audit output found. Run audit first.")
                    sys.exit(1)
                products_dir = dirs[0]
                log(f"Auto-detected products dir: {products_dir}")

        noproducts_csv, products_csv = step_collate(noproducts_dir, products_dir)

    if step in ("all", "analyze"):
        if noproducts_csv is None:
            if args.collated_noproducts:
                noproducts_csv = Path(args.collated_noproducts).resolve()
            else:
                audit_base = AUDITOR / "audit_outputs"
                dirs = sorted(audit_base.glob("without_products_audit_outputs_*"), reverse=True)
                if not dirs:
                    log("ERROR: --collated-noproducts not provided. Run collate first.")
                    sys.exit(1)
                noproducts_csv = dirs[0] / "audit_checks_collated.csv"
                log(f"Auto-detected without-products CSV: {noproducts_csv}")

        if products_csv is None:
            if args.collated_products:
                products_csv = Path(args.collated_products).resolve()
            else:
                audit_base = AUDITOR / "audit_outputs"
                dirs = sorted(audit_base.glob("audit_outputs_*"), reverse=True)
                if not dirs:
                    log("ERROR: --collated-products not provided. Run collate first.")
                    sys.exit(1)
                products_csv = dirs[0] / "audit_checks_collated.csv"
                log(f"Auto-detected products CSV: {products_csv}")

        step_analyze(noproducts_csv, products_csv)

    banner("PIPELINE COMPLETE ✓")


if __name__ == "__main__":
    main()
