from pydantic import BaseModel
from typing import Optional, Literal, Any

Verdict = Literal["MATCH", "MISMATCH", "PARTIAL", "MISSING", "HALLUCINATED"]


class NormalizedValue(BaseModel):
    """
    Populated whenever raw value needed parsing before comparison.
    e.g. "100 kg" → numeric=100.0, unit="kg"
    e.g. "₹2500"  → numeric=2500.0, unit=null
    e.g. "5-7"    → numeric_low=5.0, numeric_high=7.0
    """
    original:      str
    numeric:       Optional[float] = None
    numeric_low:   Optional[float] = None   # for ranges
    numeric_high:  Optional[float] = None   # for ranges
    unit:          Optional[str]  = None
    parse_note:    Optional[str]  = None    # e.g. "unit extracted from value string"


class FieldAuditResult(BaseModel):
    field_path:        str
    ground_truth:      str
    flash_output:      str
    gt_normalized:     Optional[NormalizedValue] = None   # populated if parsing was needed
    flash_normalized:  Optional[NormalizedValue] = None   # populated if parsing was needed
    verdict:           Verdict
    reason:            str
    context_note:      Optional[str] = None  # e.g. "value derivable from GT product name"


class SpecAudit(BaseModel):
    spec_name:                   str
    resolved_spec_name:          Optional[str] = None    # canonical name after synonym resolution
    match_status:                Literal["MATCHED", "MISSING_IN_FLASH", "EXTRA_IN_FLASH"]
    derived_from_gt_product_name: bool = False           # True = spec was embedded in GT name
    synonym_resolved:            bool = False            # True = spec names were synonyms
    dimension_split:             bool = False            # True = one spec split into multiple
    fields:                      list[FieldAuditResult]


class PriceAudit(BaseModel):
    fields:                list[FieldAuditResult]
    unit_equivalence_note: Optional[str] = None  # e.g. "50/kg == 5000/quintal confirmed"


class ProductAudit(BaseModel):
    gt_index:                 int
    flash_index:              int        # -1 = not found
    match_status:             Literal["MATCHED", "MISSING_IN_FLASH", "EXTRA_IN_FLASH"]
    alignment_note:           str
    name_embedded_attributes: list[str]  # e.g. ["material:MS", "form:Pipe", "commodity:Iron"]
    split_merge_note:         Optional[str] = None
    context_group_id:         Optional[str] = None
    fields:                   list[FieldAuditResult]
    price:                    PriceAudit
    specifications:           list[SpecAudit]


class ProductsAuditReport(BaseModel):
    """Output schema for the Products Agent only."""
    products: list[ProductAudit]