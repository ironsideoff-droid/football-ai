from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Callable, Optional

from agents.chief_predictor import ChiefPredictorAgent
from agents.researcher import ResearcherAgent
from agents.tactical_analyst import TacticalAnalystAgent
from errors import AppError
from football_data.provider import FootballProvider
from schemas.agent_reports import ChiefPrediction
from schemas.predictions import AnalysisResult, SimulationResult, StepState
from simulation.monte_carlo import N_SIMULATIONS, run_simulation

STEPS = [
    ("data", "Football data"),
    ("researcher", "Researcher"),
    ("tactical", "Tactical Analyst"),
    ("chief_plan", "Chief Predictor: assessment"),
    ("simulation", f"Monte Carlo: {N_SIMULATIONS:,} simulations"),
    ("chief_final", "Chief Predictor: verdict"),
]


class Progress:
    def __init__(self) -> None:
        self.steps = {k: StepState(key=k, label=label) for k, label in STEPS}

    def start(self, key: str, message: Optional[str] = None) -> None:
        self.steps[key].status, self.steps[key].message = "running", message

    def done(self, key: str, message: Optional[str] = None) -> None:
        self.steps[key].status, self.steps[key].message = "done", message

    def fail(self, key: str, message: str) -> None:
        self.steps[key].status, self.steps[key].message = "error", message

    def as_list(self) -> list[dict]:
        return [s.model_dump() for s in self.steps.values()]


def normalize_prediction(pred: ChiefPrediction, sim: SimulationResult) -> list[str]:
    """Make the probability groups sum to 100 and flag large departures from the simulation."""
    warnings: list[str] = []

    def norm(values: list[float]) -> list[float]:
        total = sum(values)
        return [round(100 * v / total, 1) for v in values] if total > 0 else values

    p = pred.probabilities
    p.home_win, p.draw, p.away_win = norm([p.home_win, p.draw, p.away_win])
    pred.over_under_2_5.over_2_5, pred.over_under_2_5.under_2_5 = norm(
        [pred.over_under_2_5.over_2_5, pred.over_under_2_5.under_2_5]
    )
    pred.both_teams_to_score.yes, pred.both_teams_to_score.no = norm(
        [pred.both_teams_to_score.yes, pred.both_teams_to_score.no]
    )
    for label, chief, simulated in (
        ("home win", p.home_win, sim.home_win_pct),
        ("draw", p.draw, sim.draw_pct),
        ("away win", p.away_win, sim.away_win_pct),
    ):
        if abs(chief - simulated) > 15:
            warnings.append(
                f"The Chief Predictor's {label} probability ({chief}%) differs from the simulation ({simulated}%) by more than 15 points."
            )
    return warnings


class Orchestrator:
    def __init__(
        self,
        provider: FootballProvider,
        researcher: ResearcherAgent,
        tactical: TacticalAnalystAgent,
        chief: ChiefPredictorAgent,
        pass_research_to_tactical: bool = False,
        simulate: Callable = run_simulation,
    ):
        self.provider, self.researcher, self.tactical, self.chief = provider, researcher, tactical, chief
        self.pass_research = pass_research_to_tactical
        self.simulate = simulate

    async def _step(self, progress: Progress, key: str, coro, message: Optional[str] = None):
        progress.start(key, message)
        try:
            result = await coro
        except AppError as exc:
            progress.fail(key, exc.message)
            raise
        except Exception as exc:  # noqa: BLE001
            progress.fail(key, str(exc))
            raise
        progress.done(key)
        return result

    async def analyze(self, match_id: int, progress: Optional[Progress] = None) -> AnalysisResult:
        progress = progress or Progress()
        context = await self._step(progress, "data", self.provider.get_match_context(match_id))
        match = context.match

        if self.pass_research:
            research = await self._step(progress, "researcher", self.researcher.run(context))
            tactical = await self._step(progress, "tactical", self.tactical.run(context, research))
        else:
            research, tactical = await asyncio.gather(
                self._step(progress, "researcher", self.researcher.run(context)),
                self._step(progress, "tactical", self.tactical.run(context)),
            )

        plan = await self._step(
            progress, "chief_plan", self.chief.plan(match, research, tactical, context.data_availability)
        )
        simulation = await self._step(
            progress, "simulation", asyncio.to_thread(self.simulate, plan.simulation_params)
        )
        prediction = await self._step(
            progress,
            "chief_final",
            self.chief.finalize(match, research, tactical, plan, simulation, context.data_availability),
        )
        warnings = normalize_prediction(prediction, simulation) + simulation.param_warnings

        return AnalysisResult(
            match=match,
            researcher=research,
            tactical=tactical,
            chief_plan=plan,
            simulation=simulation,
            prediction=prediction,
            data_availability=context.data_availability,
            warnings=warnings,
            models={
                "researcher": self.researcher.client.model,
                "tactical_analyst": self.tactical.client.model,
                "chief_predictor": self.chief.client.model,
            },
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
