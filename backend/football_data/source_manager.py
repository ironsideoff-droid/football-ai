"""Source tracking and data-quality assessment.

Every external fact the agents see is tied to a SourceRecord (name, URL, retrieval time, publication time,
reliability class, freshness). `assess_quality` turns what is and is not available into a score, a list of issues
(missing / outdated / conflicting / unverified / API failure) and a recommendation to abstain.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from schemas.match import MatchContext
from schemas.news import NewsBundle
from schemas.sources import DataIssue, DataQualityReport, Freshness, Reliability, SourceRecord

# Editable heuristic: outlets that are official data providers, wire agencies or established national media.
# Everything else is "unknown" (not "low"): an unfamiliar domain is not evidence of unreliability.
HIGH_RELIABILITY = (
    "football-data.org", "reuters.com", "apnews.com", "bbc.co.uk", "bbc.com", "theguardian.com", "espn.com",
    "skysports.com", "uefa.com", "fifa.com", "premierleague.com", "laliga.com", "bundesliga.com", "legaseriea.it",
    "ligue1.com", "theathletic.com", "nytimes.com", "gazzetta.it", "marca.com", "lequipe.fr", "kicker.de",
)
MEDIUM_RELIABILITY = (
    "goal.com", "90min.com", "transfermarkt", "sportingnews.com", "foxsports.com", "cbssports.com", "as.com",
    "mundodeportivo.com", "corrieredellosport.it", "tuttosport.com", "dailymail.co.uk", "telegraph.co.uk",
)
LOW_RELIABILITY = ("reddit.com", "twitter.com", "x.com", "facebook.com", "tiktok.com", "medium.com", "blogspot.")

ABSTAIN_BELOW = 0.30
STALE_FORM_DAYS = 45
STALE_NEWS_DAYS = 21
FRESH_NEWS_HOURS = 72

# weight of each piece of information in the quality score (sums to 1.0)
WEIGHTS = {
    "home_form": 0.20, "away_form": 0.20, "standings": 0.20, "head_to_head": 0.08, "news": 0.07,
    "injuries_suspensions": 0.05, "lineups": 0.05, "xg": 0.05, "shots_possession": 0.05, "rest_congestion": 0.05,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def classify_source(name: str = "", url: Optional[str] = None) -> Reliability:
    haystack = f"{urlparse(url).netloc if url else ''} {name}".lower()
    for needles, level in ((LOW_RELIABILITY, "low"), (HIGH_RELIABILITY, "high"), (MEDIUM_RELIABILITY, "medium")):
        if any(n in haystack for n in needles):
            return level  # type: ignore[return-value]
    return "unknown"


def news_freshness(published_at: Optional[str], now: Optional[datetime] = None) -> tuple[Freshness, Optional[float]]:
    if not published_at:
        return "unknown", None
    now = now or datetime.now(timezone.utc)
    try:
        age = (now - datetime.fromisoformat(published_at.replace("Z", "+00:00"))).total_seconds() / 3600
    except ValueError:
        return "unknown", None
    days = round(age / 24, 2)
    if age <= FRESH_NEWS_HOURS:
        return "fresh", days
    return ("stale" if days > STALE_NEWS_DAYS else "fresh"), days


def api_source(sid: str, name: str, url: str, status: str = "ok", note: Optional[str] = None) -> SourceRecord:
    return SourceRecord(
        id=sid, name=name, url=url, retrieved_at=now_iso(), reliability="high",
        freshness="live" if status == "ok" else "unknown", status=status, note=note,  # type: ignore[arg-type]
    )


def assess_quality(ctx: MatchContext, news: Optional[NewsBundle] = None) -> DataQualityReport:
    issues: list[DataIssue] = []
    have = {k: False for k in WEIGHTS}

    def add(kind, severity, detail):
        issues.append(DataIssue(kind=kind, severity=severity, detail=detail))

    for side, form in (("home", ctx.home_form), ("away", ctx.away_form)):
        if form and form.matches_considered >= 3:
            have[f"{side}_form"] = True
            if form.rest_days is not None and form.rest_days > STALE_FORM_DAYS:
                add("outdated", "warning", f"{form.team}: last finished match was {form.rest_days:.0f} days before kickoff.")
        elif form and form.matches_considered > 0:
            add("missing", "warning", f"Only {form.matches_considered} finished matches found for the {side} team.")
        else:
            add("missing", "critical", f"No recent finished matches available for the {side} team.")
    have["rest_congestion"] = have["home_form"] and have["away_form"]

    st = ctx.standings
    have["standings"] = bool(st and st.home_team_overall and st.away_team_overall)
    if not have["standings"]:
        add("missing", "warning", "No league table for this competition (cup, group stage or API limit): season strength falls back to recent matches only.")
    elif not st.league_avg_home_goals:  # type: ignore[union-attr]
        add("missing", "info", "League home/away goal averages unavailable: home advantage uses a stated assumption.")

    have["head_to_head"] = bool(ctx.head_to_head and ctx.head_to_head.matches_considered > 0)
    if not have["head_to_head"]:
        add("missing", "info", "No head-to-head history available.")

    for key, text in (
        ("injuries_suspensions", "Injuries and suspensions: not provided by the football data API."),
        ("lineups", "Confirmed lineups: not provided by the football data API."),
        ("xg", "Expected goals (xG/xGA): not provided by the football data API."),
        ("shots_possession", "Shots, shots on target and possession: not provided by the football data API."),
    ):
        add("missing", "info", text)

    if news is None or news.status in ("disabled", "unavailable") or not news.items:
        reason = "disabled" if news and news.status == "disabled" else "unavailable or empty"
        add("api_failure" if news and news.status == "unavailable" and news.errors else "missing", "info", f"News: {reason}." + (f" {'; '.join(news.errors[:2])}" if news and news.errors else ""))
    else:
        have["news"] = True
        add("unverified", "info", "News items are headlines from an aggregator; none is independently verified.")
        if news.status == "partial":
            add("api_failure", "warning", "News retrieval succeeded for only one of the two teams.")

    for k, v in ctx.data_availability.items():
        if v.startswith("unavailable") and k in ("head_to_head", "home_recent_matches", "away_recent_matches", "standings") and "no league table" not in v:
            add("api_failure", "warning", f"{k.replace('_', ' ')}: {v.replace('unavailable: ', '')}")

    # conflict check: standings form string vs the results we computed ourselves
    for row, form in ((st.home_team_overall if st else None, ctx.home_form), (st.away_team_overall if st else None, ctx.away_form)):
        if row and row.form and form and form.sequence_oldest_to_newest:
            theirs = row.form.replace(",", "")[-3:]
            ours = form.sequence_oldest_to_newest[-len(theirs):]
            if theirs and theirs != ours[::-1] and theirs != ours:
                add("conflict", "warning", f"{form.team}: standings form '{row.form}' differs from computed recent results '{form.sequence_oldest_to_newest}'.")

    score = round(sum(w for k, w in WEIGHTS.items() if have[k]), 3)
    critical = any(i.severity == "critical" for i in issues)
    both_forms = have["home_form"] and have["away_form"]
    insufficient = score < ABSTAIN_BELOW or (not both_forms and not have["standings"])
    level = "insufficient" if insufficient else "high" if score >= 0.7 else "medium" if score >= 0.45 else "low"
    return DataQualityReport(
        score=score, level=level, issues=issues, abstain_recommended=insufficient or (critical and not have["standings"]),
        components=have,
    )
