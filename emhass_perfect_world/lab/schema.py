"""Scheme normalizate. Tot ce iese din laborator spre scoreboard trece prin ScenarioResult;
tot ce intră din HA în arhivă trece prin normalize_slot."""
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

SLOT_SECONDS = 900
SLOT_SCHEMA = "1.0"
RESULT_SCHEMA = "1.0"

# Atribute din senzorul de captură care nu sunt date.
_DROP = {"icon", "friendly_name"}

SCENARIOS = {
    "EMHASS_PPC_PERFECT_PV_LOAD",     # același MPC, PV și load reale ca prognoză
    "EMHASS_PPC_REPLAY_INCUMBENT",    # același MPC, prognoza capturată (test de fidelitate)
    "EMHASS_HYDRO_V2_PERFECT",        # MPC cu tarif Hydro V2, PV/load perfecte
}


def num(v):
    """Număr din atribut HA: HA serializează valorile foarte mici ca șir ('1e-05'); 'unknown'/bool/nan -> None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
    elif isinstance(v, str):
        try:
            f = float(v)
        except ValueError:
            return None
    else:
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


class SchemaError(ValueError):
    pass


def normalize_slot(state, attrs, tz, observed_utc=None):
    """Senzorul de captură -> rând de arhivă. Ridică SchemaError dacă nu e un slot valid."""
    epoch = attrs.get("slot_start_epoch")
    if not isinstance(epoch, (int, float)) or int(epoch) % SLOT_SECONDS:
        raise SchemaError(f"slot_start_epoch invalid: {epoch!r}")
    epoch = int(epoch)
    start_utc = datetime.fromtimestamp(epoch, timezone.utc)
    row = {k: v for k, v in attrs.items() if k not in _DROP}
    row.update(
        slot_schema=SLOT_SCHEMA,
        slot_start_epoch=epoch,
        slot_start_utc=start_utc.isoformat(),
        day_local=start_utc.astimezone(ZoneInfo(tz)).date().isoformat(),
        capture_state=state,
    )
    return row


def slot_hash(row):
    """Hash al conținutului, fără câmpuri de observare/revizie."""
    core = {k: v for k, v in row.items() if k not in ("observed_utc", "rev")}
    return hashlib.sha1(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()[:16]


def expected_slots(day_local, tz):
    """Număr de sloturi de 15 min într-o zi locală (92/96/100 la schimbarea orei)."""
    z = ZoneInfo(tz)
    d = datetime.fromisoformat(day_local).date()
    start = datetime(d.year, d.month, d.day, tzinfo=z)
    nxt = datetime.fromordinal(d.toordinal() + 1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=z)
    return int((end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds() // SLOT_SECONDS)


@dataclass
class ScenarioResult:
    """Rezultat normalizat pe (zi, scenariu, versiune EMHASS). Cost pozitiv = cheltuit."""
    day_local: str
    scenario_id: str
    emhass_version: str
    slots: int
    net_cost_ron: float
    grid_import_kwh: float
    grid_export_kwh: float
    soc_start_pct: float
    soc_end_pct: float
    method: str
    certified: bool = False
    cert_reason: str = "NOT_TESTED"
    schema_version: str = RESULT_SCHEMA
    era: str = field(default="")
    created_utc: str = field(default="")

    def __post_init__(self):
        if not self.era:
            self.era = f"emhass-{self.emhass_version}"
        if not self.created_utc:
            self.created_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.validate()

    def validate(self):
        if self.scenario_id not in SCENARIOS:
            raise SchemaError(f"scenariu necunoscut: {self.scenario_id}")
        if not self.emhass_version:
            raise SchemaError("emhass_version lipsește")
        datetime.fromisoformat(self.day_local)
        if self.slots < 0:
            raise SchemaError("slots < 0")

    def to_json(self):
        return json.dumps(asdict(self), sort_keys=True)
