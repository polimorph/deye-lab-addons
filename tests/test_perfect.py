import asyncio, json, os, sys, tempfile, unittest
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "emhass_perfect_world"))
from lab import perfect  # noqa: E402
from lab.archive import SlotArchive  # noqa: E402
from lab.schema import normalize_slot  # noqa: E402

TZ = "Europe/Bucharest"


def epoch(day, hh=0, mm=0):
    d = datetime.fromisoformat(day)
    return int(datetime(d.year, d.month, d.day, hh, mm, tzinfo=ZoneInfo(TZ)).timestamp())


def make_archive(d, days):
    ar = SlotArchive(d)
    for day in days:
        e0 = epoch(day)
        for i in range(96):
            a = {"slot_start_epoch": e0 + 900 * i, "load_kwh": 0.1, "pv_actual_kwh": 0.2,
                 "ppc_import_price_ron_kwh": 1.9, "ppc_export_price_ron_kwh": 1.5, "actual_soc_start_pct": 40}
            ar.add(normalize_slot("VALID", a, TZ))
    return ar


class FakeSolver:
    def __init__(self): self.calls = []

    async def __call__(self, rt):
        self.calls.append(rt)
        soc = rt["soc_init"]
        return {"P_grid": -400.0, "P_batt": -1000.0, "P_PV_curtailment": 0.0, "SOC_opt": soc + 0.02, "optim_status": "Optimal"}


class T(unittest.TestCase):
    def test_horizon_end(self):
        self.assertEqual(perfect.horizon_end_epoch(epoch("2026-11-05", 11), TZ), epoch("2026-11-06"))        # <14:00: sfârșitul zilei
        self.assertEqual(perfect.horizon_end_epoch(epoch("2026-11-05", 14), TZ), epoch("2026-11-07"))        # >=14:00: și ziua următoare
        self.assertEqual(perfect.horizon_end_epoch(epoch("2026-10-24", 20), TZ), epoch("2026-10-26"))        # peste DST (25 oct, 25h)

    def test_waits_until_horizon_covered_then_runs_and_resumes(self):
        with tempfile.TemporaryDirectory() as d:
            ar = make_archive(d, ["2026-11-05"])          # doar ziua 5: sloturile de după 14:00 nu au orizont acoperit
            chain = perfect.Chain(d); chain.init(epoch("2026-11-05", 12), 0.40)
            solver = FakeSolver()
            n = asyncio.run(perfect.advance(chain, ar, TZ, solver, "0.18.8", max_slots=500, row_extra={"config_hash": "abc"}))
            self.assertEqual(n, 8)                          # 12:00..13:45 — apoi așteaptă (ora 14:00 cere ziua 6)
            self.assertEqual(solver.calls[0]["prediction_horizon"], 48)
            self.assertEqual(solver.calls[0]["soc_init"], 0.4)
            # lanțul propagă SOC-ul REALIZAT cu fizica măsurată (0.25 kWh pe bus * 0.975 / 10.24 kWh), nu SOC_opt al planificatorului
            self.assertAlmostEqual(solver.calls[1]["soc_init"], 0.4 + 0.25 * 0.975 / 10.24, places=4)
            r = chain.read_slots("2026-11-05")[0]
            self.assertEqual((r["era"], r["config_hash"], r["scored"]), ("emhass-0.18.8", "abc", True))
            # restart: starea se reia de pe disc; cu ziua 6 adăugată lanțul înaintează
            ar = make_archive(d, ["2026-11-05", "2026-11-06"])
            chain2 = perfect.Chain(d)
            self.assertEqual(chain2.state["next_epoch"], epoch("2026-11-05", 14))
            n2 = asyncio.run(perfect.advance(chain2, ar, TZ, solver, "0.18.8", max_slots=500))
            self.assertEqual(n2, 40 + 56)                   # 5 nov 14:00..23:45 + 6 nov 00:00..13:45; apoi iar așteaptă ziua 7

    def test_realize_uses_measured_physics_not_planner_belief(self):
        slot = {"ppc_import_price_ron_kwh": 2.0, "ppc_export_price_ron_kwh": 1.0, "pv_actual_kwh": 0.2, "load_kwh": 0.1}
        # plan: încarcă 1 kW pe bus (0.25 kWh/slot), PV 0.2, consum 0.1 -> bus DC: 0.2-0.25 = -0.05 kWh => AC din rețea 0.05/0.95
        row = perfect.realize({"P_grid": 123.0, "P_batt": -1000.0, "SOC_opt": 0.9}, slot, 0.40)
        self.assertAlmostEqual(row["import_kwh"], 0.1 + 0.05 / 0.95, places=5)
        self.assertEqual(row["export_kwh"], 0.0)
        self.assertAlmostEqual(row["soc_end"], 0.40 + 0.25 * 0.975 / 10.24, places=6)
        self.assertAlmostEqual(row["net_cost_ron"], row["import_kwh"] * 2.0, places=5)
        self.assertEqual(row["plan_soc_end"], 0.9)                       # planul rămâne vizibil pentru diagnostic
        self.assertEqual(row["physics"], "measured-2026-11-05")
        # export: PV 0.5, consum 0.1, bateria stă -> 0.5 * 0.99 - 0.1 la rețea
        slot2 = dict(slot, pv_actual_kwh=0.5)
        row = perfect.realize({"P_grid": -1600.0, "P_batt": 0.0, "SOC_opt": 0.5}, slot2, 0.5)
        self.assertAlmostEqual(row["export_kwh"], 0.5 * 0.99 - 0.1, places=5)
        self.assertAlmostEqual(row["net_cost_ron"], -row["export_kwh"] * 1.0, places=5)
        self.assertEqual(row["soc_end"], 0.5)

    def test_realize_limits_discharge_at_soc_floor(self):
        slot = {"ppc_import_price_ron_kwh": 2.0, "ppc_export_price_ron_kwh": 1.0, "pv_actual_kwh": 0.0, "load_kwh": 0.5}
        # planul cere 0.5 kWh descărcare dar SOC = 10.5% -> doar (0.105-0.10)*10.24*0.975 kWh pe bus
        row = perfect.realize({"P_grid": 0.0, "P_batt": 2000.0, "SOC_opt": 0.1}, slot, 0.105)
        self.assertTrue(row["battery_limited"])
        self.assertAlmostEqual(row["soc_end"], 0.10, places=6)
        self.assertGreater(row["import_kwh"], 0.4)

    def test_day_result(self):
        with tempfile.TemporaryDirectory() as d:
            ar = make_archive(d, ["2026-11-05"])
            chain = perfect.Chain(d); chain.init(epoch("2026-11-05"), 0.40)
            asyncio.run(perfect.advance(chain, ar, TZ, FakeSolver(), "0.18.8", max_slots=8))
            res = perfect.day_result(chain, "2026-11-05", TZ, "0.18.8")
            self.assertEqual(res["slots"], 8)
            self.assertFalse(res["complete"])

    def test_price_filled_slots_not_scored_and_day_aggregate(self):
        with tempfile.TemporaryDirectory() as d:
            ar = make_archive(d, ["2026-11-05", "2026-11-06"])
            # slot 13:00 fără preț: completat pentru solver, dar neevaluat
            e = epoch("2026-11-05", 13)
            row = ar.read_day("2026-11-05")[52]; assert row["slot_start_epoch"] == e
            bad = dict(row, ppc_import_price_ron_kwh="unknown", ppc_export_price_ron_kwh="unknown")
            ar.add(bad)
            chain = perfect.Chain(d); chain.init(epoch("2026-11-05"), 0.40)
            asyncio.run(perfect.advance(chain, ar, TZ, FakeSolver(), "0.18.8", max_slots=96))
            res = perfect.day_result(chain, "2026-11-05", TZ, "0.18.8")
            self.assertTrue(res["complete"])
            self.assertEqual((res["slots"], res["slots_price_filled"], res["slots_total"]), (95, 1, 96))
            self.assertEqual(res["scenario_id"], "EMHASS_PPC_PERFECT_PV_LOAD")


if __name__ == "__main__":
    unittest.main()
