"""Scoreboard Perfect World: cumul pe aceeași perioadă pentru toate lumile, publicat ca senzor HA.
Convenție (ca v0.2.7/v0.2.8): valoare = -(cost + pierdere fixă) - inventar SOC, pozitiv = mai bine.
Perfect World e în urmă cu ~1 zi (fără rezultate provizorii), deci lumile reale se însumează DOAR pe
sloturile deja evaluate în Perfect World: comparație pe aceeași perioadă."""
import json
import os

BATT_KWH = 10.24
ETA = 0.99 * 0.97
HYDRO_V2_IMPORT, HYDRO_V2_EXPORT = 1.23597, 0.515
DEFAULT_LOSS = 0.0295
ENTITY = "sensor.emhass_perfect_world_scoreboard"
LOSS_ENTITY = "sensor.deye_economic_auditor_v0_2_7_fixed_loss_accumulator"


def _num(v, default=0.0):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _inv(soc_real, soc_world, px):
    if soc_real is None or soc_world is None:
        return 0.0
    return (soc_real - soc_world) / 100 * BATT_KWH * ETA * px


def _pct(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def summarize(chain_rows, archive_by_epoch, loss_kwh, emhass_version):
    """chain_rows: rânduri Perfect World (non-skipped, scored). Returnează dict cu valori pe lume."""
    rows = sorted((r for r in chain_rows if not r.get("skipped") and r.get("scored")),
                  key=lambda r: r["slot_start_epoch"])
    if not rows:
        return None
    w = {k: {"cost": 0.0, "loss": 0.0, "imp": 0.0, "exp": 0.0}
         for k in ("PERFECT", "REAL", "STOCK_PPC", "STOCK_HYDRO_V2")}
    last = None
    n = 0
    for r in rows:
        a = archive_by_epoch.get(r["slot_start_epoch"])
        if a is None:
            continue
        pi, pe = _num(a.get("ppc_import_price_ron_kwh")), _num(a.get("ppc_export_price_ron_kwh"))
        if pi <= 0:
            continue
        n += 1
        last = (r, a, pe)
        w["PERFECT"]["cost"] += r["net_cost_ron"]
        w["PERFECT"]["loss"] += loss_kwh * (pi if r["import_kwh"] > 0 else pe)
        w["PERFECT"]["imp"] += r["import_kwh"]
        w["PERFECT"]["exp"] += r["export_kwh"]
        w["REAL"]["cost"] += _num(a.get("actual_ppc_net_cost_ron"))
        w["REAL"]["imp"] += _num(a.get("actual_grid_import_kwh"))
        w["REAL"]["exp"] += _num(a.get("actual_grid_export_kwh"))
        simp = _num(a.get("stock_grid_import_kwh")) > 0
        w["STOCK_PPC"]["cost"] += _num(a.get("stock_ppc_net_cost_ron"))
        w["STOCK_PPC"]["loss"] += loss_kwh * (pi if simp else pe)
        w["STOCK_PPC"]["imp"] += _num(a.get("stock_grid_import_kwh"))
        w["STOCK_PPC"]["exp"] += _num(a.get("stock_grid_export_kwh"))
        w["STOCK_HYDRO_V2"]["cost"] += _num(a.get("stock_hydro_v2_net_cost_ron"))
        w["STOCK_HYDRO_V2"]["loss"] += loss_kwh * (HYDRO_V2_IMPORT if simp else HYDRO_V2_EXPORT)
    if last is None:
        return None
    r, a, pe = last
    soc_real = _pct(a.get("actual_soc_end_pct"))
    soc_stock = _pct(a.get("stock_soc_end_pct"))
    soc = {"PERFECT": r["soc_end"] * 100, "REAL": soc_real, "STOCK_PPC": soc_stock, "STOCK_HYDRO_V2": soc_stock}
    px = {"PERFECT": pe, "REAL": pe, "STOCK_PPC": pe, "STOCK_HYDRO_V2": HYDRO_V2_EXPORT}
    out = {}
    for k, v in w.items():
        inv = 0.0 if k == "REAL" else _inv(soc_real, soc[k], px[k])
        out[k] = {
            "cost_ron": round(v["cost"], 3), "fixed_loss_ron": round(v["loss"], 3),
            "inventory_ron": round(inv, 3), "value_ron": round(-(v["cost"] + v["loss"]) - inv, 3),
            "import_kwh": round(v["imp"], 3), "export_kwh": round(v["exp"], 3),
            "soc_end_pct": None if soc[k] is None else round(soc[k], 1),
        }
    return {
        "emhass_version": emhass_version, "era": f"emhass-{emhass_version}",
        "slots_counted": n, "through_slot_epoch": r["slot_start_epoch"],
        "from_slot_epoch": rows[0]["slot_start_epoch"], "loss_kwh_per_slot": loss_kwh,
        "worlds": out,
        "value_ron": out["PERFECT"]["value_ron"],
        "delta_vs_real_ron": round(out["PERFECT"]["value_ron"] - out["REAL"]["value_ron"], 3),
        "delta_vs_stock_ppc_ron": round(out["PERFECT"]["value_ron"] - out["STOCK_PPC"]["value_ron"], 3),
        "method": "LAB_SEQUENTIAL_MPC_REAL_PV_LOAD_SAME_PERIOD",
    }


def flatten(s):
    """Atribute plate pentru dashboard (HA nu randează ușor dict-uri imbricate)."""
    a = {k: v for k, v in s.items() if k not in ("worlds", "value_ron")}
    for name, d in s["worlds"].items():
        for k, v in d.items():
            a[f"{name.lower()}_{k}"] = v
    return a


def write(chain_dir, s):
    with open(os.path.join(chain_dir, "scoreboard.json"), "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=1, sort_keys=True)
