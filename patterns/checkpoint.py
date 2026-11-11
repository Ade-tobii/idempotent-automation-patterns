"""Checkpointing: remember progress so a failure at unit 27 doesn't restart unit 1."""
from __future__ import annotations

import csv
import itertools
import json
import os
from pathlib import Path
from typing import Any, Iterator


class Checkpoint:
    """A small JSON checkpoint file, written atomically (temp file + os.replace)
    so a crash mid-write can never leave a half-written checkpoint behind."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if self.path.exists():
            self._state = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self._state = {"done": [], "values": {}}

    def is_done(self, unit: str) -> bool:
        return unit in self._state["done"]

    def mark_done(self, unit: str) -> None:
        if not self.is_done(unit):
            self._state["done"].append(unit)
            self._save()

    def get(self, key: str, default: Any = None) -> Any:
        return self._state["values"].get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._state["values"][key] = value
        self._save()

    def _save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)


def iter_chunks(csv_path: str | Path, chunk_size: int, start_row: int = 0) -> Iterator[tuple[int, list[dict]]]:
    """Stream a CSV in chunks without loading it into memory.

    Yields (rows_done_after_this_chunk, rows). Save the first value to a
    checkpoint after each chunk and pass it back as `start_row` to resume.
    """
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        position, chunk = start_row, []
        for record in itertools.islice(reader, start_row, None):
            chunk.append(record)
            position += 1
            if len(chunk) == chunk_size:
                yield position, chunk
                chunk = []
        if chunk:
            yield position, chunk
