"""Append-only prediction store (JSON lines on local disk). Used for local development and tests;
production uses evaluation/pg_store.py (Postgres) with the same interface.

* A prediction is written once. Writing the same match again is refused, never overwritten.
* A prediction is refused if the match has already kicked off.
* Each record carries a SHA-256 digest of its content; `verify()` reports any record that no longer matches.
* Results are stored separately, also append-only and once per match.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from errors import ImmutablePredictionError, StoreError


def _digest(record: dict) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PredictionStore:
    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self._lock = threading.Lock()
        self.predictions_file = self.dir / "predictions.jsonl"
        self.results_file = self.dir / "results.jsonl"
        self.analyses_file = self.dir / "analyses.jsonl"

    def _read(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def _append(self, path: Path, row: dict) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        except OSError as exc:
            raise StoreError(f"Could not write to the prediction store: {exc}") from exc

    def predictions(self) -> list[dict]:
        return self._read(self.predictions_file)

    def results(self) -> list[dict]:
        return self._read(self.results_file)

    def get_prediction(self, match_id: int) -> Optional[dict]:
        return next((p for p in self.predictions() if p["record"]["match_id"] == match_id), None)

    def get_result(self, match_id: int) -> Optional[dict]:
        return next((r for r in self.results() if r["match_id"] == match_id), None)

    def save_prediction(self, record: dict[str, Any], now: Optional[datetime] = None) -> dict:
        now = now or _now()
        kickoff = datetime.fromisoformat(record["kickoff_utc"].replace("Z", "+00:00"))
        with self._lock:
            if self.get_prediction(record["match_id"]):
                raise ImmutablePredictionError(f"A prediction for match {record['match_id']} already exists and cannot be changed.")
            if kickoff <= now:
                raise ImmutablePredictionError("The match has already started; predictions made after kickoff are not stored.")
            record = {**record, "created_at": now.isoformat(timespec="seconds")}
            row = {"record": record, "digest": _digest(record)}
            self._append(self.predictions_file, row)
            return row

    def save_result(self, match_id: int, home: int, away: int) -> Optional[dict]:
        with self._lock:
            if any(r["match_id"] == match_id for r in self.results()):
                return None
            row = {"match_id": match_id, "home": home, "away": away, "recorded_at": _now().isoformat(timespec="seconds")}
            self._append(self.results_file, row)
            return row

    def get_analysis(self, match_id: int) -> Optional[dict]:
        """The full analysis stored together with the prediction (served to later users instead of re-running the agents)."""
        row = next((a for a in self._read(self.analyses_file) if a["match_id"] == match_id), None)
        return row["result"] if row else None

    def save_analysis(self, match_id: int, result: dict) -> bool:
        with self._lock:
            if self.get_analysis(match_id) is not None:
                return False
            self._append(self.analyses_file, {"match_id": match_id, "result": result, "created_at": _now().isoformat(timespec="seconds")})
            return True

    def verify(self) -> list[int]:
        """Match ids whose stored prediction no longer matches its digest (tampering or corruption)."""
        return [p["record"]["match_id"] for p in self.predictions() if _digest(p["record"]) != p["digest"]]

    def joined(self) -> list[dict]:
        """Predictions that have a result, in the shape expected by evaluation.metrics."""
        results = {r["match_id"]: r for r in self.results()}
        bad = set(self.verify())
        out = []
        for p in self.predictions():
            rec = p["record"]
            res = results.get(rec["match_id"])
            if res and rec["match_id"] not in bad:
                out.append({**rec, "actual": {"home": res["home"], "away": res["away"]}, "odds": res.get("odds") or rec.get("odds")})
        return out
