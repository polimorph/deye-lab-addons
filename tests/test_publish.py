import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "emhass_perfect_world"))
from lab import physics, publish  # noqa: E402

PI, PE = 1.5, 0.3
ETA = physics.MEASURED.eta_dis * physics.MEASURED.eta_dc_ac


def arch(epoch, **kw):
    a = {"slot_start_epoch": epoch, "ppc_import_price_ron_kwh": PI, "ppc_export_price_ron_kwh": PE,
         "actual_ppc_net_cost_ron": 0.1, "actual_grid_import_kwh": 0.1, "actual_grid_export_kwh": 0.0,
         "actual_soc_start_pct": 60, "actual_soc_end_pct": 60}
    a.update(kw)
    return a


def row(epoch, imp, exp, cost, soc_end=0.5, soc_start=0.6, **kw):
    r = {"slot_start_epoch": epoch, "skipped": False, "scored": True, "import_kwh": imp, "export_kwh": exp,
         "net_cost_ron": cost, "soc_end": soc_end, "soc_start": soc_start}
    r.update(kw)
    return r


def st(imp=0.0, exp=0.1, s0=55.0, s1=50.0):
    return {"import": imp, "export": exp, "soc_start": s0, "soc_end": s1}


class T(unittest.TestCase):
    def test_loss_price_rule_and_inventory(self):
        rows = [row(0, 0.2, 0.0, 0.3), row(900, 0.0, 0.1, -0.03, soc_end=0.4)]
        by = {0: arch(0), 900: arch(900)}
        stock = {0: st(imp=0.0, exp=0.1, s0=55, s1=52), 900: st(imp=0.0, exp=0.1, s0=52, s1=50)}
        s = publish.summarize(rows, by, 0.03, "0.18.8", stock=stock)
        pw = s["worlds"]["PERFECT"]
        self.assertAlmostEqual(pw["cost_ron"], 0.27)
        self.assertAlmostEqual(pw["fixed_loss_ron"], 0.03 * PI + 0.03 * PE, places=3)
        inv = (60 - 40) / 100 * 10.24 * ETA * PE          # pornesc din același SOC real (60) -> fără corecție de start
        self.assertAlmostEqual(pw["inventory_ron"], inv, places=3)
        self.assertAlmostEqual(pw["value_ron"], -(0.27 + 0.03 * PI + 0.03 * PE) - inv, places=3)
        # STOCK: costul vine din replay (import*preț - export*preț), nu din câmpurile stock_* ale arhivei
        sp = s["worlds"]["STOCK_PPC"]
        self.assertAlmostEqual(sp["cost_ron"], -2 * 0.1 * PE, places=3)
        self.assertAlmostEqual(sp["fixed_loss_ron"], 2 * 0.03 * PE, places=3)
        inv_stock = ((60 - 50) - (60 - 55)) / 100 * 10.24 * ETA * PE   # diferența SOC de la start nu e câștig
        self.assertAlmostEqual(sp["inventory_ron"], inv_stock, places=3)
        self.assertEqual(s["worlds"]["REAL"]["fixed_loss_ron"], 0)
        self.assertEqual(s["worlds"]["REAL"]["inventory_ron"], 0)
        hy = s["worlds"]["STOCK_HYDRO_V2"]
        self.assertAlmostEqual(hy["cost_ron"], -2 * 0.1 * 0.515, places=3)
        self.assertAlmostEqual(hy["fixed_loss_ron"], 2 * 0.03 * 0.515, places=3)
        self.assertEqual((s["physics"], s["eta_batt_ch"], s["eta_dc_ac"]), ("measured-2026-11-05", 0.975, 0.99))

    def test_same_period_and_unscored_excluded(self):
        rows = [row(0, 0.0, 0.0, 0.0), row(900, 0.0, 0.0, 0.0, scored=False), row(1800, 0, 0, 0, skipped=True)]
        by = {0: arch(0), 900: arch(900), 1800: arch(1800)}
        s = publish.summarize(rows, by, 0.03, "x", stock={0: st(), 900: st(), 1800: st()})
        self.assertEqual(s["slots_counted"], 1)
        self.assertAlmostEqual(s["worlds"]["REAL"]["cost_ron"], 0.1)

    def test_slot_without_stock_replay_is_skipped_for_all_worlds(self):
        rows = [row(0, 0.0, 0.0, 0.0), row(900, 0.0, 0.0, 0.0)]
        s = publish.summarize(rows, {0: arch(0), 900: arch(900)}, 0.03, "x", stock={0: st()})
        self.assertEqual(s["slots_counted"], 1)
        self.assertAlmostEqual(s["worlds"]["REAL"]["cost_ron"], 0.1)

    def test_stock_computed_from_archive_when_not_given(self):
        a = arch(0, capture_state="VALID", load_kwh=0.2, pv_actual_kwh=0.0, stock_soc_start_pct=60.0, replay_segment=1)
        s = publish.summarize([row(0, 0.0, 0.0, 0.0)], {0: a}, 0.03, "x")   # 0:00 iarna => în fereastra de descărcare
        self.assertEqual(s["slots_counted"], 1)
        self.assertEqual(s["worlds"]["STOCK_PPC"]["import_kwh"], 0.0)       # bateria acoperă consumul
        self.assertLess(s["worlds"]["STOCK_PPC"]["soc_end_pct"], 60)

    def test_empty_and_flatten(self):
        self.assertIsNone(publish.summarize([], {}, 0.03, "x", stock={}))
        f = publish.flatten(publish.summarize([row(0, 0, 0, 0)], {0: arch(0)}, 0.03, "x", stock={0: st()}))
        self.assertIn("perfect_value_ron", f)

    def test_counter_anomaly_excluded_from_all_worlds(self):
        # slot 900: export 11.2 kWh cu descărcare 1.2 kWh și PV 0 → salt de contor, exclus din toate lumile
        rows = [row(0, 0.2, 0.0, 0.3), row(900, 0.0, 0.1, -0.03), row(1800, 0.2, 0.0, 0.3)]
        bad = arch(900, actual_grid_export_kwh=11.2, actual_battery_discharge_kwh=1.2, pv_actual_kwh=0.0,
                   load_kwh=0.1, actual_ppc_net_cost_ron=-26.0)
        by = {0: arch(0), 900: bad, 1800: arch(1800)}
        s = publish.summarize(rows, by, 0.03, "0.18.8", stock={0: st(0.0, 0.2), 900: st(), 1800: st(0.0, 0.2)})
        self.assertEqual(s["slots_counted"], 2)
        self.assertEqual(s["excluded_counter_anomaly_epochs"], [900])
        self.assertAlmostEqual(s["worlds"]["PERFECT"]["cost_ron"], 0.6)
        self.assertAlmostEqual(s["worlds"]["REAL"]["cost_ron"], 0.2)
        self.assertAlmostEqual(s["worlds"]["STOCK_PPC"]["cost_ron"], -2 * 0.2 * PE)

    def test_real_battery_export_not_flagged(self):
        ok = arch(0, actual_grid_export_kwh=1.534, actual_battery_discharge_kwh=2.017, pv_actual_kwh=0.06,
                  load_kwh=0.059, actual_grid_import_kwh=0.003)
        self.assertFalse(publish.counter_anomaly(ok))
        self.assertTrue(publish.counter_anomaly(arch(0, actual_grid_export_kwh=1.906, actual_battery_discharge_kwh=1.226,
                                                     pv_actual_kwh=0.0, load_kwh=0.319, actual_grid_import_kwh=0.022)))
        self.assertFalse(publish.counter_anomaly(arch(0, actual_grid_export_kwh=0.4)))


if __name__ == "__main__":
    unittest.main()
