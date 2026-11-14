from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from schemas.scout import Absence
from schemas.sources import Reliability


class NewsItem(BaseModel):
    item_id: str
    team: Literal["home", "away"]
    team_name: str
    title: str
    source: str
    source_url: Optional[str] = None
    link: str
    published_at: Optional[str] = None
    age_days: Optional[float] = None
    reliability_class: Reliability = "unknown"
    origin: Literal["rss", "web_research", "fan_social"] = "rss"  # web_research: a one-sentence claim written by a search agent, not a publisher headline


class NewsBundle(BaseModel):
    status: Literal["ok", "partial", "unavailable", "disabled"] = "unavailable"
    items: list[NewsItem] = []
    errors: list[str] = []
    retrieved_at: str = ""
    provider: str = ""
    absences: list[Absence] = []  # structured injury/suspension claims found by web research
