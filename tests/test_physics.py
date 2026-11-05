import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "emhass_perfect_world"))
from lab import adapter, physics  # noqa: E402
from lab.schema import num  # noqa: E402

M, L = physics.MEASURED, physics.LEGACY_STOCK
WINTER_NIGHT = 1793750400      # 2026-11-04 00:00 local: în fereastra de descărcare de iarnă (17:00-09:00)
WINTER_NOON = WINTER_NIGHT + 12 * 3600


def r(ep, load, pv, soc_start=60.0, seg=1, **kw):
    d = {"slot_start_epoch": ep, "capture_state": "VALID", "load_kwh": load, "pv_actual_kwh": pv,
         "stock_soc_start_pct": soc_start, "actual_soc_start_pct": soc_start, "replay_segment": seg}
    d.update(kw)
    return d


class T(unittest.TestCase):
    def test_num_accepts_ha_string_floats(self):
        self.assertEqual(num("1e-05"), 1e-05)
        self.assertEqual(num(3), 3.0)
        for bad in ("unknown", None, True, float("nan"), "nan", float("inf")):
            self.assertIsNone(num(bad))

    def test_slot_inputs_coerce_string_numbers(self):
        rows = [{"slot_start_epoch": 900 * i, "load_kwh": 0.1, "pv_actual_kwh": "2e-05",
                 "ppc_import_price_ron_kwh": 1.5, "ppc_export_price_ron_kwh": "0.3"} for i in range(4)]
        out = adapter.slot_inputs(rows, 0, 4)
        self.assertEqual(len(out), 4)                                      # nu se mai trunchiază orizontul la șiruri numerice
        self.assertEqual((out[0]["pv_actual_kwh"], out[0]["ppc_export_price_ron_kwh"]), (2e-05, 0.3))

    def test_realize_flows_energy_balance(self):
        imp, exp, soc_end, c, d = physics.realize_flows(M, 0.5, 0.0, 0.3, 0.0, 0.2)
        self.assertEqual(c, 0.0)
        self.assertAlmostEqual(imp, 0.3 - 0.2 * M.eta_dc_ac, places=9)
        self.assertEqual(exp, 0.0)
        self.assertAlmostEqual(soc_end, 0.5 - 0.2 / M.eta_dis / 10.24, places=9)
        # încărcare din rețea: AC -> DC cu eta_ac_dc
        imp, exp, soc_end, c, d = physics.realize_flows(M, 0.5, 0.0, 0.0, 0.3, 0.0)
        self.assertAlmostEqual(imp, 0.3 / M.eta_ac_dc, places=9)
        # baterie plină: nu se încarcă peste 100%
        imp, exp, soc_end, c, d = physics.realize_flows(M, 0.999, 1.0, 0.0, 1.0, 0.0)
        self.assertLessEqual(soc_end, 1.0 + 1e-12)
        self.assertLess(c, 1.0)

    def test_stock_discharges_only_in_window_and_uses_eta(self):
        night = physics.stock_replay([r(WINTER_NIGHT, 0.2, 0.0)], M)[WINTER_NIGHT]
        self.assertEqual(night["import"], 0.0)
        self.assertAlmostEqual(night["soc_end"], 60 - 0.2 / M.eta_dc_ac / M.eta_dis / 10.24 * 100, places=6)
        noon = physics.stock_replay([r(WINTER_NOON, 0.2, 0.0)], M)[WINTER_NOON]
        self.assertAlmostEqual(noon["import"], 0.2, places=9)              # în afara ferestrei nu se descarcă
        self.assertEqual(noon["soc_end"], 60.0)

    def test_stock_surplus_charges_then_exports(self):
        o = physics.stock_replay([r(WINTER_NOON, 0.1, 1.0, soc_start=99.0)], M)[WINTER_NOON]
        self.assertEqual(o["import"], 0.0)
        self.assertAlmostEqual(o["soc_end"], 100.0, places=6)
        # surplus DC = 1.0 - 0.1/0.99; încărcare = (cap - e0)/eta_ch; restul exportat prin invertor
        surplus = 1.0 - 0.1 / M.eta_dc_ac
        bc = 10.24 * 0.01 / M.eta_ch
        self.assertAlmostEqual(o["export"], (surplus - bc) * M.eta_dc_ac, places=6)

    def test_stock_state_carries_and_reseeds_after_gap_or_segment(self):
        rows = [r(WINTER_NIGHT, 0.2, 0.0, 60.0), r(WINTER_NIGHT + 900, 0.2, 0.0, 99.0),               # continuu: ignoră 99
                r(WINTER_NIGHT + 2700, 0.2, 0.0, 40.0), r(WINTER_NIGHT + 3600, 0.2, 0.0, 70.0, seg=2)]  # gol, apoi segment nou
        o = physics.stock_replay(rows, M)
        self.assertEqual(o[WINTER_NIGHT + 900]["soc_start"], o[WINTER_NIGHT]["soc_end"])
        self.assertEqual(o[WINTER_NIGHT + 2700]["soc_start"], 40.0)
        self.assertEqual(o[WINTER_NIGHT + 3600]["soc_start"], 70.0)

    def test_reset_pending_rows_leave_a_gap(self):
        rows = [r(WINTER_NIGHT, 0.2, 0.0, 60.0), r(WINTER_NIGHT + 900, 0.2, 0.0, 99.0, capture_state="RESET_PENDING"),
                r(WINTER_NIGHT + 1800, 0.2, 0.0, 50.0)]
        o = physics.stock_replay(rows, M)
        self.assertNotIn(WINTER_NIGHT + 900, o)
        self.assertEqual(o[WINTER_NIGHT + 1800]["soc_start"], 50.0)

    def test_string_pv_does_not_break_replay(self):
        rows = [r(WINTER_NIGHT, 0.2, "1e-05"), r(WINTER_NIGHT + 900, 0.2, 0.0)]
        self.assertEqual(len(physics.stock_replay(rows, M)), 2)

    def test_legacy_etas_reproduce_old_auditor_logic(self):
        # eficiențele vechi 0.99/0.99/0.97: 0.2 kWh consum noaptea -> 0.2/0.97/0.99 kWh din celule
        o = physics.stock_replay([r(WINTER_NIGHT, 0.2, 0.0)], L)[WINTER_NIGHT]
        self.assertAlmostEqual(o["soc_end"], 60 - 0.2 / 0.97 / 0.99 / 10.24 * 100, places=6)


if __name__ == "__main__":
    unittest.main()
