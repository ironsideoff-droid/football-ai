"""Shared fixtures. All AI and football-API calls are mocked: tests never hit the network."""
from __future__ import annotations

import pytest

from schemas.match import MatchContext, MatchSummary, TeamRef

TEAM = lambda i, n: {"id": i, "name": n, "shortName": n, "tla": n[:3].upper(), "crest": None}  # noqa: E731


def raw_match(mid, d, home, away, hs, as_, status="FINISHED"):
    return {
        "id": mid, "utcDate": f"{d}T15:00:00Z", "status": status, "matchday": 5,
        "competition": {"name": "Premier League", "code": "PL"},
        "homeTeam": home, "awayTeam": away,
        "score": {"fullTime": {"home": hs, "away": as_}},
    }


H, A = TEAM(1, "Home FC"), TEAM(2, "Away United")
OTHER = TEAM(3, "Other Town")

RESEARCHER_JSON = {
    "home_team": {"name": "Home FC", "league_position": 3, "recent_form": "WWDLW",
                  "offensive_stats": {"avg_goals_for": 1.8}, "defensive_stats": {"avg_goals_against": 0.9},
                  "home_away_performance": "Strong at home",
                  "player_availability": {"status": "unavailable", "injuries": [], "suspensions": [], "notes": ""}},
    "away_team": {"name": "Away United", "league_position": 9, "recent_form": "LDWLD"},
    "head_to_head": "Home FC won 3 of the last 5",
    "key_findings": ["Home FC unbeaten at home"],
    "data_quality": {"level": "Medium", "missing_data": ["injuries"], "notes": ""},
    "sources": [{"name": "football-data.org", "timestamp": "2026-11-08T10:00:00+00:00"}],
    "uncertainties": ["No injury data"],
}
TACTICAL_JSON = {
    "home_profile": {"style": "possession", "formation": "4-3-3", "basis": "mixed"},
    "away_profile": {"style": "counter", "formation": "4-4-2", "basis": "model_knowledge"},
    "key_matchups": ["Wingers vs full-backs"],
    "scoring_expectations": {"home_goals": 1.7, "away_goals": 0.9, "reasoning": "home attack vs weak defence"},
    "confidence": {"level": "medium", "uncertainties": ["style is partly general knowledge"]},
}
PLAN_JSON = {
    "report_assessment": {"contradictions": ["xG gap larger in tactical report"],
                          "unsupported_claims": ["claimed injury to striker"],
                          "accepted_conclusions": ["home strength"], "rejected_conclusions": ["injury claim"],
                          "data_quality_notes": "no injuries data"},
    "simulation_params": {"home_xg": 1.65, "away_xg": 0.95, "uncertainty_sigma": 0.25,
                          "goal_correlation": 0.05, "rationale": "from form + tactical edge", "key_inputs": ["form"]},
}
FINAL_JSON = {
    "predicted_score": {"home": 2, "away": 1},
    "probabilities": {"home_win": 50, "draw": 25, "away_win": 25},
    "over_under_2_5": {"over_2_5": 50, "under_2_5": 50},
    "both_teams_to_score": {"yes": 50, "no": 50},
    "most_likely_scorelines": ["1-0", "2-1", "1-1"],
    "explanation": "Home side is stronger.", "simulation_interpretation": "Sim agrees.",
    "confidence": {"level": "medium", "score": 55, "explanation": "limited data", "main_uncertainties": ["injuries"]},
}


class FakeGroq:
    """Stands in for GroqClient; returns queued JSON dicts (or raises queued exceptions)."""

    def __init__(self, name, responses, model="fake-model"):
        self.name, self.model, self.responses, self.calls = name, model, list(responses), []

    async def chat_json(self, system, user, **kw):
        self.calls.append((system, user))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def context() -> MatchContext:
    match = MatchSummary(
        id=100, utc_date="2026-11-10T15:00:00Z", status="TIMED", competition="Premier League",
        competition_code="PL", home=TeamRef(id=1, name="Home FC"), away=TeamRef(id=2, name="Away United"),
    )
    return MatchContext(match=match, data_availability={"injuries_suspensions": "unavailable"},
                        retrieved_at="2026-11-08T10:00:00+00:00")
