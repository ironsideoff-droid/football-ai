# FOOTBALL AI 2.0

Football match predictions as a paid web app (EUR 9.99 / month). Three independent Groq LLM agents analyse a fixture; the
Chief Predictor then runs a vectorised NumPy Monte Carlo of **exactly 50,000 simulated matches** with uncertain parameters,
scenarios and a minute-by-minute match model, and writes the final prediction. Every prediction is stored once, before
kickoff, and can never be edited; the track record is public.

```
Browser (React, Render static site) ──Bearer JWT──▶ FastAPI (Render web service)
   │  sign-in: Supabase Auth                          ├─▶ Supabase Postgres  predictions · results · analyses · subscriptions
   │  pay: Stripe Checkout (hosted)                   ├─▶ Stripe             checkout, billing portal, signed webhooks
   └──────────────────────────────────────────────────├─▶ football-data.org  fixtures, results, standings, head-to-head
                                                      ├─▶ Google News RSS    headlines + publisher + publication time
                                                      ├─▶ Agent 1  Statistical Researcher        (Groq key 1) ┐ run concurrently
                                                      ├─▶ Agent 2  Football Intelligence & News  (Groq key 2) ┘
                                                      └─▶ Agent 3  Chief Predictor               (Groq key 3)
                                                            phase 1: audits both reports, proposes bounded adjustments + scenarios
                                                            engine : 50,000 vectorised simulations
                                                            phase 2: interprets the simulation, writes the final prediction
                                                            backend: caps confidence by data quality, stores the prediction once
Render cron job (every 3 h) ── records the final score of every predicted match
```

## Who can do what
| | Visitor | Free account | Subscriber | Operator (`X-Admin-Secret`) |
|---|---|---|---|---|
| Track record (`/api/evaluation`) | yes | yes | yes | yes |
| Upcoming fixtures | | yes | yes | yes |
| Run / open analyses, prediction list | | | yes | |
| Record results, backtest | | | | yes |

* A subscription is active when Stripe says `active` or `trialing`; it is written **only** by signature-verified webhooks.
  Returning from Checkout grants nothing by itself; the UI waits for the webhook.
* **One analysis per match.** The first subscriber to analyse a fixture runs the agents; the full analysis is stored with the
  immutable prediction and served to everyone after that: no extra LLM calls, no allowance used, and everyone sees the analysis
  that is actually tracked. New analyses are limited per user per UTC day (`DAILY_ANALYSIS_LIMIT`, default 30); a failed
  analysis gives the allowance back. At most `MAX_CONCURRENT_ANALYSES` run at once, and a user can have one running job.

## Deploy to production
Do it in **test mode** first (Stripe test keys, a test card), then switch the keys.

1. **Supabase** (a new project)
   * Apply `supabase/migrations/20261112000000_production_schema.sql` (SQL editor, or `supabase db push`). It creates the tables,
     makes the prediction history append-only with triggers, and locks every table away from Supabase's public REST API (RLS on, no policies).
   * Authentication > Sign In / Providers: keep Email on and **Confirm email** on. URL Configuration: set *Site URL* to your web app URL and add it to *Redirect URLs*.
   * Collect: `SUPABASE_URL`, the **publishable (anon) key** (for the web app), and the **Transaction pooler** connection string
     (Connect > port 6543) as `DATABASE_URL`. Never put the service-role key or the database password in the web app.
     New projects sign tokens with asymmetric keys; nothing else is needed. Only old projects with the legacy HS256 secret need `SUPABASE_JWT_SECRET`.
2. **Stripe**
   * Product catalogue: a product with a **recurring EUR 9.99 / month** price. Its id (`price_...`) is `STRIPE_PRICE_ID`.
   * Settings > Billing > Customer portal: enable it (cancel subscription, update payment method, invoices).
   * Developers > Webhooks: add `https://<api domain>/api/stripe/webhook` with the events `checkout.session.completed`,
     `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`. Its signing secret is `STRIPE_WEBHOOK_SECRET`.
   * EU customers: decide on VAT. Turn on Stripe Tax, set the price's tax behaviour, then set `STRIPE_AUTOMATIC_TAX=true`.
3. **Render**: New > Blueprint > this repository (`render.yaml`). It creates the API (Docker), the web app (static site) and the results cron job,
   and asks for the secrets. Because each service needs the other's URL, create it once, then fill `APP_URL` and `CORS_ORIGINS`
   (the web app's URL) on the API and `VITE_API_URL` (the API's URL) on the web app, and redeploy. Custom domains make this tidy.
   `GET /api/health` returns 503 `misconfigured` if a required setting is missing, so a bad deploy fails its health check instead of going live broken.
4. Sign up in the deployed app, subscribe with Stripe's test card `4242 4242 4242 4242`, confirm the button unlocks within a few seconds, analyse a match, cancel in the portal.

### Before you take real money
* **Terms of Service, Privacy Policy and a refund / right-of-withdrawal policy** for EU consumers. Set `VITE_TERMS_URL` / `VITE_PRIVACY_URL` and the footer links appear. The footer already carries a "not betting advice" disclaimer.
* **Data licences.** football-data.org ties an API key to one application and domain, requires the visible line "Football data provided by the Football-Data.org API" (in the footer) and says team logos belong to their owners. Check its pricing page: a paid product
  almost certainly needs a paid plan, and the free tier's 10 requests/minute is shared by *all* your users. Check Groq's terms and rate limits for production use and the terms of the Google News RSS feed too.
* Decide whether to keep showing team crests (they come from football-data.org URLs and are copyrighted by the clubs).

## Local development (no login, no Stripe, no database)
```bash
# backend
cd backend
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                  # keep AUTH_ENABLED=false; fill in the Groq and football-data keys
uvicorn main:app --reload                             # http://127.0.0.1:8000

# frontend (second terminal)
cd frontend
npm install
npm run dev                                           # http://localhost:5173
```
`AUTH_ENABLED` defaults to **true** when unset (secure by default), so an existing `backend/.env` needs the line `AUTH_ENABLED=false` for local work.
With it off there is no login, nobody is billed, and predictions go to `backend/data_store/` as before. The frontend runs in the same
mode when `VITE_SUPABASE_URL` is empty. To try the real login locally, fill `frontend/.env.local` (see `frontend/.env.example`) and the production block of `backend/.env`.

Tests: `cd backend && pytest`. To also run the Postgres tests against the real migration, point `TEST_DATABASE_URL` at any
Postgres where a scratch database may be created, e.g. `TEST_DATABASE_URL=postgresql://postgres@localhost:5432/postgres pytest`.

## Configuration
All settings are documented in `backend/.env.example` (production: set them in the Render dashboard). In local development `GET /api/health` shows what is configured (never the keys); in production it returns only `{"status": "ok"}`.

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
Public: `GET /api/health` · `GET /api/evaluation` · `POST /api/stripe/webhook` (signature-verified).
Signed in (`Authorization: Bearer <Supabase access token>`): `GET /api/fixtures?days=14&competitions=PL,PD` · `GET /api/billing/status` ·
`POST /api/billing/checkout` · `POST /api/billing/portal` · `GET /api/jobs/{id}` (your own jobs only).
Subscribers: `POST /api/analyze/start` (progress via the job) · `POST /api/analyze` · `GET /api/predictions` · `GET /api/matches/{id}`.
Operator (`X-Admin-Secret`): `POST /api/evaluation/refresh` · `GET /api/backtest`.
Errors: `{"error": {"code", "message", "agent"}}`; `401 unauthorized`, `402 subscription_required`, `429 usage_limit`.

## Tests
`cd backend && pytest`: each agent, Groq errors, football-API errors, news failures, invalid/missing data, parameter
sampling and correlations, scenario normalisation, scenario-conditioned results, exactly 50,000 simulations, vectorisation
(AST check plus an RNG-call-count check), goal distributions, outcome aggregation, probability normalisation, metrics,
backtesting without look-ahead, store immutability, API error handling, the full workflow.
Production layer: JWT verification (expiry, audience, issuer, forged and algorithm-confusion tokens), access control per plan, per-user daily limits and
allowance refunds, one-analysis-per-match caching, job ownership, Stripe webhooks (signature, replay, idempotency, out-of-order delivery, retries),
checkout and portal parameters, and the Postgres stores against the real migration (append-only triggers, concurrent writers, RLS lock-out).

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
* The API runs as a single process (`render.yaml` pins one instance): running jobs and the concurrency limiter live in memory. Scaling out needs a shared job queue first.
* Not yet in place: error monitoring (Sentry or similar), IP-based rate limiting of the public endpoints, and database backups beyond Supabase's defaults.
