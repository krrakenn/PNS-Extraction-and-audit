from __future__ import annotations

import csv
import json
import os
import random
import sys
import re
import time
import warnings
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from dotenv import load_dotenv
from httpcore import RemoteProtocolError
from httpx import ReadError
from openai import APIError, APIConnectionError, APITimeoutError

from audit_pydantic import FlatAuditReport

warnings.filterwarnings("ignore")
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "csv_batch_tool", ".env")
load_dotenv(env_path, override=True)
load_dotenv()

from langfuse import Langfuse
lf_client = Langfuse()

try:
    from langfuse.openai import OpenAI as LangfuseOpenAI

    _LANGFUSE_OPENAI_WRAPPER = True
except ImportError:  # pragma: no cover
    from openai import OpenAI as LangfuseOpenAI

    _LANGFUSE_OPENAI_WRAPPER = False
    print(
        "[Langfuse] pip install langfuse for tracing; using plain OpenAI client.",
        flush=True,
    )

MODEL = (os.environ.get("PNS_AUDIT_MODEL") or "google/gemini-2.5-pro").strip()
API_KEY = (os.environ.get("IMLLM_API_KEY") or "").strip()

IMLLM_BASE_URL = "https://imllm.intermesh.net"
client = LangfuseOpenAI(api_key=API_KEY, base_url=IMLLM_BASE_URL)


def _audit_log(msg: str) -> None:
    print(f"[PNS Audit] {msg}", flush=True)


def validate_api_response(response_text: str) -> bool:
    if not response_text or not response_text.strip():
        return False
    try:
        json.loads(response_text)
        return True
    except json.JSONDecodeError:
        return False


def _safe_filename(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", s.strip())
    return s.strip("_") or "output"


def _extract_llm_name_from_path(csv_path: str) -> str:
    parts = Path(csv_path).parts
    lowered = [p.lower() for p in parts]
    if "extraction_output" in lowered:
        idx = lowered.index("extraction_output")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return Path(csv_path).parent.name or "llm"


def _normalize_colname(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.strip().lower())


def _find_required_columns(fieldnames: List[str]) -> Tuple[str, str, Optional[str]]:
    norm = {_normalize_colname(c): c for c in fieldnames}

    url_candidates = ["recordingurl", "recording_url", "callrecordingurl", "recordinglink", "recording"]
    thinking_candidates = ["thinkingjson", "thinking_json", "thinking", "reasoningjson", "reasoning_json"]
    id_candidates = ["fileid", "file_id", "id", "file"]

    url_col = None
    for k in url_candidates:
        kk = _normalize_colname(k)
        if kk in norm:
            url_col = norm[kk]
            break

    thinking_col = None
    for k in thinking_candidates:
        kk = _normalize_colname(k)
        if kk in norm:
            thinking_col = norm[kk]
            break

    id_col = None
    for k in id_candidates:
        kk = _normalize_colname(k)
        if kk in norm:
            id_col = norm[kk]
            break

    if not url_col or not thinking_col:
        raise ValueError(
            f"Could not find required columns. Found columns: {fieldnames}. "
            f"Need something like recording_url + thinking_json."
        )
    return url_col, thinking_col, id_col


def _read_csv_mapping(csv_path: str) -> Dict[str, Dict[str, str]]:
    """Reads CSV and returns {key: {url: ..., thinking: ..., id: ...}}"""
    _audit_log(f"Reading CSV mapping: {csv_path}")
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"No headers found in CSV: {csv_path}")
        
        url_col, thinking_col, id_col = _find_required_columns(reader.fieldnames)
        _audit_log(f"  columns: url={url_col!r} thinking={thinking_col!r} id={id_col!r}")
        
        out: Dict[str, Dict[str, str]] = {}
        for row in reader:
            url = (row.get(url_col) or "").strip()
            thinking = (row.get(thinking_col) or "").strip()
            fid = (row.get(id_col) or "").strip() if id_col else ""
            
            # Key is FID if exists, otherwise URL
            key = fid if fid else url
            if key and thinking:
                out[key] = {"url": url, "thinking": thinking, "fid": fid}
        
        _audit_log(f"  loaded {len(out)} records with non-empty thinking_json using key alignment")
        return out


def call_llm_structured(
    log_dir: str,
    contents: str,
    model: str = MODEL,
    retries: int = 5,
    base_delay: float = 1.0,
) -> str:
    if not API_KEY:
        print("[LLM Gateway] WARNING: IMLLM_API_KEY not set; request may fail.")

    prompt_chars = len(contents)
    _audit_log(f"LLM call: model={model!r} prompt_chars={prompt_chars} log_dir={log_dir}")

    for attempt in range(1, retries + 1):
        max_delay = 300
        wait = min(base_delay * (2 ** (attempt - 1)) * (1 + random.random() * 0.1), max_delay)
        print(f"[LLM Gateway] Attempt {attempt}/{retries} (backoff up to {max_delay}s if needed)", flush=True)
        try:
            request_kwargs = dict(
                model=model,
                messages=[{"role": "user", "content": contents}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "pns_audit_output", "schema": FlatAuditReport.model_json_schema()},
                }
                # reasoning_effort="high",
            )
            if _LANGFUSE_OPENAI_WRAPPER:
                request_kwargs["name"] = "pns-audit-structured"

            resp = client.chat.completions.create(**request_kwargs)
            text = ""
            if resp and getattr(resp, "choices", None):
                choice0 = resp.choices[0]
                text = getattr(getattr(choice0, "message", None), "content", "") or ""

            os.makedirs(log_dir, exist_ok=True)
            usage = getattr(resp, "usage", None)
            with open(os.path.join(log_dir, "usage_metadata.json"), "w", encoding="utf-8") as uf:
                json.dump(
                    {
                        "completion_tokens": getattr(usage, "completion_tokens", None) if usage else None,
                        "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
                        "total_tokens": getattr(usage, "total_tokens", None) if usage else None,
                    },
                    uf,
                    indent=2,
                    default=str,
                )

            if not validate_api_response(text):
                raise json.JSONDecodeError("Invalid JSON response", text or "", 0)

            if usage is not None:
                print(
                    "[LLM Gateway] OK "
                    f"prompt_tokens={getattr(usage, 'prompt_tokens', None)} "
                    f"completion_tokens={getattr(usage, 'completion_tokens', None)} "
                    f"total_tokens={getattr(usage, 'total_tokens', None)} "
                    f"response_chars={len(text)}",
                    flush=True,
                )
            else:
                print(f"[LLM Gateway] OK response_chars={len(text)} (no usage metadata)", flush=True)
            return text

        except (ReadError, RemoteProtocolError, APIConnectionError, APITimeoutError, APIError, json.JSONDecodeError) as e:
            print(f"[LLM Gateway] Error on attempt {attempt}/{retries}: {str(e)[:200]}")
            if attempt == retries:
                raise
            print(f"[LLM Gateway] Sleeping {wait:.1f}s before retry…", flush=True)
            time.sleep(wait)

    raise RuntimeError(f"LLM Gateway call failed after {retries} attempts")



def _is_real_value(v) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return False
        if s.lower() in {"n/a", "na", "unknown", "null", "none"}:
            return False
        return True
    if isinstance(v, (list, tuple, set)):
        return len(v) > 0
    if isinstance(v, dict):
        return len(v) > 0
    return True


def _collect_paths_with_real_values(obj, prefix: str = "") -> set[str]:
    out: set[str] = set()

    def join(pfx: str, key: str) -> str:
        return f"{pfx}.{key}" if pfx else key

    if isinstance(obj, dict):
        for k, v in obj.items():
            p = join(prefix, str(k))
            if isinstance(v, (dict, list)):
                out |= _collect_paths_with_real_values(v, p)
            else:
                if _is_real_value(v):
                    out.add(p)
        return out

    if isinstance(obj, list):
        for i, v in enumerate(obj):
            p = f"{prefix}[{i}]"
            if isinstance(v, (dict, list)):
                out |= _collect_paths_with_real_values(v, p)
            else:
                if _is_real_value(v):
                    out.add(p)
        return out

    if _is_real_value(obj) and prefix:
        out.add(prefix)
    return out


_INDEX_RE = re.compile(r"\[\d+\]")


def _wildcard_indices(path: str) -> str:
    return _INDEX_RE.sub("[*]", path)


def _build_top_level_field_checklist(gt_obj: dict, flash_obj: dict) -> dict[str, list[str]]:
    gt_paths = {_wildcard_indices(p) for p in _collect_paths_with_real_values(gt_obj)}
    flash_paths = {_wildcard_indices(p) for p in _collect_paths_with_real_values(flash_obj)}
    all_paths = sorted(gt_paths | flash_paths)

    grouped: dict[str, list[str]] = {}
    for p in all_paths:
        top = p.split(".", 1)[0]
        grouped.setdefault(top, []).append(p)

    # Always call these out (commonly missed, high-signal)
    must_check = [
        "products[*].most_specific_category.name",
        "products[*].most_specific_category.reason",
        "products[*].specifications[*].buyer_requested",
    ]
    grouped.setdefault("products", [])
    for p in must_check:
        if p not in grouped["products"]:
            grouped["products"].append(p)
    grouped["products"] = sorted(set(grouped["products"]))

    return grouped


def _render_field_checklist(grouped: dict[str, list[str]]) -> str:
    ordered_sections = [
        "buyer_details",
        "seller_details",
        "payment",
        "metadata",
        "moq",
        "minimum_order_quantity",
        "next_steps",
        "products",
    ]
    keys = [k for k in ordered_sections if k in grouped] + [k for k in sorted(grouped.keys()) if k not in ordered_sections]

    lines = ["{"]
    for idx, k in enumerate(keys):
        paths = grouped.get(k) or []
        cap = 250
        shown = paths[:cap]
        more = len(paths) - len(shown)
        arr_items = [json.dumps(p) for p in shown]
        if more > 0:
            arr_items.append(json.dumps(f"... +{more} more"))
        comma = "," if idx < len(keys) - 1 else ""
        lines.append(f'  "{k}": [{", ".join(arr_items)}]{comma}')
    lines.append("}")
    return "\n".join(lines)


def build_audit_prompt(
    recording_url: str,
    llm_1_name: str,
    llm_2_name: str,
    thinking_1: str,
    thinking_2: str,
    field_checklist_grouped: dict[str, list[str]] | None = None,
) -> str:
    checklist_block = ""
    if field_checklist_grouped:
        checklist_block = f"""
## FIELD CHECKLIST (grouped by top-level section)

You MUST produce a field judgement for every path below where at least one side has a real value.
If you omit any listed real-value field, that is a critical error.

{_render_field_checklist(field_checklist_grouped)}
""".strip()

    # Fetch prompt from Langfuse
    try:
        prompt = lf_client.get_prompt("general_audit_prompt")
        return prompt.compile(
            checklist_block=checklist_block,
            thinking_1=thinking_1,
            thinking_2=thinking_2,
            llm_1_name=llm_1_name,
            llm_2_name=llm_2_name
        )
    except Exception as e:
        _audit_log(f"CRITICAL ERROR: Failed to load general_audit_prompt from Langfuse: {e}")
        sys.exit(1)



def _load_existing_results(results_csv: str) -> Dict[str, str]:
    if not os.path.exists(results_csv):
        _audit_log(f"No existing results CSV (will create): {results_csv}")
        return {}
    _audit_log(f"Loading existing URL→id map from: {results_csv}")
    with open(results_csv, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return {}
        url_col = None
        id_col = None
        for c in reader.fieldnames:
            if _normalize_colname(c) == "recordingurl":
                url_col = c
            if _normalize_colname(c) == "uniqueid":
                id_col = c
        if not url_col or not id_col:
            _audit_log("  results CSV missing recording_url or unique_id column; starting fresh ids")
            return {}
        out: Dict[str, str] = {}
        for row in reader:
            url = (row.get(url_col) or "").strip()
            uid = (row.get(id_col) or "").strip()
            if url and uid:
                out[url] = uid
        _audit_log(f"  loaded {len(out)} existing recording_url → unique_id mappings")
        return out


def _next_unique_id(existing_ids: Iterable[str]) -> int:
    max_n = 0
    for uid in existing_ids:
        m = re.match(r"^U(\d+)$", uid.strip())
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n + 1


def run_audit(
    csv_1: str,
    csv_2: str,
    output_dir: str,
    results_csv: str,
    max_urls: Optional[int] = None,
) -> None:
    _audit_log("Starting run_audit")
    _audit_log(f"  MODEL={MODEL!r}")
    _audit_log(f"  csv_1={csv_1}")
    _audit_log(f"  csv_2={csv_2}")
    _audit_log(f"  output_dir={output_dir}")
    _audit_log(f"  results_csv={results_csv}")
    _audit_log(f"  max_urls={max_urls!r} (None = no cap)")

    llm_1 = _extract_llm_name_from_path(csv_1)
    llm_2 = _extract_llm_name_from_path(csv_2)
    _audit_log(f"  derived llm names: {llm_1!r} vs {llm_2!r}")

    m1 = _read_csv_mapping(csv_1)
    m2 = _read_csv_mapping(csv_2)

    common_keys = sorted(set(m1.keys()) & set(m2.keys()))
    only_1 = len(set(m1.keys()) - set(m2.keys()))
    only_2 = len(set(m2.keys()) - set(m1.keys()))
    _audit_log(
        f"Record overlap: common={len(common_keys)} only_in_csv1={only_1} only_in_csv2={only_2}"
    )
    if not common_keys:
        raise ValueError("No matching records (file_id or recording_url) found across both CSVs.")

    common_before_limit = len(common_keys)
    if max_urls is not None:
        if max_urls < 1:
            raise ValueError("max_urls must be >= 1 when set")
        if common_before_limit > max_urls:
            common_keys = common_keys[:max_urls]
            _audit_log(
                f"Processing first {max_urls} of {common_before_limit} common record(s) (sorted order)"
            )

    os.makedirs(output_dir, exist_ok=True)

    existing_url_to_id = _load_existing_results(results_csv)
    next_n = _next_unique_id(existing_url_to_id.values())
    _audit_log(f"Next unique id counter starts at U{next_n:06d} (after existing max)")

    thinking_col_1 = f"thinking_json_{llm_1}"
    thinking_col_2 = f"thinking_json_{llm_2}"

    results_exists = os.path.exists(results_csv)
    with open(results_csv, "a", encoding="utf-8", newline="") as f_out:
        writer = csv.DictWriter(
            f_out,
            fieldnames=["unique_id", "recording_url", thinking_col_1, thinking_col_2, "output_json"],
        )
        if not results_exists:
            writer.writeheader()
            _audit_log(f"Created results CSV header: {results_csv}")

        total = len(common_keys)
        for idx, key in enumerate(common_keys, start=1):
            data1 = m1[key]
            data2 = m2[key]
            url = data1["url"]
            fid = data1["fid"]

            unique_id = existing_url_to_id.get(url)
            if not unique_id:
                unique_id = f"U{next_n:06d}"
                next_n += 1
                existing_url_to_id[url] = unique_id

            thinking_1 = data1["thinking"]
            thinking_2 = data2["thinking"]

            per_url_dir = os.path.join(output_dir, _safe_filename(unique_id))
            os.makedirs(per_url_dir, exist_ok=True)

            _audit_log(
                f"[{idx}/{total}] unique_id={unique_id} key={key} "
                f"thinking_lens=({len(thinking_1)},{len(thinking_2)}) chars"
            )
            _audit_log(f"  recording_url={url[:120]}{'…' if len(url) > 120 else ''}")

            try:
                gt_obj = json.loads(thinking_1) if thinking_1 else {}
            except Exception:
                gt_obj = {}
            try:
                flash_obj = json.loads(thinking_2) if thinking_2 else {}
            except Exception:
                flash_obj = {}
            checklist = _build_top_level_field_checklist(
                gt_obj if isinstance(gt_obj, dict) else {},
                flash_obj if isinstance(flash_obj, dict) else {},
            )

            prompt_text = build_audit_prompt(url, llm_1, llm_2, thinking_1, thinking_2, field_checklist_grouped=checklist)
            with open(os.path.join(per_url_dir, "prompt.txt"), "w", encoding="utf-8") as pf:
                pf.write(prompt_text)

            output_json_text = call_llm_structured(log_dir=per_url_dir, contents=prompt_text)

            out_path = os.path.join(per_url_dir, f"{unique_id}_audit.json")
            with open(out_path, "w", encoding="utf-8") as jf:
                try:
                    json.dump(json.loads(output_json_text), jf, indent=2, ensure_ascii=False)
                except Exception:
                    jf.write(output_json_text)


            _audit_log(f"  wrote {out_path} and appended row to {results_csv}")

            writer.writerow(
                {
                    "unique_id": unique_id,
                    "recording_url": url,
                    thinking_col_1: thinking_1,
                    thinking_col_2: thinking_2,
                    "output_json": output_json_text,
                }
            )
            f_out.flush()


    _audit_log(f"Finished run_audit: processed {total} recording_url(s)")


def _resolve_max_urls_from_prompt() -> Optional[int]:
    """Ask how many URLs to process when running interactively."""
    try:
        line = input("How many recording URLs to process? [Enter = all]: ").strip()
    except EOFError:
        return None
    if not line:
        return None
    try:
        n = int(line)
    except ValueError:
        _audit_log("Not an integer; processing all URLs.")
        return None
    if n < 1:
        _audit_log("Use a positive integer; processing all URLs.")
        return None
    return n


def main() -> None:
    # ========= Path Resolution =========
    # Priority: Environment Variables -> Hardcoded Defaults
    CSV_1 = os.environ.get("PNS_CSV_1")
    if not CSV_1:
        CSV_1 = r"C:\Users\Imart\Desktop\PNS Audit\csv_batch_tool\extraction_output_30-04\google_gemini-2.5-pro\20260511_104855\output.csv"

    CSV_2 = os.environ.get("PNS_CSV_2")
    if not CSV_2:
        CSV_2 = r"C:\Users\Imart\Desktop\PNS Audit\csv_batch_tool\extraction_output_30-04\imported_flash\output.csv"

    BASE_OUTPUT_DIR = os.environ.get("PNS_AUDIT_OUTPUT_DIR") or r"C:\Users\Imart\Desktop\PNS Audit\Auditor\audit_outputs"
    run_ts = time.strftime("%Y%m%d_%H%M%S")
    OUTPUT_DIR = os.path.join(BASE_OUTPUT_DIR, f"without_products_audit_outputs_{run_ts}")
    RESULTS_CSV = os.path.join(OUTPUT_DIR, "results.csv")

    # Cap overlap URLs
    MAX_URLS_TO_PROCESS: Optional[int] = 210
    max_urls = MAX_URLS_TO_PROCESS
    if max_urls is None and sys.stdin.isatty():
        max_urls = _resolve_max_urls_from_prompt()

    _audit_log("main() starting")
    _audit_log(f"CSV 1 (Pro): {CSV_1}")
    _audit_log(f"CSV 2 (Flash): {CSV_2}")
    _audit_log(f"Run output folder: {OUTPUT_DIR}")
    run_audit(
        csv_1=CSV_1,
        csv_2=CSV_2,
        output_dir=OUTPUT_DIR,
        results_csv=RESULTS_CSV,
        max_urls=max_urls,
    )
    if _LANGFUSE_OPENAI_WRAPPER:
        try:
            from langfuse import get_client

            get_client().flush()
            _audit_log("Langfuse events flushed")
        except Exception as e:
            _audit_log(f"Langfuse flush skipped: {e!s}")
    _audit_log("main() complete — DONE")


if __name__ == "__main__":
    main()
