import json, os, sys, tempfile, threading, unittest, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "emhass_perfect_world"))
from lab import runner  # noqa: E402
from lab.archive import SlotArchive  # noqa: E402
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
            body = [Fake.hist]
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
            Fake.hist = [{"state": "VALID", "attributes": attrs(i)} for i in range(3)] + \
                        [{"state": "VALID", "attributes": attrs(2)},              # duplicat
                         {"state": "unavailable", "attributes": {}}]
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

    def test_corrupt_line_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            ar = SlotArchive(d); ar.add(normalize_slot("VALID", attrs(0), TZ))
            with open(os.path.join(d, "slots", "2026-11-05.jsonl"), "a") as fh: fh.write('{"slot_st')
            self.assertEqual(len(SlotArchive(d).read_day("2026-11-05")), 1)

    def test_result_schema(self):
        r = ScenarioResult("2026-11-05", "EMHASS_PPC_PERFECT_PV_LOAD", "0.18.8", 96, 1.5, 3.0, 2.0, 40, 45, "t")
        self.assertEqual(r.era, "emhass-0.18.8"); self.assertFalse(r.certified)
        with self.assertRaises(SchemaError):
            ScenarioResult("2026-11-05", "NOPE", "0.18.8", 96, 0, 0, 0, 0, 0, "t")


if __name__ == "__main__":
    unittest.main()
