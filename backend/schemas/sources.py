from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

Reliability = Literal["high", "medium", "low", "unknown"]
Freshness = Literal["live", "fresh", "stale", "unknown"]


class SourceRecord(BaseModel):
    id: str
    name: str
    url: Optional[str] = None
    retrieved_at: str
    published_at: Optional[str] = None
    reliability: Reliability = "unknown"
    freshness: Freshness = "unknown"
    status: Literal["ok", "failed", "unavailable"] = "ok"
    note: Optional[str] = None


class DataIssue(BaseModel):
    kind: Literal["missing", "outdated", "conflict", "unverified", "api_failure"]
    severity: Literal["info", "warning", "critical"]
    detail: str


class DataQualityReport(BaseModel):
    score: float  # 0..1, share of the weighted information checklist that is actually available
    level: Literal["high", "medium", "low", "insufficient"]
    issues: list[DataIssue] = []
    abstain_recommended: bool = False
    components: dict[str, bool] = {}
