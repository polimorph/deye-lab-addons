"""Lanț Perfect World: același MPC ca producția, PV/load REALE, SOC propriu al laboratorului, slot cu slot.
Un slot se rezolvă doar când arhiva acoperă tot orizontul de producție (fără rezultate provizorii)."""
import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import adapter, physics
from .schema import SLOT_SECONDS, ScenarioResult, expected_slots, num

SCENARIO = "EMHASS_PPC_PERFECT_PV_LOAD"
PRICES_PUBLISHED_HOUR = 14   # după ora 14:00 locală, producția cunoaște prețurile zilei următoare
SOC_MIN, SOC_MAX = physics.MEASURED.soc_min, physics.MEASURED.soc_max
MIN_HORIZON = 8              # ca în producție (sub 8 sloturi nu se optimizează)


def horizon_end_epoch(slot_epoch, tz):
    """Capătul orizontului de producție: sfârșitul zilei locale (până la 14:00) sau al zilei următoare (după)."""
    z = ZoneInfo(tz)
    local = datetime.fromtimestamp(slot_epoch, timezone.utc).astimezone(z)
    days = 1 if local.hour < PRICES_PUBLISHED_HOUR else 2
    end_date = local.date() + timedelta(days=days)
    return int(datetime(end_date.year, end_date.month, end_date.day, tzinfo=z).timestamp())


class Chain:
    """Starea lanțului, persistentă în data_dir/results/chain.json."""

    def __init__(self, data_dir):
        self.dir = os.path.join(data_dir, "results")
        os.makedirs(os.path.join(self.dir, "slots"), exist_ok=True)
        self.path = os.path.join(self.dir, "chain.json")
        try:
            with open(self.path, encoding="utf-8") as fh:
                self.state = json.load(fh)
        except (OSError, ValueError):
            self.state = None

    def init(self, next_epoch, soc):
        self.state = {"next_epoch": int(next_epoch), "soc": float(soc), "curtail": False, "slots_done": 0}
        self.save()

    def save(self):
        with open(self.path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, indent=1, sort_keys=True)
        os.replace(self.path + ".tmp", self.path)

    def append_slot(self, day, row):
        with open(os.path.join(self.dir, "slots", f"{day}.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def read_slots(self, day):
        out = {}
        try:
            with open(os.path.join(self.dir, "slots", f"{day}.jsonl"), encoding="utf-8") as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                        out[r["slot_start_epoch"]] = r
                    except ValueError:
                        continue
        except FileNotFoundError:
            pass
        return [out[k] for k in sorted(out)]


def realize(first, slot0, soc0, phys=physics.MEASURED):
    """Primul pas al planului EMHASS -> energii realizate pe slot cu FIZICA MĂSURATĂ (physics.py), nu cu credința planificatorului.
    Din plan se păstrează fluxurile bateriei pe bus-ul DC și curtailment-ul; PV și consumul sunt cele reale;
    rețeaua și SOC-ul rezultă din eficiențele măsurate. Planul inițial rămâne în rând (plan_*), pentru diagnostic."""
    p_imp, p_exp = float(slot0["ppc_import_price_ron_kwh"]), float(slot0["ppc_export_price_ron_kwh"])
    plan_grid = float(first["P_grid"])             # W; negativ = export
    batt_w = float(first["P_batt"])                # W; pozitiv = descărcare (pe bus DC)
    pv = num(slot0.get("pv_actual_kwh")) or 0.0
    load = num(slot0.get("load_kwh")) or 0.0
    curtailed = min(float(first.get("P_PV_curtailment", 0.0)) * 0.25 / 1000, pv)
    c_plan, d_plan = max(-batt_w, 0.0) * 0.25 / 1000, max(batt_w, 0.0) * 0.25 / 1000
    imp, exp, soc_end, c, d = physics.realize_flows(phys, soc0, pv - curtailed, load, c_plan, d_plan)
    return {
        "import_kwh": round(imp, 6) + 0.0, "export_kwh": round(exp, 6) + 0.0,
        "batt_charge_kwh": round(c, 6) + 0.0, "batt_discharge_kwh": round(d, 6) + 0.0,
        "pv_curtailed_kwh": round(curtailed, 6),
        "net_cost_ron": round(imp * p_imp - exp * p_exp, 6) + 0.0,
        "soc_end": soc_end,
        "physics": phys.label,
        "plan_import_kwh": round(max(plan_grid, 0.0) * 0.25 / 1000, 6) + 0.0,
        "plan_export_kwh": round(max(-plan_grid, 0.0) * 0.25 / 1000, 6) + 0.0,
        "plan_soc_end": float(first["SOC_opt"]),
        "battery_limited": bool(c < c_plan - 1e-9 or d < d_plan - 1e-9),
    }


async def advance(chain, archive, tz, solve_first_row, emhass_version, max_slots=60, log=None, row_extra=None):
    """Rezolvă sloturile al căror orizont e acoperit de arhivă, în ordine. Returnează numărul de sloturi procesate."""
    rows = []
    for d in archive.days():
        rows += archive.read_day(d)
    rows.sort(key=lambda r: r["slot_start_epoch"])
    if not rows or chain.state is None:
        return 0
    last_arch = rows[-1]["slot_start_epoch"]
    done = 0
    while done < max_slots:
        s = chain.state["next_epoch"]
        n = min((horizon_end_epoch(s, tz) - s) // SLOT_SECONDS, adapter.MAX_HORIZON)
        if s + 900 * (n - 1) > last_arch:
            break                                  # orizontul nu e încă acoperit: așteptăm (fără provizorii)
        slots = adapter.slot_inputs(rows, s, n)
        truncated = len(slots) < n
        if len(slots) < MIN_HORIZON:               # gol real în arhivă: sărim slotul, SOC-ul rămâne
            row = {"slot_start_epoch": s, "skipped": True, "reason": "ARCHIVE_GAP", "emhass_version": emhass_version}
        else:
            soc = min(max(chain.state["soc"], SOC_MIN), SOC_MAX)
            runtime, latch = adapter.build_runtime(slots, soc, chain.state["curtail"], perfect=True)
            first = await solve_first_row(runtime)
            row = realize(first, slots[0], soc)
            row.update(
                slot_start_epoch=s, skipped=False, emhass_version=emhass_version, era=f"emhass-{emhass_version}",
                horizon=len(slots), horizon_truncated=truncated, scored=not slots[0]["price_filled"],
                soc_start=soc, optim_status=str(first.get("optim_status", "")),
            )
            chain.state["soc"] = row["soc_end"]
            chain.state["curtail"] = latch
        day = datetime.fromtimestamp(s, timezone.utc).astimezone(ZoneInfo(tz)).date().isoformat()
        row["day_local"] = day
        row.update(row_extra or {})
        chain.append_slot(day, row)
        chain.state["next_epoch"] = s + SLOT_SECONDS
        chain.state["slots_done"] = chain.state.get("slots_done", 0) + 1
        chain.save()
        done += 1
        if log and done % 20 == 0:
            log.info("perfect: %d sloturi, la %s", done, day)
    return done


def day_result(chain, day, tz, emhass_version):
    """Agregat pe zi (doar sloturi evaluate). Zi completă = toate sloturile zilei procesate."""
    rows = [r for r in chain.read_slots(day)]
    ok = [r for r in rows if not r.get("skipped") and r.get("scored")]
    if not ok:
        return None
    res = ScenarioResult(
        day_local=day, scenario_id=SCENARIO, emhass_version=emhass_version, slots=len(ok),
        net_cost_ron=round(sum(r["net_cost_ron"] for r in ok), 6),
        grid_import_kwh=round(sum(r["import_kwh"] for r in ok), 6),
        grid_export_kwh=round(sum(r["export_kwh"] for r in ok), 6),
        soc_start_pct=round(rows[0]["soc_start"] * 100, 2) if "soc_start" in rows[0] else 0.0,
        soc_end_pct=round(rows[-1]["soc_end"] * 100, 2) if "soc_end" in rows[-1] else 0.0,
        method="LAB_SEQUENTIAL_MPC_REAL_PV_LOAD",
    )
    d = json.loads(res.to_json())
    d["complete"] = len(rows) == expected_slots(day, tz)
    d["slots_total"] = len(rows)
    d["slots_skipped"] = sum(1 for r in rows if r.get("skipped"))
    d["slots_price_filled"] = sum(1 for r in rows if not r.get("skipped") and not r.get("scored"))
    d["horizon_truncated_slots"] = sum(1 for r in rows if r.get("horizon_truncated"))
    return d


def write_day_results(chain, tz, emhass_version, days):
    out = {}
    for day in days:
        d = day_result(chain, day, tz, emhass_version)
        if d:
            out[day] = d
            with open(os.path.join(chain.dir, f"day_{day}.json"), "w", encoding="utf-8") as fh:
                json.dump(d, fh, indent=1, sort_keys=True)
    return out
