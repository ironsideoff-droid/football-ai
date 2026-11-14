# TACTIQ (formerly FOOTBALL AI) — 2.0

A personal football match prediction app. Three independent Groq LLM agents analyse a fixture; the Chief Predictor
then runs a vectorised NumPy Monte Carlo of **exactly 50,000 simulated matches** with uncertain parameters, scenarios
and a minute-by-minute match model, and writes the final prediction. No database, no login, runs locally.

```
Browser (React) ──/api──▶ FastAPI
                            ├─▶ football-data.org   fixtures, results, standings, head-to-head        (real data only)
                            ├─▶ Google News RSS     headlines + publisher + publication time          (real data only)
                            │
                            ├─▶ Agent 1  Statistical Researcher        (Groq key 1) ┐ run concurrently
                            ├─▶ Agent 2  Football Intelligence & News  (Groq key 2) ┘
                            └─▶ Agent 3  Chief Predictor               (Groq key 3)
                                  phase 1: audits both reports, proposes bounded adjustments + matchup scenarios
                                  engine : 50,000 vectorised simulations (parameter engine → scenario engine → goal model)
                                  phase 2: interprets the simulation, writes the final prediction
                                  backend: caps confidence by data quality, stores the prediction once (immutable)
```

## Requirements
Python 3.10+, Node.js 18+, three Groq API keys (one per account), a free football-data.org token.

## Install and run
```bash
# backend
cd backend
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # Windows: copy .env.example .env  → fill in the keys
uvicorn main:app --reload                             # http://127.0.0.1:8000

# frontend (second terminal)
cd frontend
npm install
npm run dev                                           # http://localhost:5173
```
Pick a match, press **Analyze**. Tests: `cd backend && pytest` (all external APIs and LLM answers are mocked; no paid calls).

## Configuration (`backend/.env`)
See `.env.example`: `GROQ_API_KEY_1..3`, `GROQ_MODEL_1..3`, `FOOTBALL_API_KEY`, `NEWS_ENABLED`, `GOAL_MODEL`
(`poisson` default), `DC_RHO`, `BP_SHARE`, `PREDICTIONS_DIR`. `GET /api/health` shows what is configured (never the keys).

## Changes from v1
| Area | v1 | 2.0 |
|---|---|---|
| Agent 2 | Tactical Analyst (general knowledge) | **Football Intelligence & News Analyst**: reads real headlines, classifies claims, validated by the backend |
| Simulation | 1,000 matches, 2 fixed expected-goals values | **50,000** matches, every match draws its own correlated parameters, scenario mixture, 6 time segments |
| Parameters | chosen by the LLM | **derived from real results by the backend**; the LLM may only propose bounded, evidenced adjustments |
| Data | form, standings, h2h | + rest days and congestion, source registry, data-quality score, conflict/outdated/missing checks, **abstention** |
| Evaluation | none | immutable prediction store, Brier / log loss / calibration / MAE / exact score, walk-forward backtest |
| UI | prediction + analysis | + scenarios with conditional results, score heat-map, parameter uncertainty, news findings with sources, data quality, track record |

## The three agents
1. **Statistical Researcher** receives only backend-computed facts (last results, goals, home/away splits, rest days, standings,
   head-to-head) plus the source registry. Every finding cites a `source_id`; the backend replaces the model's reliability with the
   registry's. xG, shots, possession, injuries and lineups are not provided by football-data.org, so they are reported as *unavailable*.
2. **Football Intelligence & News Analyst** receives real headlines (publisher, publication time, age, publisher reliability class).
   Each finding must reference a supplied `item_id`; the backend then fills source, link and date itself, drops findings that
   cite unknown items, and removes any `exact_quote` that does not appear verbatim in the headline. Claim types: confirmed fact,
   credible report, opinion, rumour, unverified. Reliability is capped by publisher class and claim type (an opinion can never
   look like a fact). If no news is available the agent is not called and the report says so.
   News text is untrusted data; the prompts tell the agents never to follow instructions found inside it.
3. **Chief Predictor** audits both reports, proposes adjustments and scenarios (phase 1), then interprets the simulation and
   produces the prediction (phase 2). It may abstain. It never picks the base numbers.

## Dynamic parameter model
For one match, with `g` = league goals per team per game and `H` = home advantage:

```
λ_home = g · ATT_home · CONC_away · H          λ_away = g · ATT_away · CONC_home / H
```
* **Team strengths** use a Poisson–Gamma model. With prior Gamma(k·g, k) (k = 8 pseudo-games at league average) and `goals` in
  `games` games, the posterior is Gamma(k·g + goals, k + games). The relative rate has mean (k·g+goals)/((k+games)·g) and
  coefficient of variation 1/√shape; it is sampled log-normally with the same mean and CV (positive, multiplicative).
* **Recent form** adds a bounded correction of weight 0.25·n/(n+5) (recent matches are already inside the season totals, so only a
  fraction is added back: avoids double counting).
* **Home advantage** `H = √(league home goals / league away goals)`, uncertainty from the goal counts. If league home/away data is
  missing, a stated assumption is used and flagged.
* `g` is log-normal with sd 1/√(league goals).
* All strengths are drawn together from one multivariate normal in log space (Cholesky). The only correlation imposed is the
  *openness correlation* between a team's attack and conceding, which defaults to 0 and is used only if the Chief supplies evidence.
* **Adjustments** from the Chief are capped by evidence type (statistical 0.15, intelligence 0.08, tactical 0.10, environmental 0.10,
  speculation 0 in log units, 0.25 total per parameter), need a justification, and are recorded as applied / capped / ignored.
  These caps are guard-rails I chose, not fitted values.

## Scenario engine
A scenario is a *regime* that scales the two teams' scoring rates (e.g. possession-dominant home side vs low block). Rules:
probabilities are normalised to 100%; any remainder goes to an automatic "ordinary conditions" scenario; each of the 50,000 matches
is assigned to exactly one scenario by largest-remainder allocation (rare scenarios stay rare); effects are capped by evidence
type; **scenarios describing events the simulator already generates (red cards, early/late goals, going behind, protecting a lead,
chasing an equaliser) are rejected to avoid double counting**. Results are reported per scenario: share, 1X2, goals, BTTS, over 2.5,
likely scorelines.

## Goal model (time-aware)
Each match has six 15-minute segments. In segment `s`, each team's goals are Poisson with rate

```
λ · w_s · state_multiplier · red_card_multiplier
```
`w_s` = share of goals per segment (0.142, 0.158, 0.166, 0.166, 0.175, 0.193); trailing teams get +6% per goal behind, leading teams
−4% per goal ahead (capped at 2 goals); a red card (4.5% per team per match) multiplies the scoring rate by 0.70 for that team
and 1.30 for the opponent. Optional extensions: Dixon–Coles low-score correction (importance resampling) and bivariate Poisson
(shared shock). **These timing and event constants are approximations from the football-analytics literature, not fitted to your
data**; they are printed with every result. They are not renormalised, so average simulated goals can differ slightly from the
input expected goals (both are shown).

## Exactly 50,000 simulations
`simulation/monte_carlo.py` defines `N_SIMULATIONS = 50_000`, generates all arrays at once with NumPy and raises an error if the
result does not contain exactly that many matches. There is **no Python loop over simulations**: the only loop in the whole engine
runs over the 6 time segments (each step acts on all 50,000 matches at once); other loops run over a handful of adjustments or
scenarios. Tests enforce this by parsing the source. The seed is stored with every result, so a run is reproducible. On this
machine a full simulation takes about 0.05 s.

## Evaluation and backtesting
* **Immutable history.** `POST /api/analyze` stores the Chief's prediction once, as append-only JSON lines (`data_store/`),
  with timestamp and SHA-256 digest. A second analysis never overwrites it; a match that has kicked off is refused;
  `verify()` reports tampering. Results are stored separately, once per match. Nothing is retrained from a single result.
* **Metrics** (`/api/evaluation`): 1X2 accuracy, Brier, log loss; home/away scoring; total goals; BTTS; over/under 2.5; MAE of
  team goals; exact score; calibration curves; ROI only if odds were recorded (none are; the free API has no odds). Each market is
  judged separately, so "predicted 1–2, actual 1–1" correctly scores BTTS and one team's goals as right and the outcome as wrong.
  The Chief is compared with the raw simulation and with always predicting the league base rates. Use **Check finished matches**
  in the UI (or `POST /api/evaluation/refresh`) to record results. Small samples are flagged; do not read anything into < 50 matches.
* **Backtest** (`GET /api/backtest?competition=PL`): walk-forward on real finished matches (each match predicted only from earlier
  ones) comparing Poisson, Dixon–Coles, bivariate and a naive baseline. A complex model is recommended only if it beats Poisson by
  more than two standard errors; otherwise Poisson stays. This evaluates the goal models only, **not** the LLM agents, news or scenarios.

**No claim is made that 2.0 predicts better than v1.** That can only be shown by out-of-sample results, which the track record
and backtest will accumulate.

## API
`GET /api/health` · `GET /api/fixtures?days=14&competitions=PL,PD` (up to 30 days; split into 10-day requests) · `GET /api/matches/{id}` · `POST /api/analyze` ·
`POST /api/analyze/start` + `GET /api/jobs/{id}` (progress, used by the UI) · `GET /api/predictions` · `GET /api/evaluation` ·
`POST /api/evaluation/refresh` · `GET /api/backtest`. Errors: `{"error": {"code", "message", "agent"}}`.

## Tests
`cd backend && pytest` (122 tests): each agent, Groq errors, football-API errors, news failures, invalid/missing data, parameter
sampling and correlations, scenario normalisation, scenario-conditioned results, exactly 50,000 simulations, vectorisation
(AST check plus an RNG-call-count check), goal distributions, outcome aggregation, probability normalisation, metrics,
backtesting without look-ahead, store immutability, API error handling, the full workflow.

## Known limitations
* **Not tested live.** The build environment could not reach Groq, football-data.org or Google News, so every automated test uses mocks.
  The first real run is the real integration test; if a provider response differs from what the parsers expect, the analysis degrades to
  "unavailable" for that source instead of inventing data.
* football-data.org (free tier) has no injuries, suspensions, lineups, xG, shots, possession or odds; these are reported as missing and lower confidence.
  Its limit is 10 requests/minute (one analysis uses about 5; results are cached for 5 minutes).
* International matches: football-data.org's free plan includes only the World Cup and the European Championship, so other international fixtures (Nations League, qualifiers, friendlies) cannot be listed. Adding them needs another data provider (`FootballProvider` is the extension point).
* News = **headlines only** from an aggregator, not article bodies; nothing is independently verified, so exact quotes are limited to what a headline contains.
* Time-segment and event constants are literature approximations; adjustment and scenario caps are guard-rails, not fitted.
* The LLM agents are not covered by the backtest; their value shows only in the track record, which needs many finished matches.
* Prediction history is stored on local disk. On hosts with an ephemeral disk (e.g. Render free plan) it is lost on redeploy.
* The `Dockerfile` and `render.yaml` from v1 are kept; deployment is optional.

## Accounts and subscription (9.99 EUR / month)

* **Login:** Supabase Auth (email + password). The browser gets the public Supabase URL and publishable key from `/api/config`; the backend verifies every request's access token with Supabase.
* **Database:** table `tactiq_subscriptions` (one row per user, row-level security: users can only read their own row; only the backend writes) and `tactiq_stripe_events` (webhook de-duplication). The SQL is in `supabase/migrations/`.
* **Payments:** Stripe Checkout in subscription mode (9.99 EUR per month, created inline unless `STRIPE_PRICE_ID` is set) and the Stripe customer portal for cancelling or changing the card.
* **Access:** every data and analysis endpoint needs a signed-in user with an `active` or `trialing` subscription. The server fails closed: without Supabase configuration it refuses requests. `AUTH_DISABLED=true` is for local development only.
* **Subscription state** is always re-read from Stripe when a webhook arrives, so event order does not matter. If a webhook is late, the app asks Stripe directly after you return from checkout.

Stripe webhook: add the endpoint `https://<your-app>/api/billing/webhook` with the events `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`, and put its signing secret in `STRIPE_WEBHOOK_SECRET`.

## Data coverage

* **football-data.org** (free plan): fixtures, results, league tables (including group tables for cups) and head-to-head. Requests are
  spaced to stay under the 10-per-minute limit, rate-limit answers are retried, and tables and team results are cached for 30 minutes.
* **News:** three Google News RSS searches per team (general, injuries and suspensions, team news and lineups). Headlines only.
* **Web research (optional, `WEB_RESEARCH=true`):** a Groq model with `browser_search` looks for recent injuries, suspensions and lineups and
  returns short claims with URLs. A claim is dropped if it has no URL or its URL does not appear in what the search returned, and claims are
  marked as lower-reliability summaries for the agents. If it fails (rate limit, timeout) the analysis simply continues without it.
* **Not available from any free source used here:** xG, shots and possession. The app says so in the data-quality report and never invents them.
  Confirmed lineups appear on the pitch view only if the football data provider supplies them.

## Agent 4: Data Scout

Fills gaps football-data.org leaves open, and always shows where each number came from:

| Data | Source | Notes |
|---|---|---|
| Confirmed lineups + formations | ESPN public site API | usually published about an hour before kickoff |
| Shots, shots on target, possession | ESPN, averaged over the teams' last finished matches | |
| xG / xGA (last 6 matches), league home/away goal averages | Understat | big-5 leagues only |
| Injuries / suspensions | web research (Groq `browser_search`, `GROQ_API_KEY_4`) | every claim needs a URL that appears in the search evidence; shown as unverified |

These are unofficial endpoints and their format can change without notice; a source that fails is reported in the progress panel
("Agent 4: Data Scout") and the analysis continues without it. Check each site's terms of use before running this commercially.
Set `SCOUT_ENABLED=false` to turn scraping off.
