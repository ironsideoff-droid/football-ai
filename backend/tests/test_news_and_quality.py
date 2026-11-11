import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from football_data.news import GoogleNewsRssProvider, parse_rss
from football_data.source_manager import ABSTAIN_BELOW, WEIGHTS, assess_quality, classify_source, news_freshness
from schemas.match import StandingsContext
from schemas.news import NewsBundle

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


def rss(items):
    body = "".join(
        f"<item><title>{t}</title><link>{l}</link><pubDate>{d}</pubDate><source url=\"{su}\">{s}</source></item>" for t, l, d, su, s in items)
    return f"<?xml version='1.0'?><rss><channel>{body}</channel></rss>"


def pub(days):
    return (NOW - timedelta(days=days)).strftime("%a, %d %b %Y %H:%M:%S GMT")


FEED = rss([
    ("Coach: &quot;we are ready&quot; - BBC Sport", "https://n/1", pub(1), "https://www.bbc.co.uk", "BBC Sport"),
    ("Coach: &quot;we are ready&quot; - BBC Sport", "https://n/1b", pub(1), "https://www.bbc.co.uk", "BBC Sport"),  # duplicate title
    ("Old story - Some Blog", "https://n/2", pub(40), "https://x.blogspot.com", "Some Blog"),  # too old
    ("Reddit thread on striker - Reddit", "https://n/3", pub(2), "https://reddit.com", "Reddit"),
])


def test_parse_rss_dedupes_filters_old_and_classifies():
    items = parse_rss(FEED, "home", "Home FC", NOW)
    assert [i.source for i in items] == ["BBC Sport", "Reddit"] and items[0].title == 'Coach: "we are ready"'
    assert items[0].reliability_class == "high" and items[1].reliability_class == "low"
    assert items[0].published_at.startswith("2026-11-07") and items[0].age_days == pytest.approx(1.0, abs=0.1) and items[0].item_id.startswith("h-")


def provider(handler):
    return GoogleNewsRssProvider(transport=httpx.MockTransport(handler))


def test_provider_fetches_both_teams(context):
    seen = []
    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, text=FEED.replace("pub", "pub"))
    # freshness depends on the real clock, so use a feed dated "now"
    fresh = rss([("Fresh news - BBC Sport", "https://n/9", datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT"), "https://bbc.com", "BBC Sport")])
    bundle = asyncio.run(provider(lambda r: (seen.append(str(r.url)), httpx.Response(200, text=fresh))[1]).fetch(context.match))
    assert bundle.status == "ok" and {i.team for i in bundle.items} == {"home", "away"}
    assert any("Home+FC" in u for u in seen) and any("Away+United" in u for u in seen)


@pytest.mark.parametrize("status,expected", [(500, "unavailable")])
def test_news_failure_is_reported_not_raised(context, status, expected):
    bundle = asyncio.run(provider(lambda r: httpx.Response(status)).fetch(context.match))
    assert bundle.status == expected and len(bundle.errors) == 2 and bundle.items == []


def test_news_partial_failure(context):
    fresh = rss([("Fresh - BBC Sport", "https://n/9", datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT"), "https://bbc.com", "BBC Sport")])
    handler = lambda r: httpx.Response(200, text=fresh) if "Home" in str(r.url) else httpx.Response(503)  # noqa: E731
    bundle = asyncio.run(provider(handler).fetch(context.match))
    assert bundle.status == "partial" and len(bundle.errors) == 1 and bundle.items


def test_malformed_feed_is_an_error(context):
    bundle = asyncio.run(provider(lambda r: httpx.Response(200, text="<html>not rss")).fetch(context.match))
    assert bundle.status == "unavailable"


# ---------------------------------------------------------------- source classification & freshness
def test_classification_and_freshness():
    assert classify_source("BBC Sport", "https://www.bbc.co.uk/sport") == "high"
    assert classify_source("Goal.com", None) == "medium" and classify_source("random", "https://x.example") == "unknown"
    assert classify_source("fan", "https://twitter.com/x") == "low"
    assert news_freshness((NOW - timedelta(hours=5)).isoformat(), NOW) == ("fresh", pytest.approx(0.21, abs=0.01))
    assert news_freshness((NOW - timedelta(days=30)).isoformat(), NOW)[0] == "stale" and news_freshness(None)[0] == "unknown"


# ---------------------------------------------------------------- data quality
def test_weights_sum_to_one():
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


def test_quality_reports_what_is_missing(context, news_bundle):
    q = assess_quality(context)
    kinds = {(i.kind, i.detail.split(":")[0]) for i in q.issues}
    assert not q.abstain_recommended and q.level in ("high", "medium") and q.components["standings"]
    assert ("missing", "Injuries and suspensions") in kinds and ("missing", "Confirmed lineups") in kinds and ("missing", "Expected goals (xG/xGA)") in kinds
    with_news = assess_quality(context, news_bundle)
    assert with_news.score == pytest.approx(q.score + WEIGHTS["news"]) and any(i.kind == "unverified" for i in with_news.issues)


def test_quality_flags_stale_form_conflicts_and_api_failures(context):
    context.home_form.rest_days = 80
    context.standings.home_team_overall.form = "L,L,L"
    context.data_availability["head_to_head"] = "unavailable: HTTP 500"
    context.head_to_head = None
    q = assess_quality(context, NewsBundle(status="unavailable", errors=["ConnectError"], retrieved_at="t"))
    kinds = {i.kind for i in q.issues}
    assert {"outdated", "conflict", "api_failure"} <= kinds and not q.components["head_to_head"]


def test_quality_recommends_abstaining_without_core_data(context):
    context.standings, context.home_form, context.away_form, context.head_to_head = None, None, None, None
    q = assess_quality(context)
    assert q.level == "insufficient" and q.abstain_recommended and q.score < ABSTAIN_BELOW and any(i.severity == "critical" for i in q.issues)


def test_form_only_data_is_still_enough(context):
    context.standings = None
    q = assess_quality(context)
    assert not q.abstain_recommended and not q.components["standings"] and any("league table" in i.detail for i in q.issues)
