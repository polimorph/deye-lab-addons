"""Runner laborator: arhivează sloturile de captură (pas 3). Pasul 4 adaugă soluțiile Perfect World."""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from importlib.metadata import version

from .archive import SlotArchive
from .ha import HA
from . import adapter, experiments, perfect, probe, publish, solve_probe
from .schema import SchemaError, normalize_slot

OPTS_PATH = os.environ.get("LAB_OPTIONS", "/data/options.json")
DEFAULTS = {
    "capture_entity": "sensor.deye_economic_auditor_scenario_capture",
    "data_dir": "/share/deye_lab/pw_lab",
    "poll_seconds": 30,
    "backfill_days": 10,
    "chain_start": "2026-11-04T15:15:00+02:00",
    "chain_every_seconds": 300,
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


def chain_start(opts, archive):
    """(epoch, soc) de pornire a lanțurilor, din slotul de start configurat; None dacă lipsește din arhivă."""
    start = int(datetime.fromisoformat(opts["chain_start"]).timestamp()) // 900 * 900
    row = next((r for d in archive.days() for r in archive.read_day(d) if r["slot_start_epoch"] == start), None)
    soc = row.get("actual_soc_start_pct") if row else None
    if not isinstance(soc, (int, float)):
        return None
    return start, soc / 100


def run_chain(opts, archive, chain, tz, emhass_version):
    """Un pas de lanț: înaintează cât permite arhiva (orizont acoperit), apoi rescrie agregatele pe zi."""
    if chain.state is None:
        st = chain_start(opts, archive)
        if st is None:
            log.warning("chain: lipsește slotul de start %s sau SOC-ul lui; lanțul nu pornește", opts["chain_start"])
            return 0
        chain.init(*st)
        log.info("chain: pornit la %s cu SOC %.1f%%", opts["chain_start"], st[1] * 100)

    async def go():
        clog = logging.getLogger("lab.chain")
        emhass_conf, config, params = await adapter.make_context(opts["data_dir"], clog)
        solver = adapter.first_row_solver(emhass_conf, params, clog)
        return await perfect.advance(
            chain, archive, tz, solver, emhass_version, log=log,
            row_extra={"config_hash": adapter.config_hash(config)},
        )

    n = asyncio.run(asyncio.wait_for(go(), 900))
    if n:
        days = sorted({d for d in archive.days()})
        perfect.write_day_results(chain, tz, emhass_version, days)
        log.info("chain: %d sloturi procesate, următorul %s", n, chain.state["next_epoch"])
    return n


def publish_scoreboard(ha, archive, chain, tz, emhass_version):
    """Cumul pe aceeași perioadă pentru toate lumile -> results/scoreboard.json + senzor HA (republicat periodic)."""
    rows = [r for d in archive.days() for r in chain.read_slots(d)]
    by_epoch = {r["slot_start_epoch"]: r for d in archive.days() for r in archive.read_day(d)}
    try:
        loss = float(ha.state(publish.LOSS_ENTITY)["attributes"].get("loss_kwh_per_slot", publish.DEFAULT_LOSS))
    except Exception:  # noqa: BLE001 — L implicit dacă acumulatorul v0.2.7 lipsește
        loss = publish.DEFAULT_LOSS
    s = publish.summarize(rows, by_epoch, loss, emhass_version)
    if s is None:
        # încă niciun slot evaluat (fără provizorii): senzorul există, dar fără valoare
        ha.post_state(publish.ENTITY, "unknown", {
            "friendly_name": "EMHASS MaxControl Perfect World", "icon": "mdi:crystal-ball", "unit_of_measurement": "RON",
            "status": "WAITING_FULL_HORIZON", "emhass_version": emhass_version,
            "chain_next_slot_epoch": (chain.state or {}).get("next_epoch"),
        })
        return None
    publish.write(chain.dir, s)
    attrs = publish.flatten(s)
    attrs.update(unit_of_measurement="RON", friendly_name="EMHASS MaxControl Perfect World", icon="mdi:crystal-ball")
    ha.post_state(publish.ENTITY, str(s["value_ron"]), attrs)
    return s


def run_experiments(opts, archive, tz, emhass_version):
    """Variantele experimentale, fiecare izolat: o eroare la una nu le oprește pe celelalte și nici lanțul principal."""
    start = chain_start(opts, archive)
    total = 0
    for v in experiments.VARIANTS:
        try:
            total += asyncio.run(asyncio.wait_for(
                experiments.advance_variant(opts["data_dir"], archive, start, tz, emhass_version, v, log=log), 900))
        except Exception as exc:  # noqa: BLE001
            log.warning("experiment %s: %r", v["id"], exc)
    if total:
        log.info("experimente: %d sloturi procesate", total)
    return total


def publish_experiments(ha, archive, main_chain, tz, emhass_version, opts):
    by_epoch = {r["slot_start_epoch"]: r for d in archive.days() for r in archive.read_day(d)}
    main_rows = [r for d in archive.days() for r in main_chain.read_slots(d)]
    variants = []
    for v in experiments.VARIANTS:
        ch = perfect.Chain(experiments.chain_dir(opts["data_dir"], v["id"]))
        variants.append((v, [r for d in archive.days() for r in ch.read_slots(d)]))
    try:
        loss = float(ha.state(publish.LOSS_ENTITY)["attributes"].get("loss_kwh_per_slot", publish.DEFAULT_LOSS))
    except Exception:  # noqa: BLE001
        loss = publish.DEFAULT_LOSS
    s = publish.summarize_experiments(main_rows, variants, by_epoch, loss, emhass_version)
    base_attrs = {"friendly_name": "EMHASS Perfect World · experimente soc_final", "icon": "mdi:flask-outline"}
    if s is None:
        ha.post_state(experiments_entity(), "unknown", dict(base_attrs, status="WAITING_FULL_HORIZON",
                      emhass_version=emhass_version))
        return None
    publish.write_experiments(os.path.join(opts["data_dir"], "experiments"), s)
    attrs = publish.flatten_experiments(s)
    attrs.update(base_attrs, unit_of_measurement="sloturi", status="OK")
    ha.post_state(experiments_entity(), str(s["slots_counted"]), attrs)
    return s


def experiments_entity():
    return "sensor.emhass_perfect_world_experiments"


def chain_selftest(opts, archive, tz, emhass_version):
    """Autotest unic pe dimineața de 04.11 (orizont acoperit): lanț separat în data_dir/selftest, comparat cu lumea reală."""
    root = os.path.join(opts["data_dir"], "selftest")
    marker = os.path.join(root, f"selftest_{emhass_version}.json")
    if os.path.exists(marker):
        return
    os.makedirs(root, exist_ok=True)
    start = int(datetime.fromisoformat("2026-11-04T00:00:00+02:00").timestamp())
    rows = {r["slot_start_epoch"]: r for r in archive.read_day("2026-11-04")}
    if start not in rows or not isinstance(rows[start].get("actual_soc_start_pct"), (int, float)):
        log.warning("selftest: lipsește 04.11 00:00")
        return
    chain = perfect.Chain(root)
    chain.init(start, rows[start]["actual_soc_start_pct"] / 100)

    async def go():
        clog = logging.getLogger("lab.selftest")
        emhass_conf, config, params = await adapter.make_context(opts["data_dir"], clog)
        solver = adapter.first_row_solver(emhass_conf, params, clog)
        return await perfect.advance(chain, archive, tz, solver, emhass_version, max_slots=56,
                                     row_extra={"config_hash": adapter.config_hash(config)})

    t0 = time.time()
    n = asyncio.run(asyncio.wait_for(go(), 900))
    lab = chain.read_slots("2026-11-04")
    real = [rows[r["slot_start_epoch"]] for r in lab if not r.get("skipped")]
    num = lambda v: v if isinstance(v, (int, float)) else 0.0
    rep = {
        "emhass_version": emhass_version, "slots": n, "seconds": round(time.time() - t0, 1),
        "lab_cost_ron": round(sum(r["net_cost_ron"] for r in lab if not r.get("skipped")), 4),
        "real_cost_ron": round(sum(num(r["actual_ppc_net_cost_ron"]) for r in real), 4),
        "lab_import_kwh": round(sum(r["import_kwh"] for r in lab if not r.get("skipped")), 3),
        "real_import_kwh": round(sum(num(r["actual_grid_import_kwh"]) for r in real), 3),
        "lab_export_kwh": round(sum(r["export_kwh"] for r in lab if not r.get("skipped")), 3),
        "real_export_kwh": round(sum(num(r["actual_grid_export_kwh"]) for r in real), 3),
        "soc_start_pct": round(lab[0]["soc_start"] * 100, 1) if lab else None,
        "lab_soc_end_pct": round(lab[-1]["soc_end"] * 100, 1) if lab else None,
        "real_soc_end_pct": real[-1].get("actual_soc_end_pct") if real else None,
        "skipped": sum(1 for r in lab if r.get("skipped")),
        "price_filled": sum(1 for r in lab if not r.get("skipped") and not r.get("scored")),
        "horizon_truncated": sum(1 for r in lab if r.get("horizon_truncated")),
        "statuses": sorted({r.get("optim_status", "") for r in lab if not r.get("skipped")}),
    }
    with open(marker, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1)
    log.info("selftest lanț: %s", rep)


def selftest():
    log.info("emhass %s", version("emhass"))
    import emhass.optimization  # noqa: F401  (dependențele grele: cvxpy, pandas)
    log.info("import emhass.optimization OK")


REPUBLISH_MIN_GAP = 60  # secunde între două republicări forțate


def published_entities_missing(ha, chain):
    """Senzorii publicați prin REST dispar la repornirea HA. True dacă vreunul lipsește (404)."""
    names = [publish.ENTITY]
    if chain.state is not None:  # senzorul de experimente există doar după primul rezultat al lanțului
        names.append(experiments_entity())
    return any(not ha.exists(n) for n in names)


def main():
    opts = load_options()
    root = opts["data_dir"]
    os.makedirs(root, exist_ok=True)
    selftest()
    if probe.needs_run(root):
        try:
            probe.run(root)
            log.info("probe.json scris (versiune nouă de EMHASS)")
        except Exception as exc:  # noqa: BLE001
            log.warning("probe eșuat: %s", exc)
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
    if solve_probe.needs_run(root):
        rep = solve_probe.run(root, tz)
        log.info("solve_probe: ok=%s %s", rep.get("ok"), rep.get("error", ""))
    try:
        chain_selftest(opts, archive, tz, emhass_version)
    except Exception as exc:  # noqa: BLE001
        log.warning("selftest lanț eșuat: %r", exc)
    chain = perfect.Chain(root)
    next_chain = 0.0
    last_forced = 0.0
    while True:
        if time.time() >= next_chain:
            next_chain = time.time() + int(opts["chain_every_seconds"])
            try:
                run_chain(opts, archive, chain, tz, emhass_version)
            except Exception as exc:  # noqa: BLE001 — lanțul nu trebuie să oprească arhivarea
                log.warning("chain: %r", exc)
            try:
                publish_scoreboard(ha, archive, chain, tz, emhass_version)
            except Exception as exc:  # noqa: BLE001
                log.warning("publish: %r", exc)
            if chain.state is not None:
                try:
                    run_experiments(opts, archive, tz, emhass_version)
                    publish_experiments(ha, archive, chain, tz, emhass_version, opts)
                except Exception as exc:  # noqa: BLE001 — experimentele nu trebuie să afecteze lanțul principal
                    log.warning("experimente: %r", exc)
        try:
            st = ha.state(opts["capture_entity"])
            if ingest(archive, st["state"], st.get("attributes", {}), tz):
                log.info("slot nou arhivat: %s", st["attributes"].get("slot_start_epoch"))
            last_err = None
            # HA a repornit și a pierdut senzorii publicați prin REST: republică imediat, nu după următorul ciclu de 300 s.
            if time.time() - last_forced >= REPUBLISH_MIN_GAP and next_chain > time.time() \
                    and published_entities_missing(ha, chain):
                last_forced = time.time()
                next_chain = 0.0
                log.info("senzori publicați lipsă din HA (repornire?): republicare imediată")
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
            chain=chain.state,
        )
        time.sleep(int(opts["poll_seconds"]))


if __name__ == "__main__":
    main()
