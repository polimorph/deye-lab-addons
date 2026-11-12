"""Variante experimentale ale țintei de SOC la sfârșitul orizontului (soc_final și battery_soc_final_reward_factor).
Fiecare variantă e un lanț Perfect World separat (același arhiv, aceeași fizică măsurată, același start), cu config și
soc_final proprii DOAR în laborator. Nu intră în scoreboard-ul principal și nu ating producția."""
import logging
import os
import shutil

from . import adapter, perfect

DIAG_VERSION = 2   # 2 = rândurile conțin plan_soc_horizon_end/min; la schimbare lanțurile experimentale se reiau de la start
VARIANTS = (
    {"id": "sf30_r0", "label": "soc_final 0,3 · recompensă 0 (replică baseline)", "soc_final": 0.3, "reward": 0.0},
    {"id": "sf20_r0", "label": "soc_final 0,2 · recompensă 0", "soc_final": 0.2, "reward": 0.0},
    {"id": "sf30_r50", "label": "soc_final 0,3 · recompensă 0,5", "soc_final": 0.3, "reward": 0.5},
    {"id": "sf30_r70", "label": "soc_final 0,3 · recompensă 0,7", "soc_final": 0.3, "reward": 0.7},
    {"id": "sf30_r100", "label": "soc_final 0,3 · recompensă 1,0", "soc_final": 0.3, "reward": 1.0},
    {"id": "sf50_r0", "label": "soc_final 0,5 · recompensă 0", "soc_final": 0.5, "reward": 0.0},
    # Tiparul din documentația EMHASS pentru MPC rulant: soc_final 0 = fără țintă efectivă la capătul orizontului.
    {"id": "sf0_r0", "label": "soc_final 0 · recompensă 0", "soc_final": 0.0, "reward": 0.0},
)
REWARD_KEY = "battery_soc_final_reward_factor"


def chain_dir(root, vid):
    return os.path.join(root, "experiments", vid)


def overrides(v):
    return {REWARD_KEY: v["reward"]}


async def advance_variant(root, archive, start, tz, emhass_version, v, log=None, max_slots=60):
    """Un pas pentru o variantă. start = (epoch, soc) al lanțului principal; întoarce numărul de sloturi procesate."""
    chain = perfect.Chain(chain_dir(root, v["id"]))
    if chain.state is not None and chain.state.get("diag") != DIAG_VERSION:
        shutil.rmtree(chain.dir, ignore_errors=True)       # rânduri vechi fără diagnostic: reluare curată (doar copia laboratorului)
        chain = perfect.Chain(chain_dir(root, v["id"]))
    if chain.state is None:
        if start is None:
            return 0
        chain.init(*start)
        chain.state["diag"] = DIAG_VERSION
        chain.save()
    clog = logging.getLogger("lab.exp." + v["id"])
    emhass_conf, config, params = await adapter.make_context(
        root, clog, overrides=overrides(v), name="emhass_data_" + v["id"])
    if float(config.get(REWARD_KEY, -1)) != float(v["reward"]):      # suprascrierea trebuie să ajungă în config
        raise RuntimeError(f"{v['id']}: {REWARD_KEY}={config.get(REWARD_KEY)!r}, așteptat {v['reward']}")
    solver = adapter.first_row_solver(emhass_conf, params, clog)
    return await perfect.advance(
        chain, archive, tz, solver, emhass_version, max_slots=max_slots, log=log, soc_final=v["soc_final"],
        row_extra={"config_hash": adapter.config_hash(config), "variant": v["id"],
                   "soc_final": v["soc_final"], "reward_factor": v["reward"]})
