"""Arhivă pe sloturi: JSONL pe zi, append-only, cu revizii. Cititorul ia ultima revizie per slot."""
import json
import os
from datetime import datetime, timezone

from .schema import expected_slots, slot_hash


class SlotArchive:
    def __init__(self, root):
        self.dir = os.path.join(root, "slots")
        os.makedirs(self.dir, exist_ok=True)
        self._index = {}  # epoch -> (hash, rev)
        self._load_index()

    def _path(self, day):
        return os.path.join(self.dir, f"{day}.jsonl")

    def _load_index(self):
        for name in sorted(os.listdir(self.dir)):
            if not name.endswith(".jsonl"):
                continue
            with open(os.path.join(self.dir, name), encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        self._index[row["slot_start_epoch"]] = (slot_hash(row), row.get("rev", 1))
                    except (ValueError, KeyError):
                        continue  # linie coruptă (oprire în mijlocul scrierii): ignorată

    def add(self, row):
        """True dacă s-a scris ceva (slot nou sau conținut schimbat)."""
        epoch = row["slot_start_epoch"]
        h = slot_hash(row)
        prev = self._index.get(epoch)
        if prev and prev[0] == h:
            return False
        rev = (prev[1] + 1) if prev else 1
        out = dict(row, rev=rev, observed_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        with open(self._path(row["day_local"]), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(out, sort_keys=True, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._index[epoch] = (h, rev)
        return True

    def read_day(self, day):
        rows = {}
        try:
            with open(self._path(day), encoding="utf-8") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    rows[row["slot_start_epoch"]] = row  # ultima revizie câștigă
        except FileNotFoundError:
            pass
        return [rows[k] for k in sorted(rows)]

    def coverage(self, day, tz):
        got = len(self.read_day(day))
        exp = expected_slots(day, tz)
        return {"day_local": day, "slots": got, "expected": exp, "complete": got == exp}

    def days(self):
        return sorted(n[:-6] for n in os.listdir(self.dir) if n.endswith(".jsonl"))

    def __len__(self):
        return len(self._index)
