"""Runner laborator: arhivează sloturile de captură (pas 3). Pasul 4 adaugă soluțiile Perfect World."""
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from importlib.metadata import version

from .archive import SlotArchive
from .ha import HA
from .schema import SchemaError, normalize_slot

OPTS_PATH = os.environ.get("LAB_OPTIONS", "/data/options.json")
DEFAULTS = {
    "capture_entity": "sensor.deye_economic_auditor_scenario_capture",
    "data_dir": "/share/deye_lab/pw_lab",
    "poll_seconds": 30,
    "backfill_days": 10,
    "log_level": "info",
}

log = logging.getLogger("lab")
log.propagate = False  # emhass configurează root logger; evită liniile duble
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [lab] %(levelname)s %(message)s"))
    log.addHandler(_h)


def load_options():
    opts = dict(DEFAULTS)
    try:
        with open(OPTS_PATH, encoding="utf-8") as fh:
            opts.update({k: v for k, v in json.load(fh).items() if v not in (None, "")})
    except FileNotFoundError:
        pass
    log.setLevel(str(opts["log_level"]).upper())
    return opts


def ingest(archive, state, attrs, tz):
    """Un rând de stare -> arhivă. Returnează True dacă s-a scris."""
    try:
        row = normalize_slot(state, attrs, tz)
    except SchemaError as exc:
        log.debug("ignorat: %s", exc)
        return False
    return archive.add(row)


def backfill(ha, archive, entity, days, tz):
    """Istoric pe felii de 24 h: API-ul REST întoarce doar 24 h de la start dacă lipsește end_time."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = now - timedelta(days=days)
    new = 0
    while start < now:
        end = min(start + timedelta(days=1), now)
        for st in ha.history(entity, start.isoformat(), end.isoformat()):
            if st.get("state") in ("unknown", "unavailable"):
                continue
            new += ingest(archive, st["state"], st.get("attributes", {}), tz)
        start = end
    log.info("backfill %sz: %d sloturi noi (total %d)", days, new, len(archive))
    return new


def write_status(root, **kw):
    kw["updated_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp = os.path.join(root, "status.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(kw, fh, indent=1, sort_keys=True)
    os.replace(tmp, os.path.join(root, "status.json"))


def selftest():
    log.info("emhass %s", version("emhass"))
    import emhass.optimization  # noqa: F401  (dependențele grele: cvxpy, pandas)
    log.info("import emhass.optimization OK")


def main():
    opts = load_options()
    root = opts["data_dir"]
    os.makedirs(root, exist_ok=True)
    selftest()
    ha = HA()
    tz = ha.config()["time_zone"]
    archive = SlotArchive(root)
    log.info("arhivă: %s, %d sloturi existente, tz=%s", root, len(archive), tz)
    emhass_version = version("emhass")
    last_err = None
    try:
        backfill(ha, archive, opts["capture_entity"], int(opts["backfill_days"]), tz)
    except Exception as exc:  # noqa: BLE001 — nu oprim add-on-ul pentru un backfill eșuat
        last_err = f"backfill: {exc}"
        log.warning("%s", last_err)
    while True:
        try:
            st = ha.state(opts["capture_entity"])
            if ingest(archive, st["state"], st.get("attributes", {}), tz):
                log.info("slot nou arhivat: %s", st["attributes"].get("slot_start_epoch"))
            last_err = None
        except Exception as exc:  # noqa: BLE001
            last_err = f"poll: {exc}"
            log.warning("%s", last_err)
        days = archive.days()
        write_status(
            root,
            emhass_version=emhass_version,
            slots_total=len(archive),
            last_day=days[-1] if days else None,
            last_day_coverage=archive.coverage(days[-1], tz) if days else None,
            last_error=last_err,
        )
        time.sleep(int(opts["poll_seconds"]))


if __name__ == "__main__":
    main()
