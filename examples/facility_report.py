"""Demo: month-end report across 32 facilities with classified retries and per-unit checkpoints.

    python -m examples.facility_report

Run 1 hits noisy timeouts (retried), one facility with missing data (skipped),
then expired credentials at FAC-27 (stop + alert). After a human fixes the
credentials, run 2 resumes at FAC-27 instead of starting again from FAC-01.
"""
from __future__ import annotations

import random
import tempfile
from pathlib import Path

from examples.fakes import FakeClock, FakeFacilitySource, banner, setup_logging
from patterns.checkpoint import Checkpoint
from patterns.retry import RetryPolicy, process_units

FACILITIES = [f"FAC-{i:02d}" for i in range(1, 33)]


def main() -> None:
    setup_logging()
    clock = FakeClock()  # virtual time: backoff waits are recorded, not slept
    source = FakeFacilitySource(seed=7, missing={"FAC-07"}, credentials_expire_at="FAC-27")
    report: dict[str, dict] = {}

    def handle(facility: str) -> None:
        report[facility] = source.fetch(facility)

    with tempfile.TemporaryDirectory() as tmp:
        checkpoint_path = Path(tmp) / "facility_run.json"
        for run in (1, 2):
            banner(f"Run {run}")
            summary = process_units(
                FACILITIES, handle, Checkpoint(checkpoint_path),
                policy=RetryPolicy(max_attempts=4, base_delay=2.0),
                sleep=clock.sleep, rng=random.Random(run),
            )
            print("\nRun summary:\n" + summary.report())
            if summary.stopped_at:
                print("\n[operator] rotates the service-account password and re-runs the bot")
                source.credentials_ok, source.credentials_expire_at = True, None

    banner("Totals")
    print(f"facilities in report : {len(report)}/{len(FACILITIES)}")
    print(f"fetch calls made     : {len(source.fetch_calls)} (FAC-01 fetched {source.fetch_calls.count('FAC-01')}x)")
    print(f"time spent backing off (virtual): {sum(clock.sleeps):.1f}s over {len(clock.sleeps)} retries")


if __name__ == "__main__":
    main()
