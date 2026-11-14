"""Deep search: four role-based headline engines, one per analysis role, 100-300 unique recent headlines in total.

  statistical   (Statistical Researcher)  numbers: xG, goals, assists, shots, records, streaks, league position
  intelligence  (Football Intelligence)   off-pitch: injuries, suspensions, press conferences, morale, manager, transfers
  tactical      (Agent 4 Data Scout)      lineups, predicted XI, formations, tactics, key players' goals/assists, rotation
  context       (Chief Predictor)         previews, expert predictions, referee, weather/pitch, stakes, derby, schedule, travel

Every engine runs many role-specific Google News RSS searches for both teams (headlines only; no article text), keeps only
headlines that mention one of the teams and are at most 30 days old, drops duplicates across all engines and caps each engine
at 75 items (4 x 75 = 300). Nothing is padded or invented: if fewer than 100 are found the summary says so.

The language models cannot read 300 headlines inside free-tier token limits, so each agent receives a compact digest of its own
engine (topic counts + the freshest, most reliable headlines); the full list is shown to the user.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import quote_plus

import httpx

from football_data.scout import norm_team
from football_data.source_manager import classify_source, news_freshness
from schemas.deep import DeepItem, DeepSearchSummary, EngineSummary
from schemas.match import MatchSummary

MAX_AGE_DAYS = 30
MIN_TARGET, MAX_TOTAL, PER_ENGINE_CAP = 100, 300, 75
CONCURRENCY = 6
CACHE_SECONDS = 1800

# {t} = the team, {o} = the opponent, {c} = the competition. "team" queries run once per team, "match" queries once per match.
ENGINES: dict[str, dict] = {
    "statistical": {
        "label": "Statistical Researcher", "focus": "xG, goals, assists, shots, records, streaks, table position",
        "team": ["{t} stats", "{t} expected goals xG", "{t} goals scored conceded form", "{t} top scorer assists season",
                 "{t} shots possession analysis", "{t} home record", "{t} away record", "{t} unbeaten run OR losing streak",
                 "{t} clean sheets defence record", "{t} league table position points", "{t} goals per game average"],
        "match": ["{t} {o} stats head to head", "{t} {o} statistics preview data"]},
    "intelligence": {
        "label": "Football Intelligence", "focus": "injuries, suspensions, press conferences, morale, manager, transfers",
        "team": ["{t} injury news", "{t} injured OR doubt OR ruled out", "{t} suspended OR suspension", "{t} press conference manager",
                 "{t} morale OR confidence", "{t} manager pressure OR sacked OR contract", "{t} transfer news", "{t} dressing room OR conflict",
                 "{t} captain OR star player says", "{t} club statement"],
        "match": []},
    "tactical": {
        "label": "Agent 4 · Data Scout", "focus": "lineups, predicted XI, formations, tactics, key players' goals and assists, rotation",
        "team": ["{t} lineup", "{t} predicted lineup", "{t} starting XI", "{t} formation", "{t} tactics analysis",
                 "{t} key player form goals assists", "{t} squad rotation", "{t} team news", "{t} defence midfield attack analysis"],
        "match": ["{t} {o} predicted lineups formations", "{t} {o} tactical preview"]},
    "context": {
        "label": "Chief Predictor", "focus": "previews, expert predictions, referee, weather and pitch, stakes, rivalry, schedule, travel",
        "team": ["{t} fixture congestion OR schedule OR rest", "{t} stakes title OR relegation OR qualification", "{t} travel OR away trip"],
        "match": ["{t} {o} preview", "{t} {o} prediction", "{t} {o} referee", "{t} {o} weather OR pitch", "{t} {o} derby OR rivalry",
                  "{t} {o} head to head history", "{c} {t} {o} what to expect", "{t} {o} how to watch OR kick-off"]},
}

TOPICS = [  # first match wins
    ("injuries", r"injur|ruled out|doubt|sidelined|fitness|knock|surgery|recover"),
    ("suspensions", r"suspend|suspension|ban\b|banned|red card|yellow card|sent off"),
    ("lineups", r"line-?ups?|starting xi|starting eleven|predicted xi|xi\b|team news|formation|3-4-3|4-3-3|4-2-3-1|4-4-2|3-5-2|rotation"),
    ("tactics", r"tactic|pressing|high line|low block|system|set[- ]piece|counter"),
    ("player_stats", r"top scorer|goals?\b.*assists?|assists?|golden boot|goal tally|clean sheets?|xg|xa\b|expected goals|shots?"),
    ("form_records", r"unbeaten|winning streak|losing streak|record|run of|form\b|points|table|standings|home record|away record"),
    ("manager_press", r"press conference|manager|coach|says|said|reveals|admits|warns|confident|morale|pressure|sack"),
    ("transfers", r"transfer|sign\b|signing|loan|bid\b|deal\b|contract|release clause"),
    ("referee_conditions", r"referee|var\b|weather|rain|pitch|stadium|wind|snow|heat|kick-?off time"),
    ("stakes_schedule", r"title|relegation|qualif|congestion|fixture|schedule|rest\b|travel|derby|rivalry|champions league|europa"),
    ("previews_predictions", r"preview|prediction|predict|odds|tips|how to watch|what to expect|head[- ]to[- ]head"),
]
_TOPIC_RE = [(name, re.compile(p, re.I)) for name, p in TOPICS]


def classify_topic(title: str) -> str:
    for name, rx in _TOPIC_RE:
        if rx.search(title):
            return name
    return "general"


def parse_rss_all(xml_text: str, now: Optional[datetime] = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    out: list[dict] = []
    for node in ET.fromstring(xml_text).iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        src = node.find("source")
        source = (src.text or "").strip() if src is not None and src.text else ""
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3].rstrip()
        if not title or not link:
            continue
        try:
            published = parsedate_to_datetime(node.findtext("pubDate") or "").astimezone(timezone.utc).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            published = None
        _, age = news_freshness(published, now)
        if age is not None and age > MAX_AGE_DAYS:
            continue
        out.append({"title": title, "link": link, "source": source or "unknown", "source_url": src.get("url") if src is not None else None,
                    "published_at": published, "age_days": age})
    return out


def _tokens(name: str) -> set[str]:
    return {t for t in norm_team(name).split() if len(t) >= 4} or set(norm_team(name).split())


def mentions(title: str, tokens: set[str]) -> bool:
    words = set(norm_team(title).split())
    return bool(tokens & words)


_REL = {"high": 3, "medium": 2, "unknown": 1, "low": 0}


class DeepSearch:
    def __init__(self, timeout: float = 12.0, transport: Optional[httpx.AsyncBaseTransport] = None, base_url: str = "https://news.google.com",
                 clock=time.monotonic):
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport, follow_redirects=True,
                                       headers={"User-Agent": "football-ai/2.0"})
        self._cache: dict[str, tuple[float, list[dict]]] = {}
        self._clock = clock

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _query(self, q: str, sem: asyncio.Semaphore) -> list[dict]:
        hit = self._cache.get(q)
        if hit and self._clock() - hit[0] < CACHE_SECONDS:
            return hit[1]
        async with sem:
            r = await self._http.get(f"/rss/search?q={quote_plus(q + f' when:{MAX_AGE_DAYS}d')}&hl=en-GB&gl=GB&ceid=GB:en")
        r.raise_for_status()
        rows = parse_rss_all(r.text)
        self._cache[q] = (self._clock(), rows)
        return rows

    async def run(self, match: MatchSummary) -> tuple[DeepSearchSummary, dict[str, list[DeepItem]]]:
        home, away, comp = match.home.name, match.away.name, match.competition or ""
        toks = {"home": _tokens(home), "away": _tokens(away)}
        sem = asyncio.Semaphore(CONCURRENCY)
        plan: list[tuple[str, str, str]] = []  # (engine, query, scope)
        fill = lambda tpl, t, o: re.sub(r"\s+", " ", tpl.replace("{t}", t).replace("{o}", o).replace("{c}", comp)).strip()  # noqa: E731
        for key, e in ENGINES.items():
            for tpl in e["team"]:
                for scope, t, o in (("home", home, away), ("away", away, home)):
                    plan.append((key, "football " + fill(tpl, f'"{t}"', f'"{o}"'), scope))
            for tpl in e["match"]:
                plan.append((key, "football " + fill(tpl, f'"{home}"', f'"{away}"'), "both"))
        results = await asyncio.gather(*(self._query(q, sem) for _, q, _ in plan), return_exceptions=True)

        summaries = {k: EngineSummary(key=k, label=e["label"], focus=e["focus"]) for k, e in ENGINES.items()}
        seen: set[str] = set()
        pool: dict[str, list[DeepItem]] = {k: [] for k in ENGINES}
        errors: list[str] = []
        for (key, _, scope), res in zip(plan, results):
            summaries[key].queries += 1
            if isinstance(res, BaseException):
                summaries[key].failed_queries += 1
                if len(errors) < 3:
                    errors.append(f"{type(res).__name__}: {str(res)[:100]}")
                continue
            for row in res:
                sides = [s for s in ("home", "away") if mentions(row["title"], toks[s])]
                if scope in ("home", "away"):
                    if scope not in sides:
                        continue  # the headline does not mention the team this query was about
                elif not sides:
                    continue
                norm = re.sub(r"\W+", " ", row["title"].lower())[:90]
                if norm in seen:
                    continue
                seen.add(norm)
                team = sides[0] if len(sides) == 1 else "both"
                pool[key].append(DeepItem(
                    item_id=f"d{key[0]}-{hashlib.sha1((row['link'] + row['title']).encode()).hexdigest()[:8]}", engine=key,
                    topic=classify_topic(row["title"]), team=team, title=row["title"][:220], source=row["source"][:80], link=row["link"],
                    published_at=row["published_at"], age_days=row["age_days"],
                    reliability_class=classify_source(row["source"], row.get("source_url"))))

        kept: dict[str, list[DeepItem]] = {}
        for key, items in pool.items():
            items.sort(key=lambda i: (-_REL[i.reliability_class] * 3 + (i.age_days if i.age_days is not None else 99)))
            kept[key] = items[:PER_ENGINE_CAP]
            summaries[key].found = len(kept[key])
            for i in kept[key]:
                summaries[key].topics[i.topic] = summaries[key].topics.get(i.topic, 0) + 1
        everything = sorted((i for v in kept.values() for i in v), key=lambda i: i.published_at or "", reverse=True)[:MAX_TOTAL]
        topics: dict[str, int] = {}
        for i in everything:
            topics[i.topic] = topics.get(i.topic, 0) + 1
        notes = []
        failed = sum(s.failed_queries for s in summaries.values())
        if failed:
            notes.append(f"{failed} of {len(plan)} searches failed ({'; '.join(errors)}).")
        if len(everything) < MIN_TARGET:
            notes.append(f"Only {len(everything)} relevant headlines exist for this match (target {MIN_TARGET}+); nothing was padded or invented.")
        summary = DeepSearchSummary(total=len(everything), minimum_target=MIN_TARGET, maximum=MAX_TOTAL, below_target=len(everything) < MIN_TARGET,
                                    queries=len(plan), failed_queries=failed, engines=list(summaries.values()), topics=topics, notes=notes,
                                    items=everything)
        return summary, kept


def digest(summary: DeepSearchSummary, items: list[DeepItem], limit: int = 24) -> dict:
    """What one agent receives: counts per topic for its engine + its freshest, most reliable headlines (ids let findings cite them)."""
    topics: dict[str, int] = {}
    for i in items:
        topics[i.topic] = topics.get(i.topic, 0) + 1
    return {"headlines_found": len(items), "topics": topics,
            "items": [{"id": i.item_id, "topic": i.topic, "title": i.title[:110], "source": i.source[:30], "reliability": i.reliability_class}
                      for i in items[:limit]]}
