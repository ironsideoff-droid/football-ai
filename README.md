# FOOTBALL AI

A personal football match prediction app. Three independent Groq LLM agents analyze a fixture, and the
Chief Predictor runs a vectorized NumPy Monte Carlo simulation (exactly 1,000 matches) before giving its verdict.
No database, no login, runs locally.

```
Browser (React) ──/api──▶ FastAPI ──▶ football-data.org   (real fixtures, results, standings, head-to-head)
                            │
                            ├─▶ Agent 1 Researcher        (Groq account 1) ┐ run concurrently
                            ├─▶ Agent 2 Tactical Analyst  (Groq account 2) ┘
                            └─▶ Agent 3 Chief Predictor   (Groq account 3)
                                  1. assesses both reports, sets simulation parameters
                                  2. NumPy engine: 1,000 vectorized simulations
                                  3. interprets the results, writes the final prediction
```

## Requirements
- Python 3.10+ and Node.js 18+
- Three Groq API keys, one per account: https://console.groq.com/keys
- A free football-data.org token: https://www.football-data.org/client/register

## Install and run

**Backend**
```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
# edit .env and fill in the keys (see below)
uvicorn main:app --reload          # http://127.0.0.1:8000
```

**Frontend** (second terminal)
```bash
cd frontend
npm install
npm run dev                        # http://localhost:5173
```
Open http://localhost:5173, pick a match and press **Analyze match**.

## Configuration (`backend/.env`)
| Variable | Meaning |
|---|---|
| `GROQ_API_KEY_1/2/3` | One key per agent, from three different Groq accounts |
| `GROQ_MODEL_1/2/3` | Model per agent. Empty = `llama-3.3-70b-versatile`. Pick models your accounts can use: https://console.groq.com/docs/models |
| `FOOTBALL_API_KEY` | football-data.org token |
| `PASS_RESEARCH_TO_TACTICAL` | `false` (default): agents 1 and 2 run concurrently on the same factual dataset. `true`: agent 2 waits for agent 1 and also receives its report |

Keys are read only by the backend and are never sent to the browser. `GET /api/health` shows which keys are configured (never their values).

## Tests
```bash
cd backend && pytest
```
All AI and football-API calls are mocked, so no paid requests are made. Tests cover each agent, Groq configuration and
errors, football data retrieval and statistics, report validation, the simulation (exactly 1,000 outcomes, a check that
`monte_carlo.py` contains no loops or comprehensions, aggregation), the full workflow and API error handling.

## API
| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Configuration status |
| `GET /api/fixtures?days=7&competitions=PL,PD` | Upcoming fixtures (max 10 days) |
| `GET /api/matches/{id}` | The factual dataset gathered for a match |
| `POST /api/analyze` `{match_id}` | Runs the full workflow and returns the result |
| `POST /api/analyze/start` + `GET /api/jobs/{id}` | Same workflow with step-by-step progress (used by the UI, in-memory only) |

Errors use `{"error": {"code", "message", "agent"}}` with codes such as `groq_auth_error`, `groq_rate_limit`,
`invalid_agent_output`, `football_auth_error`, `football_rate_limit`, `simulation_error`, `configuration_error`.

## Design notes and limits
- **Football data provider**: football-data.org (free tier, 10 requests/minute, major competitions only). One analysis uses 5 requests; responses are cached for 5 minutes. To change provider, implement `FootballProvider` in `backend/football_data/provider.py`.
- **Not available from this provider**: injuries, suspensions, lineups, team news, xG, pressing/possession stats. These are reported to the agents as `unavailable`, shown in the UI, and lower the confidence. Nothing is invented.
- Form, home/away splits and head-to-head are computed by the backend from real results, not by the LLMs, so the numbers are exact.
- The Tactical Analyst may use general football knowledge (e.g. usual formations); such profiles are labelled `model_knowledge` or `mixed`.
- **Simulation**: Poisson goals; each team's expected goals gets a mean-preserving log-normal uncertainty shock, optionally correlated. The Chief Predictor chooses `home_xg`, `away_xg`, `uncertainty_sigma` and `goal_correlation` from the reports (values are clamped to safe ranges, and clamping is reported). The seed and all parameters are returned with the results. Output percentages are estimates under those assumptions, not real-world probabilities.
- **Groq rate limits**: free-tier token-per-minute limits can be small for large models. If you see `groq_rate_limit`, wait a minute or choose a model with higher limits.
- The Chief Predictor's final probabilities are normalized to sum to 100, and a warning is shown if they differ from the simulation by more than 15 points.
