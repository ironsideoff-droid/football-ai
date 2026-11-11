"""Shared fixtures. All AI and football-API calls are mocked: tests never hit the network."""
from __future__ import annotations

import pytest

from football_data.source_manager import assess_quality
from schemas.match import (FormSplit, H2HSummary, MatchContext, MatchSummary, StandingRow, StandingsContext, TeamForm,
                           TeamRef)
from schemas.news import NewsBundle, NewsItem

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
                  "home_away_performance": "Strong at home", "rest_and_congestion": "6 days rest",
                  "player_availability": {"status": "unavailable", "injuries": [], "suspensions": [], "notes": ""}},
    "away_team": {"name": "Away United", "league_position": 9, "recent_form": "LDWLD"},
    "head_to_head": "Home FC won 3 of the last 5",
    "key_findings": [{"statement": "Home FC unbeaten at home", "source_id": "standings", "reliability": "High"}, "plain string finding"],
    "advanced_stats_status": "xG, shots, possession not provided",
    "data_quality": {"level": "Medium", "missing_data": ["injuries"], "notes": ""},
    "uncertainties": ["No injury data"],
}
INTEL_JSON = {
    "status": "ok",
    "home": {"team": "Home FC", "summary": "Calm", "morale_and_pressure": "manager secure",
             "findings": [
                 {"item_id": "h-1", "claim_type": "Confirmed_Fact", "category": "club_news", "summary": "Striker returns to training",
                  "exact_quote": "back in training", "reliability_score": 0.8, "relevance": "attack", "uncertainty": "Low"},
                 {"item_id": "h-1", "claim_type": "opinion", "summary": "Pundit thinks the club is in crisis",
                  "exact_quote": "a club in crisis", "reliability_score": 0.3, "uncertainty": "high"},
                 {"item_id": "h-999", "claim_type": "rumour", "summary": "invented", "reliability_score": 0.9}]},
    "away": {"team": "Away United", "summary": "No usable news", "findings": []},
    "cross_cutting": [], "overall_relevance": "minor",
    "data_quality": {"level": "medium", "missing_data": [], "notes": ""},
}
PLAN_JSON = {
    "report_assessment": {"contradictions": ["tactical xG gap"], "unsupported_claims": ["claimed injury"],
                          "accepted_conclusions": ["home strength"], "rejected_conclusions": ["injury claim"],
                          "missing_information": ["lineups"], "data_quality_notes": "no injuries data"},
    "abstain": False,
    "adjustments": [
        {"target": "home_attack", "log_effect": 0.04, "uncertainty_sd": 0.03, "evidence": "intelligence",
         "justification": "striker back in training (news h-1)", "source_refs": ["h-1"]},
        {"target": "away_attack", "log_effect": -0.2, "evidence": "speculation", "justification": "gut feeling"}],
    "scenarios": [
        {"name": "Home dominates possession", "description": "Away sits in a low block", "probability": 30,
         "assumptions": ["away low block"], "impact_on_teams": "fewer away chances", "home_log_effect": 0.05,
         "away_log_effect": -0.08, "uncertainty_sd": 0.05, "evidence": "tactical"},
        {"name": "Red card for either side", "description": "Sent off early", "probability": 5, "home_log_effect": 0.2},
        {"name": "Ordinary match conditions", "description": "Nothing special", "probability": 65}],
    "parameter_notes": "adjustments are small",
}
FINAL_JSON = {
    "predicted_score": {"home": 2, "away": 1},
    "probabilities": {"home_win": 50, "draw": 25, "away_win": 25},
    "over_under_2_5": {"over_2_5": 50, "under_2_5": 50},
    "both_teams_to_score": {"yes": 50, "no": 50},
    "most_likely_scorelines": ["1-0", "2-1", "1-1"],
    "explanation": "Home side is stronger.", "simulation_interpretation": "Sim agrees.",
    "scenario_commentary": "Possession scenario helps the hosts.", "parameter_commentary": "Priors from season data.",
    "confidence": {"level": "high", "score": 95, "explanation": "limited data", "main_uncertainties": ["injuries"]},
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


def _split(p, w, d, l, gf, ga):
    return FormSplit(played=p, won=w, drawn=d, lost=l, goals_for=gf, goals_against=ga)


def make_form(name, gf, ga, n=10):
    return TeamForm(team=name, matches_considered=n, overall=_split(n, 5, 3, 2, gf, ga), home=_split(5, 3, 1, 1, gf // 2, ga // 2),
                    away=_split(5, 2, 2, 1, gf - gf // 2, ga - ga // 2), points=18, points_per_game=1.8, avg_goals_for=gf / n,
                    avg_goals_against=ga / n, sequence_oldest_to_newest="WWDLWDWLDW", last_match_date="2026-11-03T15:00:00Z", rest_days=7.0)


def _row(pos, name, gf, ga, played=12):
    return StandingRow(position=pos, team=name, played=played, won=6, draw=3, lost=3, points=21, goals_for=gf, goals_against=ga,
                       goal_difference=gf - ga)


@pytest.fixture
def context() -> MatchContext:
    match = MatchSummary(
        id=100, utc_date="2099-11-10T15:00:00Z", status="TIMED", competition="Premier League",
        competition_code="PL", home=TeamRef(id=1, name="Home FC"), away=TeamRef(id=2, name="Away United"),
    )
    standings = StandingsContext(
        competition="Premier League", table_size=20, league_games=120, league_total_goals=330, league_avg_goals_per_team=1.375,
        league_avg_home_goals=1.55, league_avg_away_goals=1.2, league_home_goals_total=186, league_away_goals_total=144,
        home_team_overall=_row(3, "Home FC", 24, 11), away_team_overall=_row(9, "Away United", 14, 17))
    ctx = MatchContext(match=match, standings=standings, home_form=make_form("Home FC", 18, 9), away_form=make_form("Away United", 11, 14),
                       head_to_head=H2HSummary(matches_considered=5, upcoming_home_team_wins=3, draws=1, upcoming_away_team_wins=1,
                                               upcoming_home_team_goals=8, upcoming_away_team_goals=4),
                       data_availability={"injuries_suspensions": "unavailable"}, retrieved_at="2026-11-08T10:00:00+00:00")
    ctx.data_quality = assess_quality(ctx)
    return ctx


@pytest.fixture
def news_bundle() -> NewsBundle:
    item = NewsItem(item_id="h-1", team="home", team_name="Home FC", title="Striker is back in training before derby",
                    source="BBC Sport", link="https://example.com/a", published_at="2026-11-07T09:00:00+00:00", age_days=1.0,
                    reliability_class="high")
    return NewsBundle(status="ok", items=[item], retrieved_at="2026-11-08T10:00:00+00:00", provider="test")
