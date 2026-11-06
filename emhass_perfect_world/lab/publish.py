"""Scoreboard Perfect World: cumul pe aceeași perioadă pentru toate lumile, publicat ca senzor HA.
Convenție (ca v0.2.7/v0.2.8): valoare = -(cost + pierdere fixă) - inventar SOC, pozitiv = mai bine.
Perfect World e în urmă cu ~1 zi (fără rezultate provizorii), deci lumile reale se însumează DOAR pe
sloturile deja evaluate în Perfect World: comparație pe aceeași perioadă."""
import json
import os

from . import physics
from .schema import num

PHYS = physics.MEASURED   # aceeași fizică în Perfect World, replay STOCK și inventar
HYDRO_V2_IMPORT, HYDRO_V2_EXPORT = 1.23597, 0.515
DEFAULT_LOSS = 0.0295
ENTITY = "sensor.emhass_perfect_world_scoreboard"
LOSS_ENTITY = "sensor.deye_economic_auditor_v0_2_7_fixed_loss_accumulator"
# Salt de contor în arhivă: exportul măsurat nu poate depăsi ce poate produce fizic invertorul în slot
# (descărcare + PV − încărcare − consum + import). Calibrat pe 26.10–05.11: cele 6 salturi reale (1.9–11.2 kWh
# într-un slot) depășesc plafonul cu 0.5–10 kWh, restul sloturilor cu < 0.3 kWh.
EXPORT_MIN_KWH = 0.5
EXPORT_TOL_KWH = 0.4


def _num(v, default=0.0):
    f = num(v)
    return default if f is None else f


def counter_anomaly(a):
    """True dacă exportul din arhivă e imposibil fizic (salt de contor). Slotul se exclude din TOATE lumile."""
    exp = _num(a.get("actual_grid_export_kwh"))
    if exp <= EXPORT_MIN_KWH:
        return False
    bound = (_num(a.get("actual_battery_discharge_kwh")) + _num(a.get("pv_actual_kwh"))
             - _num(a.get("actual_battery_charge_kwh")) - _num(a.get("load_kwh"))
             + _num(a.get("actual_grid_import_kwh")))
    return exp > bound + EXPORT_TOL_KWH


def _inv(real_end, world_end, real_start, world_start, px, phys=PHYS):
    """Valoarea energiei rămase în baterie față de lumea reală, corectată cu diferența de la începutul perioadei
    (toate lumile pornesc din același SOC doar dacă nu s-a resetat replay-ul; altfel diferența inițială nu e câștig)."""
    if None in (real_end, world_end):
        return 0.0
    d = real_end - world_end
    if real_start is not None and world_start is not None:
        d -= real_start - world_start
    return d / 100 * phys.capacity_kwh * phys.eta_discharge_to_ac * px


def _pct(v):
    return num(v)


def summarize(chain_rows, archive_by_epoch, loss_kwh, emhass_version, stock=None, phys=PHYS):
    """chain_rows: rânduri Perfect World (non-skipped, scored). Returnează dict cu valori pe lume.
    STOCK_* = replay-ul STOCK din laborator (physics.stock_replay) cu fizica `phys`, nu valorile `stock_*` din arhivă
    (acelea folosesc eficiențele vechi ale auditorului)."""
    if stock is None:
        stock = physics.stock_replay(list(archive_by_epoch.values()), phys)
    rows = sorted((r for r in chain_rows if not r.get("skipped") and r.get("scored")),
                  key=lambda r: r["slot_start_epoch"])
    if not rows:
        return None
    w = {k: {"cost": 0.0, "loss": 0.0, "imp": 0.0, "exp": 0.0}
         for k in ("PERFECT", "REAL", "STOCK_PPC", "STOCK_HYDRO_V2")}
    last = first = None
    n = 0
    excluded = []
    for r in rows:
        a = archive_by_epoch.get(r["slot_start_epoch"])
        if a is None:
            continue
        if counter_anomaly(a):
            excluded.append(r["slot_start_epoch"])
            continue
        pi, pe = _num(a.get("ppc_import_price_ron_kwh")), _num(a.get("ppc_export_price_ron_kwh"))
        st = stock.get(r["slot_start_epoch"])
        if pi <= 0 or st is None:
            continue
        n += 1
        last = (r, a, pe, st)
        first = first or (r, a, st)
        w["PERFECT"]["cost"] += r["net_cost_ron"]
        w["PERFECT"]["loss"] += loss_kwh * (pi if r["import_kwh"] > 0 else pe)
        w["PERFECT"]["imp"] += r["import_kwh"]
        w["PERFECT"]["exp"] += r["export_kwh"]
        w["REAL"]["cost"] += _num(a.get("actual_ppc_net_cost_ron"))
        w["REAL"]["imp"] += _num(a.get("actual_grid_import_kwh"))
        w["REAL"]["exp"] += _num(a.get("actual_grid_export_kwh"))
        simp = st["import"] > 0
        w["STOCK_PPC"]["cost"] += st["import"] * pi - st["export"] * pe
        w["STOCK_PPC"]["loss"] += loss_kwh * (pi if simp else pe)
        w["STOCK_PPC"]["imp"] += st["import"]
        w["STOCK_PPC"]["exp"] += st["export"]
        w["STOCK_HYDRO_V2"]["cost"] += st["import"] * HYDRO_V2_IMPORT - st["export"] * HYDRO_V2_EXPORT
        w["STOCK_HYDRO_V2"]["imp"] += st["import"]
        w["STOCK_HYDRO_V2"]["exp"] += st["export"]
        w["STOCK_HYDRO_V2"]["loss"] += loss_kwh * (HYDRO_V2_IMPORT if simp else HYDRO_V2_EXPORT)
    if last is None:
        return None
    r, a, pe, st = last
    pi_last = _num(a.get("ppc_import_price_ron_kwh"))
    r0, a0, st0 = first
    soc_real, soc_real0 = _pct(a.get("actual_soc_end_pct")), _pct(a0.get("actual_soc_start_pct"))
    soc = {"PERFECT": r["soc_end"] * 100, "REAL": soc_real, "STOCK_PPC": st["soc_end"], "STOCK_HYDRO_V2": st["soc_end"]}
    soc0 = {"PERFECT": r0["soc_start"] * 100 if "soc_start" in r0 else None, "REAL": soc_real0,
            "STOCK_PPC": st0["soc_start"], "STOCK_HYDRO_V2": st0["soc_start"]}
    px = {"PERFECT": pe, "REAL": pe, "STOCK_PPC": pe, "STOCK_HYDRO_V2": HYDRO_V2_EXPORT}
    px_imp = {"PERFECT": pi_last, "REAL": pi_last, "STOCK_PPC": pi_last, "STOCK_HYDRO_V2": HYDRO_V2_IMPORT}
    out = {}
    for k, v in w.items():
        inv = 0.0 if k == "REAL" else _inv(soc_real, soc[k], soc_real0, soc0[k], px[k], phys)
        # limită superioară: energia rămasă în baterie evaluată la prețul de import (evită importul de mâine)
        inv_i = 0.0 if k == "REAL" else _inv(soc_real, soc[k], soc_real0, soc0[k], px_imp[k], phys)
        out[k] = {
            "value_inv_at_import_ron": round(-(v["cost"] + v["loss"]) - inv_i, 3),
            "cost_ron": round(v["cost"], 3), "fixed_loss_ron": round(v["loss"], 3),
            "inventory_ron": round(inv, 3), "value_ron": round(-(v["cost"] + v["loss"]) - inv, 3),
            "import_kwh": round(v["imp"], 3), "export_kwh": round(v["exp"], 3),
            "soc_end_pct": None if soc[k] is None else round(soc[k], 1),
        }
    return {
        "emhass_version": emhass_version, "era": f"emhass-{emhass_version}",
        "slots_counted": n, "excluded_counter_anomaly_slots": len(excluded),
        "excluded_counter_anomaly_epochs": excluded, "through_slot_epoch": r["slot_start_epoch"],
        "from_slot_epoch": rows[0]["slot_start_epoch"], "loss_kwh_per_slot": loss_kwh, "physics": phys.label,
        "eta_batt_ch": phys.eta_ch, "eta_batt_dis": phys.eta_dis, "eta_dc_ac": phys.eta_dc_ac, "eta_ac_dc": phys.eta_ac_dc,
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


def _delta_all_slots(base_rows, var_rows, archive_by_epoch, loss_kwh, phys=PHYS):
    """Δ variantă − referință între două lanțuri Perfect World (aceleași prețuri, aceeași fizică), pe TOATE sloturile
    procesate de ambele, inclusiv cele cu preț completat (scored=false). Motiv: la egalitate de preț între sloturi
    vecine, solver-ul poate muta un export într-un slot exclus din scorare; comparația cu REAL/STOCK rămâne pe sloturile
    scorate, dar între variante nu are sens să pierdem acel venit. Valoare pozitivă = varianta câștigă."""
    ok = lambda rows: {r["slot_start_epoch"]: r for r in rows if not r.get("skipped")}
    b, v = ok(base_rows), ok(var_rows)
    d_cost = d_loss = 0.0
    n, last = 0, None
    for e in sorted(set(b) & set(v)):
        a = archive_by_epoch.get(e)
        if a is None or counter_anomaly(a):
            continue
        pi, pe = _num(a.get("ppc_import_price_ron_kwh")), _num(a.get("ppc_export_price_ron_kwh"))
        if pi <= 0:
            continue
        n += 1
        last = (b[e], v[e], pi, pe)
        d_cost += v[e]["net_cost_ron"] - b[e]["net_cost_ron"]
        d_loss += loss_kwh * (pi if v[e]["import_kwh"] > 0 else pe) - loss_kwh * (pi if b[e]["import_kwh"] > 0 else pe)
    if last is None:
        return {}
    rb, rv, pi, pe = last
    d_soc = (rb["soc_end"] - rv["soc_end"]) * 100            # puncte procentuale: referința are mai mult => varianta pierde
    inv = lambda px: d_soc / 100 * phys.capacity_kwh * phys.eta_discharge_to_ac * px
    return {
        "slots_all": n,
        "delta_all_slots_ron": round(-(d_cost + d_loss) - inv(pe), 3),
        "delta_all_slots_inv_at_import_ron": round(-(d_cost + d_loss) - inv(pi), 3),
    }


def summarize_experiments(main_rows, variants, archive_by_epoch, loss_kwh, emhass_version):
    """Variante experimentale (soc_final / recompensă) față de Perfect World principal, pe ACELEAȘI sloturi.
    variants: listă de (meta_dict, rânduri_lanț). Nu intră în scoreboard-ul principal."""
    def ok(rows):
        return {r["slot_start_epoch"] for r in rows if not r.get("skipped") and r.get("scored")}
    common = ok(main_rows)
    for _, rows in variants:
        common &= ok(rows)
    if not common:
        return None
    pick = lambda rows: [r for r in rows if r["slot_start_epoch"] in common]
    base = summarize(pick(main_rows), archive_by_epoch, loss_kwh, emhass_version)
    if base is None:
        return None
    bw = base["worlds"]["PERFECT"]
    out = {}
    for meta, rows in variants:
        s = summarize(pick(rows), archive_by_epoch, loss_kwh, emhass_version)
        if s is None or s["slots_counted"] != base["slots_counted"]:
            continue
        w = s["worlds"]["PERFECT"]
        out[meta["id"]] = dict(
            w, label=meta["label"], soc_final=meta["soc_final"], reward_factor=meta["reward"],
            delta_vs_baseline_ron=round(w["value_ron"] - bw["value_ron"], 3),
            delta_vs_baseline_inv_at_import_ron=round(w["value_inv_at_import_ron"] - bw["value_inv_at_import_ron"], 3),
        )
    for meta, rows in variants:                     # Δ pe TOATE sloturile procesate (vezi _delta_all_slots)
        if meta["id"] in out:
            out[meta["id"]].update(_delta_all_slots(main_rows, rows, archive_by_epoch, loss_kwh))
    return {
        "emhass_version": emhass_version, "slots_counted": base["slots_counted"],
        "from_slot_epoch": base["from_slot_epoch"], "through_slot_epoch": base["through_slot_epoch"],
        "physics": base["physics"], "loss_kwh_per_slot": loss_kwh,
        "baseline": bw, "real": base["worlds"]["REAL"], "stock_ppc": base["worlds"]["STOCK_PPC"],
        "variants": out,
        "method": "EXPERIMENTAL_SOC_FINAL_VARIANTS_SAME_PERIOD",
    }


def flatten_experiments(s):
    a = {k: v for k, v in s.items() if k not in ("baseline", "real", "stock_ppc", "variants")}
    for name in ("baseline", "real", "stock_ppc"):
        for k, v in s[name].items():
            a[f"{name}_{k}"] = v
    for vid, d in s["variants"].items():
        for k, v in d.items():
            a[f"{vid}_{k}"] = v
    a["variant_ids"] = list(s["variants"])
    return a


def write_experiments(root, s):
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=1, sort_keys=True)
