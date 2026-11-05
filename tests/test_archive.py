import json, os, sys, tempfile, threading, time, unittest, urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "emhass_perfect_world"))
from lab import runner  # noqa: E402
from lab.archive import SlotArchive  # noqa: E402
from lab import adapter  # noqa: E402
from lab.ha import HA  # noqa: E402
from lab.schema import ScenarioResult, SchemaError, expected_slots, normalize_slot  # noqa: E402

TZ = "Europe/Bucharest"
BASE = 1793873700  # 2026-11-05 10:15 UTC


def attrs(i, **kw):
    a = {"schema_version": "0.1.0", "slot_start_epoch": BASE + 900 * i, "load_kwh": 0.1 + i,
         "pv_actual_kwh": 0.6, "icon": "mdi:x", "friendly_name": "x", "plan_batt_w": -1763}
    a.update(kw)
    return a


class Fake(BaseHTTPRequestHandler):
    hist = []
    calls = []
    cur = None

    def do_GET(self):
        path = urllib.parse.unquote(self.path)
        assert self.headers["Authorization"] == "Bearer tok"
        if path.startswith("/config"):
            body = {"time_zone": TZ}
        elif path.startswith("/states/"):
            body = Fake.cur
        elif path.startswith("/history/period/"):
            assert "significant_changes_only=0" in path
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)  # din calea brută, o singură decodare
            assert "significant_changes_only" in q and q["significant_changes_only"] == ["0"]
            assert "end_time" in q, "HA real taie la 24 h fără end_time"
            lo = datetime.fromisoformat(path.split("/history/period/")[1].split("?")[0]).timestamp()
            hi = datetime.fromisoformat(q["end_time"][0]).timestamp()
            Fake.calls.append((lo, hi))
            # ca HA real: doar rândurile din fereastra [start, end_time)
            body = [[s for s in Fake.hist if lo <= s["t"] < hi]]
        else:
            self.send_error(404); return
        data = json.dumps(body).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a): pass


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.ha = HA(base=f"http://127.0.0.1:{cls.srv.server_port}", token="tok")

    @classmethod
    def tearDownClass(cls): cls.srv.shutdown()

    def test_normalize(self):
        r = normalize_slot("VALID", attrs(0), TZ)
        self.assertEqual(r["day_local"], "2026-11-05")
        self.assertNotIn("icon", r)
        with self.assertRaises(SchemaError): normalize_slot("VALID", {"slot_start_epoch": 5}, TZ)
        with self.assertRaises(SchemaError): normalize_slot("VALID", {}, TZ)

    def test_expected_slots_dst(self):
        self.assertEqual(expected_slots("2026-11-05", TZ), 96)
        self.assertEqual(expected_slots("2026-10-25", TZ), 100)  # trecere la ora de iarnă
        self.assertEqual(expected_slots("2026-03-29", TZ), 92)   # trecere la ora de vară

    def test_backfill_poll_dedup_revision(self):
        with tempfile.TemporaryDirectory() as d:
            now = time.time()
            Fake.hist = [{"t": now - 600 - i, "state": "VALID", "attributes": attrs(i)} for i in range(3)] + \
                        [{"t": now - 500, "state": "VALID", "attributes": attrs(2)},   # duplicat
                         {"t": now - 400, "state": "unavailable", "attributes": {}}]
            ar = SlotArchive(d)
            self.assertEqual(runner.backfill(self.ha, ar, "sensor.x", 10, TZ), 3)
            Fake.cur = {"state": "VALID", "attributes": attrs(3)}
            st = self.ha.state("sensor.x")
            self.assertTrue(runner.ingest(ar, st["state"], st["attributes"], TZ))
            self.assertFalse(runner.ingest(ar, st["state"], st["attributes"], TZ))
            # slot recalculat: revizie nouă, cititorul vede ultima
            self.assertTrue(runner.ingest(ar, "VALID", attrs(3, load_kwh=9.9), TZ))
            rows = SlotArchive(d).read_day("2026-11-05")  # reîncărcare de pe disc
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[3]["load_kwh"], 9.9); self.assertEqual(rows[3]["rev"], 2)
            self.assertFalse(SlotArchive(d).add(rows[3]))  # reindexat corect

    def test_backfill_covers_whole_window(self):
        """Regresie 0.2.1: fără end_time HA întoarce doar 24 h; sloturi vechi de 9 zile trebuie găsite."""
        with tempfile.TemporaryDirectory() as d:
            now = time.time()
            Fake.hist = [{"t": now - 86400 * k - 3600, "state": "VALID",
                          "attributes": attrs(0, slot_start_epoch=int(now - 86400 * k - 3600) // 900 * 900)}
                         for k in range(9)]
            Fake.calls.clear()
            self.assertEqual(runner.backfill(self.ha, SlotArchive(d), "sensor.x", 10, TZ), 9)
            self.assertEqual(len(Fake.calls), 10)
            self.assertTrue(all(hi - lo <= 86400 for lo, hi in Fake.calls))

    def test_corrupt_line_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            ar = SlotArchive(d); ar.add(normalize_slot("VALID", attrs(0), TZ))
            with open(os.path.join(d, "slots", "2026-11-05.jsonl"), "a") as fh: fh.write('{"slot_st')
            self.assertEqual(len(SlotArchive(d).read_day("2026-11-05")), 1)

    def _row(self, i, exp=1.5, imp=1.9):
        return attrs(i, ppc_import_price_ron_kwh=imp, ppc_export_price_ron_kwh=exp, load_kwh=0.1, pv_actual_kwh=0.25)

    def test_slot_inputs_stops_at_gap(self):
        rows = [self._row(0), self._row(1), self._row(3)]  # lipsește slotul 2
        self.assertEqual(len(adapter.slot_inputs(rows, BASE, 10)), 2)
        rows[1]["ppc_export_price_ron_kwh"] = "unknown"      # preț invalid oprește secvența
        self.assertEqual(len(adapter.slot_inputs(rows, BASE, 10)), 1)

    def test_build_runtime_matches_production_shape(self):
        slots = [self._row(i) for i in range(4)]
        rt, _ = adapter.build_runtime(slots, 0.44)
        self.assertEqual(rt["pv_power_forecast"], [1000.0] * 4)      # 0.25 kWh/15min = 1 kW
        self.assertEqual(rt["load_power_forecast"], [400.0] * 4)
        self.assertEqual(rt["prediction_horizon"], 4)
        self.assertEqual((rt["soc_final"], rt["lp_solver_mip_rel_gap"], rt["alpha"], rt["beta"]), (0.3, 0, 1, 0))
        self.assertEqual(rt["prod_price_forecast"], [1.5] * 4)       # fără taxă de risc în Perfect World

    def test_curtailment_hysteresis_and_haircut(self):
        exps = [0.02, 0.03, 0.04, 0.2]   # intră la <=0.025, rămâne între praguri, iese la >=0.035
        rt, first = adapter.build_runtime([self._row(i, exp=e) for i, e in enumerate(exps)], 0.5)
        self.assertEqual(rt["prod_price_forecast"], [-0.001, -0.001, 0.04, 0.2])
        self.assertTrue(first)
        rt, _ = adapter.build_runtime([self._row(0, exp=1.5)], 0.5, perfect=False, haircut=0.1)
        self.assertEqual(rt["prod_price_forecast"], [1.4])           # replay incumbent păstrează taxa de risc

    def test_result_schema(self):
        r = ScenarioResult("2026-11-05", "EMHASS_PPC_PERFECT_PV_LOAD", "0.18.8", 96, 1.5, 3.0, 2.0, 40, 45, "t")
        self.assertEqual(r.era, "emhass-0.18.8"); self.assertFalse(r.certified)
        with self.assertRaises(SchemaError):
            ScenarioResult("2026-11-05", "NOPE", "0.18.8", 96, 0, 0, 0, 0, 0, "t")


if __name__ == "__main__":
    unittest.main()
