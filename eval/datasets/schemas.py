"""
schemas.py — Pydantic schemas for each golden-dataset example type.

Every dataset is JSONL of {id, inputs, reference, metadata}. These models validate
shape + enums; referential checks (machine ids exist, pages in range, SQL read-only)
live in build/validate_datasets.py. extra="allow" on references so case-specific
hint flags (e.g. expect_low_confidence) don't trip validation.
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict

ALLOWED_SOURCES = {
    "lulzbot_mini_user_manual.pdf",
    "lulzbot_taz6_user_manual.pdf",
    "lulzbot_taz_workhorse_user_manual.pdf",
    "lulzbot_taz_pro_user_manual.pdf",
    "niosh_safe_3d_printing_2024-103.pdf",
}
INTENTS = {"troubleshoot", "analytics", "manage_incident", "general"}
MANAGE_ACTIONS = {"close", "assign", "update_comment", "unsupported"}


class PageRef(BaseModel):
    source_file: str
    page_start: int
    page_end: int


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    metadata: dict = {}


class TroubleshootExample(_Base):
    class _In(BaseModel):
        machine_id: str
        mvc_code: str
        symptom: str
    class _Ref(BaseModel):
        model_config = ConfigDict(extra="allow")
        needs_technician: Optional[bool] = None
        cited_pages: List[PageRef] = []
    inputs: _In
    reference: _Ref


class RetrievalExample(_Base):
    class _In(BaseModel):
        query: str
        mvc_code: Optional[str] = None
        k: int = 5
    class _Ref(BaseModel):
        model_config = ConfigDict(extra="allow")
        relevant: List[PageRef]
    inputs: _In
    reference: _Ref


class SqlExample(_Base):
    class _In(BaseModel):
        question: str
    class _Ref(BaseModel):
        model_config = ConfigDict(extra="allow")
        gold_sql: Optional[str] = None
        must_be_readonly: bool = True
        must_not_reference: List[str] = []
    inputs: _In
    reference: _Ref


class RoutingExample(_Base):
    class _In(BaseModel):
        utterance: str
    class _Ref(BaseModel):
        model_config = ConfigDict(extra="allow")
        intent: str
        machine_id: Optional[str] = None
    inputs: _In
    reference: _Ref


class SafetyExample(_Base):
    class _In(BaseModel):
        utterance: str
    class _Ref(BaseModel):
        model_config = ConfigDict(extra="allow")
        input_safe: bool
        category: str
    inputs: _In
    reference: _Ref


class ManageExample(_Base):
    class _In(BaseModel):
        model_config = ConfigDict(extra="allow")
        utterance: str
        incident_id: Optional[str] = None
    class _Ref(BaseModel):
        model_config = ConfigDict(extra="allow")
        action: Optional[str] = None
    inputs: _In
    reference: _Ref


# filename -> (model, langsmith dataset name)
DATASETS = {
    "troubleshoot_cases.jsonl": (TroubleshootExample, "fdm-troubleshoot"),
    "retrieval_labels.jsonl": (RetrievalExample, "fdm-retrieval"),
    "sql_cases.jsonl": (SqlExample, "fdm-sql"),
    "routing_cases.jsonl": (RoutingExample, "fdm-routing"),
    "safety_redteam.jsonl": (SafetyExample, "fdm-safety"),
    "manage_cases.jsonl": (ManageExample, "fdm-manage"),
}
