"""Demo: stream a large CSV in chunks into an API that goes down mid-run.

    python -m examples.streaming_ingest

- The file is read chunk by chunk (never fully in memory).
- A checkpoint is saved after every chunk, so a killed process resumes where it stopped.
- A circuit breaker stops the bot from hammering the API while it is down.
"""
from __future__ import annotations

import csv
import logging
import tempfile
from pathlib import Path

from examples.fakes import FakeClock, FlakyAPI, banner, setup_logging
from patterns.checkpoint import Checkpoint, iter_chunks
from patterns.circuit_breaker import CircuitBreaker, CircuitOpenError

log = logging.getLogger("demo")
TOTAL_ROWS, CHUNK = 10_000, 500


def write_sample_csv(path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "amount"])
        for i in range(1, TOTAL_ROWS + 1):
            w.writerow([f"R{i:05d}", f"{(i * 37) % 1000}.00"])


def ingest(csv_path: Path, api: FlakyAPI, breaker: CircuitBreaker, checkpoint: Checkpoint,
           clock: FakeClock, stop_after_chunks: int | None = None) -> None:
    start = checkpoint.get("rows_done", 0)
    log.info("starting at row %d", start)
    for n, (rows_done, rows) in enumerate(iter_chunks(csv_path, CHUNK, start), start=1):
        while True:
            try:
                breaker.call(api.send, rows)
                break
            except CircuitOpenError as exc:
                log.info("breaker open -> waiting %.0fs instead of calling the API", exc.retry_in)
                clock.sleep(exc.retry_in)
            except ConnectionError as exc:
                log.warning("send failed at row %d (%s)", rows_done - len(rows), exc)
                clock.sleep(2)  # short pause; the breaker decides when to stop trying
        checkpoint.set("rows_done", rows_done)
        log.info("t=%4.0fs chunk ok, checkpoint rows_done=%d", clock(), rows_done)
        if stop_after_chunks and n >= stop_after_chunks:
            log.error("process killed (simulated crash)")
            return


def main() -> None:
    setup_logging()
    clock = FakeClock()
    api = FlakyAPI(clock, down_from=10, down_until=100)
    with tempfile.TemporaryDirectory() as tmp:
        csv_path, cp_path = Path(tmp) / "transactions.csv", Path(tmp) / "ingest.json"
        write_sample_csv(csv_path)

        banner("Run 1: API goes down at t=10s and recovers at t=100s; process dies after 12 chunks")
        breaker = CircuitBreaker("ledger-api", failure_threshold=3, cooldown=30, clock=clock)
        ingest(csv_path, api, breaker, Checkpoint(cp_path), clock, stop_after_chunks=12)

        banner("Run 2: restart -- resumes from checkpoint")
        breaker = CircuitBreaker("ledger-api", failure_threshold=3, cooldown=30, clock=clock)
        ingest(csv_path, api, breaker, Checkpoint(cp_path), clock)

    banner("Totals")
    ids = api.received_ids
    print(f"rows delivered       : {len(ids)} of {TOTAL_ROWS} (unique: {len(set(ids))})")
    print(f"API calls total      : {api.calls}")
    print(f"calls during outage  : {api.calls_while_down} over a 90s outage "
          f"(a 2s retry loop without a breaker would make ~{90 // 3})")


if __name__ == "__main__":
    main()
