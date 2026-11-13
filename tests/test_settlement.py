import os
import sys
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "emhass_perfect_world"))
from lab import publish, settlement  # noqa: E402
from lab.settlement import Rules, consumption_price, settle_months, settle_slots, settle_totals  # noqa: E402

K = 1.21


def ts(y, m, d, h=12):
    return int(datetime(y, m, d, h, tzinfo=ZoneInfo("Europe/Bucharest")).timestamp())


class T(unittest.TestCase):
    def test_matches_contract_panel_numbers(self):
        """Cifrele din panoul „Deye Economics · Contract” (04.11 15:00 →, 867 sloturi, 13.11.2026)."""
        actual = settle_totals(138.44, 145.03, 1.0624, 2.1969)
        stock = settle_totals(59.86, 39.86, 1.0614, 1.4306)
        self.assertEqual((actual["regime"], stock["regime"]), ("SURPLUS", "DEFICIT"))
        self.assertAlmostEqual(actual["cost_ron"], 70.34, delta=0.05)    # intrările din panou sunt rotunjite la 2 zecimale
        self.assertAlmostEqual(stock["cost_ron"], 62.37, delta=0.05)
        self.assertAlmostEqual(stock["cost_ron"] - actual["cost_ron"], -7.97, delta=0.08)

    def test_ppc_example_cases(self):
        """Exemplele PPC (răspunsul din 09–11.11): caz A deficit -> 562,65 lei; caz B surplus -> factură de consum 116,16."""
        r = Rules(regulated=0.48)
        a = settle_totals(500, 200, 0.75, 0.0, r)
        self.assertAlmostEqual(a["cost_ron"], 562.65, places=2)
        b = settle_totals(200, 500, 0.75, 0.45, r)
        # factura de consum = reglementate cu TVA; valoarea surplusului (300 · 0,45) nu e în factură, ci report în avans
        self.assertAlmostEqual(b["regulated_ron"] + b["energy_ron"], 116.16, places=2)
        self.assertAlmostEqual(b["surplus_value_ron"], 135.0, places=2)

    def test_consumption_price_cap_and_tg(self):
        r = Rules()
        self.assertAlmostEqual(consumption_price(0.5, r), 0.5 + 0.069)
        self.assertAlmostEqual(consumption_price(2.0, r), 0.999 + 0.069)           # plafon pe interval
        self.assertAlmostEqual(consumption_price(0.0, r), 0.069)
        self.assertAlmostEqual(consumption_price(0.5, Rules(tg_on_consumption=False)), 0.5 + 0.00363 + 0.069)

    def test_marginal_values_by_regime(self):
        d = settle_totals(100, 60, 1.0, 2.0)
        self.assertEqual(d["regime"], "DEFICIT")
        self.assertAlmostEqual(d["v_export"], 1.0 * K)
        self.assertAlmostEqual(d["v_import"], (0.5064632 + 1.0) * K)
        s = settle_totals(60, 100, 1.0, 2.0)
        self.assertEqual(s["regime"], "SURPLUS")
        self.assertAlmostEqual(s["v_export"], 2.0)
        self.assertAlmostEqual(s["v_import"], 0.5064632 * K + 2.0)

    def test_cycling_costs_regulated_tariff_when_energy_cancels(self):
        """Import apoi export al aceleiași energii, la același preț: energia activă se anulează (deficitul rămâne 4 kWh),
        costul suplimentar sunt doar tarifele reglementate cu TVA pe cei 2 kWh importați brut."""
        base = settle_slots([(0.0, 0.0, 1.0)] * 4 + [(1.0, 0.0, 1.0)] * 4)                  # doar consum de 4 kWh
        cyc = settle_slots([(0.0, 0.0, 1.0)] * 4 + [(1.0, 0.0, 1.0)] * 4 + [(2.0, 0.0, 1.0), (0.0, 2.0, 1.0)])
        self.assertAlmostEqual(base["deficit_kwh"], cyc["deficit_kwh"])
        self.assertAlmostEqual(cyc["cost_ron"] - base["cost_ron"], 2.0 * 0.5064632 * K, places=9)

    def test_loss_modes(self):
        slots = [(0.0, 1.0, 0.5), (1.0, 0.0, 0.5)]
        a = settle_slots(slots, loss_kwh=0.1)                                  # import suplimentar în fiecare slot
        self.assertAlmostEqual(a["import_kwh"], 1.2)
        self.assertAlmostEqual(a["export_kwh"], 1.0)
        n = settle_slots(slots, loss_kwh=0.1, loss_mode="net")                 # scade din fluxul net al slotului
        self.assertAlmostEqual(n["export_kwh"], 0.9)
        self.assertAlmostEqual(n["import_kwh"], 1.1)

    def test_months_split_and_sum(self):
        slots = [(ts(2026, 11, 30, 23), 1.0, 0.0, 1.0), (ts(2026, 12, 1, 1), 0.0, 2.0, 1.0)]
        m = settle_months(slots)
        self.assertEqual(sorted(m["months"]), ["2026-11", "2026-12"])
        self.assertEqual(m["month"], "2026-12")
        self.assertEqual(m["months"]["2026-11"]["regime"], "DEFICIT")
        self.assertEqual(m["months"]["2026-12"]["regime"], "SURPLUS")
        self.assertAlmostEqual(m["cost_ron"], m["months"]["2026-11"]["cost_ron"] + m["months"]["2026-12"]["cost_ron"])
        self.assertIsNone(settle_months([]))

    def test_empty_and_zero_flows(self):
        z = settle_slots([])
        self.assertEqual((z["cost_ron"], z["import_kwh"], z["pi_c"]), (0.0, 0.0, 0.0))

    def test_default_rules_values(self):
        r = settlement.DEFAULT_RULES
        self.assertEqual((r.vat, r.cap, r.regulated, r.fixed), (0.21, 0.999, 0.5064632, 0.069))


class SummarizeIntegration(unittest.TestCase):
    def test_summarize_adds_settlement_and_includes_anomaly_slots(self):
        e0 = ts(2026, 11, 5, 12)

        def arch(e, **kw):
            a = {"slot_start_epoch": e, "ppc_import_price_ron_kwh": 1.5, "ppc_export_price_ron_kwh": 0.9,
                 "actual_ppc_net_cost_ron": 0.1, "actual_grid_import_kwh": 0.2, "actual_grid_export_kwh": 0.0,
                 "actual_soc_start_pct": 60, "actual_soc_end_pct": 60}
            a.update(kw)
            return a

        def row(e, imp, exp, cost):
            return {"slot_start_epoch": e, "skipped": False, "scored": True, "import_kwh": imp, "export_kwh": exp,
                    "net_cost_ron": cost, "soc_end": 0.6, "soc_start": 0.6}

        st = lambda imp, exp: {"import": imp, "export": exp, "soc_start": 60.0, "soc_end": 60.0}
        # al doilea slot: salt de contor (export 6 kWh, imposibil fizic) -> exclus din cash, inclus în decontare
        rows = [row(e0, 0.2, 0.0, 0.3), row(e0 + 900, 0.0, 0.1, -0.09)]
        by = {e0: arch(e0), e0 + 900: arch(e0 + 900, actual_grid_export_kwh=6.0, actual_grid_import_kwh=0.0)}
        stock = {e0: st(0.1, 0.0), e0 + 900: st(0.0, 0.0)}
        s = publish.summarize(rows, by, 0.03, "x", stock=stock)
        self.assertEqual(s["excluded_counter_anomaly_slots"], 1)
        self.assertEqual(s["slots_counted"], 1)                                   # cash: doar slotul normal
        real = s["worlds"]["REAL"]
        self.assertAlmostEqual(real["settle_export_kwh"], 6.0)                    # decontare: include slotul anormal
        self.assertEqual(real["settle_regime"], "SURPLUS")
        # fără slotul anormal dispare încasarea surplusului (5,8 kWh) și apare deficit de 0,2 kWh la π̄_c cu TVA
        pi_c = 0.9 + 0.069
        self.assertAlmostEqual(real["settle_exanom_cost_ron"] - real["settle_cost_ron"],
                               5.8 * real["settle_pi_inj"] + 0.2 * pi_c * K, places=2)
        self.assertNotIn("settle_value_ron", s["worlds"]["STOCK_HYDRO_V2"])       # Hydro nu e sub regulile PPC
        self.assertIsNotNone(s["settle_delta_vs_real_ron"])
        f = publish.flatten(s)
        self.assertIn("perfect_settle_value_ron", f)
        self.assertIn("settle_rule_cap", f)


class ExperimentsDelta(unittest.TestCase):
    @staticmethod
    def row(e, imp, exp, cost, soc_end=0.5):
        return {"slot_start_epoch": e, "skipped": False, "scored": True, "import_kwh": imp, "export_kwh": exp,
                "net_cost_ron": cost, "soc_end": soc_end, "soc_start": 0.5}

    def test_delta_all_slots_settlement_import_saved(self):
        e = ts(2026, 11, 5, 12)
        by = {e: {"ppc_import_price_ron_kwh": 1.5, "ppc_export_price_ron_kwh": 0.9}}
        d = publish._delta_all_slots_settlement([self.row(e, 1.0, 0.0, 1.5)], [self.row(e, 0.0, 0.0, 0.0)], by, 0.0)
        # varianta evită 1 kWh import: economisește tarifele brute + energia deficitară (π_c = 0,9 + 0,069)
        self.assertAlmostEqual(d["settle_delta_all_slots_ron"], (0.5064632 + 0.969) * K, delta=0.001)
        self.assertEqual(d["settle_regime_ref"], "DEFICIT")

    def test_delta_inventory_valued_at_marginal_values(self):
        e = ts(2026, 11, 5, 12)
        by = {e: {"ppc_import_price_ron_kwh": 1.5, "ppc_export_price_ron_kwh": 0.9}}
        base, var = self.row(e, 1.0, 0.0, 1.5, soc_end=0.6), self.row(e, 1.0, 0.0, 1.5, soc_end=0.5)
        d = publish._delta_all_slots_settlement([base], [var], by, 0.0)
        kwh = 0.1 * 10.24 * publish.PHYS.eta_discharge_to_ac            # varianta rămâne cu 10 puncte mai puțin
        self.assertAlmostEqual(d["settle_delta_all_slots_ron"], -kwh * 0.969 * K, delta=0.001)
        self.assertAlmostEqual(d["settle_delta_all_slots_inv_at_import_ron"], -kwh * (0.5064632 + 0.969) * K, delta=0.001)

    def test_price_filled_slot_uses_chain_implied_price(self):
        e = ts(2026, 11, 5, 12)
        # arhiva fără preț (slot cu preț completat): prețul de export implicit = -cost/export = 0,8
        d = publish._delta_all_slots_settlement([self.row(e, 0.0, 1.0, -0.8)], [self.row(e, 0.0, 0.0, 0.0)], {e: {}}, 0.0)
        self.assertAlmostEqual(d["settle_delta_all_slots_ron"], -1.0 * 0.8, delta=0.001)   # varianta nu mai încasează surplusul
        self.assertIsNone(publish._pe_for({}, [self.row(e, 0.0, 0.0, 0.0)]))
        self.assertEqual(publish._pe_for({"ppc_import_price_ron_kwh": 1.5, "ppc_export_price_ron_kwh": 0.0}, []), 0.0)

    def test_summarize_carries_settlement_fields_for_perfect_world(self):
        e = ts(2026, 11, 5, 12)
        by = {e: {"slot_start_epoch": e, "ppc_import_price_ron_kwh": 1.5, "ppc_export_price_ron_kwh": 0.9,
                  "actual_grid_import_kwh": 0.2, "actual_grid_export_kwh": 0.0, "actual_soc_start_pct": 60,
                  "actual_soc_end_pct": 60}}
        stock = {e: {"import": 0.1, "export": 0.0, "soc_start": 60.0, "soc_end": 60.0}}
        main = [self.row(e, 0.2, 0.0, 0.3, soc_end=0.6)]
        s = publish.summarize(main, by, 0.03, "x", stock=stock)
        self.assertIn("settle_value_ron", s["worlds"]["PERFECT"])
        self.assertIn("settle_value_inv_at_import_ron", s["worlds"]["PERFECT"])


if __name__ == "__main__":
    unittest.main()
