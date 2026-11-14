from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from schemas.sources import Reliability


class DeepItem(BaseModel):
    item_id: str
    engine: str  # statistical | intelligence | tactical | context
    topic: str
    team: Literal["home", "away", "both"] = "both"
    title: str
    source: str
    link: str
    published_at: Optional[str] = None
    age_days: Optional[float] = None
    reliability_class: Reliability = "unknown"


class EngineSummary(BaseModel):
    key: str
    label: str  # which agent owns this engine
    focus: str  # what it searches for
    queries: int = 0
    failed_queries: int = 0
    found: int = 0  # unique, relevant, recent headlines kept for this engine
    topics: dict[str, int] = {}


class DeepSearchSummary(BaseModel):
    total: int = 0
    minimum_target: int = 100
    maximum: int = 300
    below_target: bool = False
    queries: int = 0
    failed_queries: int = 0
    engines: list[EngineSummary] = []
    topics: dict[str, int] = {}
    notes: list[str] = []
    items: list[DeepItem] = []
