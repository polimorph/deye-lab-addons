"""Un solve de probă pe o zi reală din arhivă; scrie data_dir/solve_probe.json (coloane, primele rânduri, erori)."""
import asyncio
import json
import logging
import os
import time
import traceback
from importlib.metadata import version

from . import adapter
from .archive import SlotArchive

PROBE_REV = 1


def needs_run(data_dir):
    try:
        with open(os.path.join(data_dir, "solve_probe.json"), encoding="utf-8") as fh:
            d = json.load(fh)
        return d.get("emhass_version") != version("emhass") or d.get("probe_rev") != PROBE_REV or d.get("ok") is not True
    except (OSError, ValueError):
        return True


async def _run(data_dir, tz):
    rep = {"emhass_version": version("emhass"), "probe_rev": PROBE_REV, "ok": False}
    log = logging.getLogger("lab.solve_probe")
    try:
        archive = SlotArchive(data_dir)
        days = [d for d in archive.days() if archive.coverage(d, tz)["complete"]]
        rep["complete_days"] = days[-3:]
        day = days[-1]
        rows = []
        for d in archive.days():
            rows += archive.read_day(d)
        rows.sort(key=lambda r: r["slot_start_epoch"])
        # începem la prima felie a zilei complete de la ora 12:00 locală (+ 48 de sloturi de la miezul nopții)
        first = archive.read_day(day)[0]["slot_start_epoch"] + 48 * 900
        slots = adapter.slot_inputs(rows, first, adapter.MAX_HORIZON)
        rep.update(day=day, start_epoch=first, horizon=len(slots))
        soc0 = slots[0]["actual_soc_start_pct"] / 100
        runtime, _ = adapter.build_runtime(slots, soc0)
        rep["runtime_head"] = {k: (v[:4] if isinstance(v, list) else v) for k, v in runtime.items()}
        t0 = time.time()
        emhass_conf, config, params = await adapter.make_context(data_dir, log)
        rep["context_s"] = round(time.time() - t0, 2)
        t0 = time.time()
        df = await adapter.solve(emhass_conf, params, runtime, log)
        rep["solve_s"] = round(time.time() - t0, 2)
        rep["columns"] = list(map(str, df.columns))
        rep["index_head"] = [str(x) for x in df.index[:3]]
        rep["rows"] = len(df)
        rep["head"] = json.loads(df.head(3).to_json(orient="records"))
        rep["real_first_slot"] = {k: slots[0].get(k) for k in (
            "load_kwh", "pv_actual_kwh", "actual_soc_start_pct", "actual_soc_end_pct", "plan_batt_w", "plan_grid_w")}
        rep["ok"] = True
    except Exception as exc:  # noqa: BLE001 — probă: raportăm, nu oprim
        rep["error"] = repr(exc)
        rep["traceback"] = traceback.format_exc()[-3500:]
    path = os.path.join(data_dir, "solve_probe.json")
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, ensure_ascii=False, default=str)
    os.replace(path + ".tmp", path)
    return rep


def run(data_dir, tz, timeout=300):
    async def guarded():
        return await asyncio.wait_for(_run(data_dir, tz), timeout)
    try:
        return asyncio.run(guarded())
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"timeout {timeout}s"}
