"""Postgres (Supabase) implementation of the prediction store. Same interface and guarantees as the file store:
a prediction is written once, never overwritten, refused after kickoff, and carries a SHA-256 digest. The schema also
blocks UPDATE/DELETE with triggers (see supabase/migrations)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from errors import ImmutablePredictionError, StoreError
from evaluation.store import PredictionStore, _digest, _now


class PostgresPredictionStore(PredictionStore):
    def __init__(self, pool):
        super().__init__(directory=".")  # file paths unused
        self.pool = pool

    # ------------------------------------------------------------------ helpers
    def _fetch(self, sql: str, params: tuple = ()) -> list[dict]:
        try:
            with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                return cur.fetchall()
        except (psycopg.Error, OSError) as exc:  # pool timeouts are psycopg_pool.PoolTimeout (a psycopg.Error subclass)
            raise StoreError(f"Database error: {type(exc).__name__}") from exc

    def _execute_returning(self, sql: str, params: tuple) -> Optional[dict]:
        rows = self._fetch(sql, params)
        return rows[0] if rows else None

    # ------------------------------------------------------------------ predictions and results
    def predictions(self) -> list[dict]:
        return [{"record": r["record"], "digest": r["digest"]}
                for r in self._fetch("select record, digest from predictions order by created_at, match_id")]

    def results(self) -> list[dict]:
        return [{"match_id": r["match_id"], "home": r["home"], "away": r["away"], "recorded_at": r["recorded_at"].isoformat(timespec="seconds")}
                for r in self._fetch("select match_id, home, away, recorded_at from results order by match_id")]

    def get_prediction(self, match_id: int) -> Optional[dict]:
        r = self._execute_returning("select record, digest from predictions where match_id = %s", (match_id,))
        return {"record": r["record"], "digest": r["digest"]} if r else None

    def get_result(self, match_id: int) -> Optional[dict]:
        r = self._execute_returning("select match_id, home, away, recorded_at from results where match_id = %s", (match_id,))
        return {**r, "recorded_at": r["recorded_at"].isoformat(timespec="seconds")} if r else None

    def save_prediction(self, record: dict[str, Any], now: Optional[datetime] = None) -> dict:
        now = now or _now()
        kickoff = datetime.fromisoformat(record["kickoff_utc"].replace("Z", "+00:00"))
        if self.get_prediction(record["match_id"]):
            raise ImmutablePredictionError(f"A prediction for match {record['match_id']} already exists and cannot be changed.")
        if kickoff <= now:
            raise ImmutablePredictionError("The match has already started; predictions made after kickoff are not stored.")
        record = {**record, "created_at": now.isoformat(timespec="seconds")}
        row = {"record": record, "digest": _digest(record)}
        inserted = self._execute_returning(
            "insert into predictions (match_id, record, digest, created_at) values (%s, %s, %s, %s) "
            "on conflict (match_id) do nothing returning match_id",
            (record["match_id"], Jsonb(record), row["digest"], now))
        if inserted is None:  # lost a race with a concurrent analysis of the same match
            raise ImmutablePredictionError(f"A prediction for match {record['match_id']} already exists and cannot be changed.")
        return row

    def save_result(self, match_id: int, home: int, away: int) -> Optional[dict]:
        r = self._execute_returning(
            "insert into results (match_id, home, away) values (%s, %s, %s) on conflict (match_id) do nothing "
            "returning match_id, home, away, recorded_at", (match_id, home, away))
        return {**r, "recorded_at": r["recorded_at"].isoformat(timespec="seconds")} if r else None

    # ------------------------------------------------------------------ full analyses
    def get_analysis(self, match_id: int) -> Optional[dict]:
        r = self._execute_returning("select result from analyses where match_id = %s", (match_id,))
        return r["result"] if r else None

    def save_analysis(self, match_id: int, result: dict) -> bool:
        return self._execute_returning(
            "insert into analyses (match_id, result) values (%s, %s) on conflict (match_id) do nothing returning match_id",
            (match_id, Jsonb(result))) is not None
