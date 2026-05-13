from pydantic import BaseModel
from typing import Optional, Literal

Verdict = Literal["MATCH", "MISMATCH", "PARTIAL", "MISSING", "HALLUCINATED"]


class NormalizedValue(BaseModel):
    original:      str            # serialize to string before sending — no Any
    numeric:       Optional[str] = None   # store as string "100.0" not float
    numeric_low:   Optional[str] = None
    numeric_high:  Optional[str] = None
    unit:          Optional[str] = None
    parse_note:    Optional[str] = None


class FieldAuditResult(BaseModel):
    field_path:       str
    ground_truth:     str          # serialize both sides to str before API call
    flash_output:     str
    gt_normalized:    Optional[NormalizedValue] = None
    flash_normalized: Optional[NormalizedValue] = None
    verdict:          Verdict
    reason:           str
    context_note:     Optional[str] = None


class FlatAuditReport(BaseModel):
    buyer_details:  list[FieldAuditResult]
    seller_details: list[FieldAuditResult]
    payment:        list[FieldAuditResult]
    metadata:       list[FieldAuditResult]
    moq:            list[FieldAuditResult]
    next_steps:     list[FieldAuditResult]