"""Adaptor EMHASS: TOT ce depinde de API-ul intern al lui EMHASS stă aici.
Config = copia configului LIVE de producție (GET /get-config), nu o copie scrisă de mână.
Nu scrie nimic în /share/emhass: datele EMHASS ale laboratorului stau în data_dir/emhass_data."""
import hashlib
import json
import logging
import os
import pathlib
import urllib.request

from .schema import num

PROD_API = "http://5b918bf2-emhass:5000"
KWH_TO_W = 4000.0          # kWh pe slot de 15 min -> W mediu
CURTAIL_ENTER, CURTAIL_EXIT, CURTAIL_TIEBREAK = 0.025, 0.035, -0.001   # ca în MPC v4.5 de producție
SOC_FINAL = 0.3            # ca în producție
MAX_HORIZON = 192


def fetch_prod_config(timeout=15):
    with urllib.request.urlopen(PROD_API + "/get-config", timeout=timeout) as resp:
        return json.load(resp)


async def make_context(data_dir, logger, overrides=None, name="emhass_data"):
    """Construiește (emhass_conf, params) din configul live. Doar citire de la producție.
    `overrides`: chei de config suprascrise DOAR în copia laboratorului (variantele experimentale); producția nu se atinge."""
    import emhass
    import emhass.utils as u

    root = pathlib.Path(emhass.__file__).parent
    work = pathlib.Path(data_dir) / name
    work.mkdir(parents=True, exist_ok=True)
    cfg_path = work / "config.json"
    cfg = fetch_prod_config()
    cfg.update(overrides or {})
    cfg_path.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    emhass_conf = {
        "config_path": cfg_path,
        "options_path": pathlib.Path("/data/options.json"),
        "defaults_path": root / "data" / "config_defaults.json",
        "associations_path": root / "data" / "associations.csv",
        "legacy_config_path": pathlib.Path("/nonexistent/config_emhass.yaml"),
        "data_path": work,
        "root_path": root,
        "secrets_path": pathlib.Path("/nonexistent/secrets_emhass.yaml"),
    }
    config = await u.build_config(emhass_conf, logger, str(emhass_conf["defaults_path"]), str(cfg_path), None)
    emhass_conf, secrets = await u.build_secrets(
        emhass_conf, logger, options_path=str(emhass_conf["options_path"]),
        secrets_path=str(emhass_conf["secrets_path"]), no_response=False,
    )
    params = await u.build_params(emhass_conf, secrets, config, logger)
    return emhass_conf, config, params


def _num(v):
    return num(v) is not None


NUM_KEYS = ("load_kwh", "pv_actual_kwh", "ppc_import_price_ron_kwh", "ppc_export_price_ron_kwh")
MAX_PRICE_GAP = 4   # cel mult atâtea sloturi consecutive cu preț lipsă sunt completate


def slot_inputs(rows, start_epoch, n):
    """Sloturi consecutive din arhivă începând de la start_epoch; se oprește la prima lipsă de load/PV.
    Prețurile lipsă (capturile auditorului au 'unknown' la unele limite de slot) se completează cu cel mai apropiat
    preț valid, DOAR ca intrare pentru solver. Slotul primește `price_filled=True`: costul lui nu se evaluează
    (în auditor aceleași sloturi au cost 0 pentru toate lumile)."""
    by = {r["slot_start_epoch"]: r for r in rows}
    seq = []
    for k in range(n):
        r = by.get(start_epoch + 900 * k)
        if not r or not all(_num(r.get(x)) for x in ("load_kwh", "pv_actual_kwh")):
            break
        seq.append({k: (num(v) if k in NUM_KEYS and num(v) is not None else v) for k, v in r.items()})
    ok = [all(_num(r.get(x)) for x in ("ppc_import_price_ron_kwh", "ppc_export_price_ron_kwh")) for r in seq]
    out = []
    for i, r in enumerate(seq):
        r["price_filled"] = not ok[i]
        if not ok[i]:
            near = next((j for d in range(1, MAX_PRICE_GAP + 1) for j in (i - d, i + d) if 0 <= j < len(seq) and ok[j]), None)
            if near is None:
                break   # gol prea lung: oprim secvența aici
            r["ppc_import_price_ron_kwh"] = seq[near]["ppc_import_price_ron_kwh"]
            r["ppc_export_price_ron_kwh"] = seq[near]["ppc_export_price_ron_kwh"]
        out.append(r)
    return out


def build_runtime(slots, soc_init, curtail_prev=False, perfect=True, haircut=0.0, soc_final=SOC_FINAL):
    """Payload ca MPC v4.5 de producție, dar cu PV/load REALE (perfect=True).
    Fără scalare PV și fără taxă de risc la export când perfect=True (acoperă eroarea de prognoză, inexistentă aici).
    Returnează (runtime, curtail_state_after_first_slot)."""
    imp, exp, pv, load = [], [], [], []
    curtail = curtail_prev
    first_state = curtail_prev
    for i, r in enumerate(slots):
        p_exp = float(r["ppc_export_price_ron_kwh"])
        # histerezis de curtailment economic, exact ca în producție
        if curtail:
            if p_exp >= CURTAIL_EXIT:
                curtail = False
        elif p_exp <= CURTAIL_ENTER:
            curtail = True
        if i == 0:
            first_state = curtail
        if curtail:
            p_emhass = CURTAIL_TIEBREAK
        elif haircut > 0 and p_exp >= CURTAIL_EXIT + haircut:
            p_emhass = round(p_exp - haircut, 9)
        else:
            p_emhass = p_exp
        imp.append(round(float(r["ppc_import_price_ron_kwh"]), 9))
        exp.append(p_emhass)
        pv.append(round(float(r["pv_actual_kwh"]) * KWH_TO_W, 3))
        load.append(round(float(r["load_kwh"]) * KWH_TO_W, 3))
    runtime = {
        "load_cost_forecast": imp,
        "prod_price_forecast": exp,
        "pv_power_forecast": pv,
        "load_power_forecast": load,
        "set_use_adjusted_pv": False,
        "alpha": 1, "beta": 0,
        "optimization_time_step": 15,
        "prediction_horizon": len(slots),
        "soc_init": round(float(soc_init), 4),
        "soc_final": soc_final,
        "lp_solver_mip_rel_gap": 0,
    }
    return runtime, first_state


async def solve(emhass_conf, params, runtime, logger):
    """O optimizare naive-mpc-optim; întoarce DataFrame-ul EMHASS (rândul 0 = slotul curent)."""
    from emhass import command_line as cli

    inp = await cli.set_input_data_dict(
        emhass_conf, "profit", json.dumps(params), json.dumps(runtime), "naive-mpc-optim", logger,
        get_data_from_file=False,
    )
    if not inp:
        raise RuntimeError("set_input_data_dict a întors gol (vezi logul EMHASS)")
    try:
        return await cli.naive_mpc_optim(inp, logger, save_data_to_file=False)
    finally:
        rh = inp.get("rh") if isinstance(inp, dict) else None
        if rh is not None and hasattr(rh, "close"):
            await rh.close()


def config_hash(config):
    """Amprenta configului EMHASS live: o schimbare de config în lanț trebuie să fie vizibilă pe rânduri."""
    return hashlib.sha1(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest()[:12]


def first_row_solver(emhass_conf, params, logger):
    """Funcție async runtime -> primul rând al planului (dict), pentru lanțul slot cu slot."""
    async def run(runtime):
        df = await solve(emhass_conf, params, runtime, logger)
        return df.iloc[0].to_dict()
    return run
