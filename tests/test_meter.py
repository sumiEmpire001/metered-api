import concurrent.futures
import csv
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from metered_api import Meter, QuotaExceeded
from metered_api.server import make_server


class MeterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "meter.db"
        self.now = 100
        self.meter = Meter(self.path, clock=lambda: self.now, window=60)
        self.key = self.meter.register("client", 10)

    def test_quota_boundary_and_rejection_do_not_append(self):
        self.meter.charge("client", "full", 10)
        with self.assertRaises(QuotaExceeded):
            self.meter.charge("client", "extra")
        self.assertEqual(self.meter.status("client")["used"], 10)
        self.assertEqual(len(self.meter.events("client")), 1)

    def test_duplicate_and_conflicting_event_ids(self):
        self.assertFalse(self.meter.charge("client", "same", 3)["duplicate"])
        self.assertTrue(self.meter.charge("client", "same", 3)["duplicate"])
        self.assertEqual(self.meter.status("client")["used"], 3)
        with self.assertRaises(ValueError):
            self.meter.charge("client", "same", 4)

    def test_window_boundary_resets_usage_but_not_event_deduplication(self):
        self.meter.charge("client", "old", 10)
        self.now = 120
        self.assertEqual(self.meter.status("client")["used"], 0)
        self.assertTrue(self.meter.charge("client", "old", 10)["duplicate"])
        self.assertEqual(self.meter.status("client")["used"], 0)

    def test_concurrent_new_events_do_not_exceed_quota(self):
        def charge(n):
            try:
                return self.meter.charge("client", str(n))
            except QuotaExceeded:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(charge, range(30)))
        self.assertEqual(sum(r is not None for r in results), 10)
        self.assertEqual(self.meter.status("client")["used"], 10)

    def test_concurrent_duplicate_events_have_one_effect(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.meter.charge("client", "same", 2), range(12)))
        self.assertEqual(sum(not r["duplicate"] for r in results), 1)
        self.assertEqual(self.meter.status("client")["used"], 2)

    def test_api_keys_are_hashed_and_revocation_is_enforced(self):
        self.assertEqual(self.meter.authenticate(self.key), "client")
        with self.meter.db() as db:
            self.assertNotEqual(db.execute("SELECT key_hash FROM clients").fetchone()[0], self.key)
        self.meter.configure("client", enabled=False)
        with self.assertRaises(PermissionError):
            self.meter.authenticate(self.key)
        with self.assertRaises(PermissionError):
            self.meter.charge("client", "revoked")

    def test_clients_are_isolated_and_can_reuse_event_names(self):
        self.meter.register("other", 10)
        self.meter.charge("client", "same", 2)
        self.meter.charge("other", "same", 3)
        self.assertEqual(self.meter.events("client")[0]["units"], 2)
        self.assertEqual(self.meter.events("other")[0]["units"], 3)

    def test_quota_reduction_does_not_rewrite_history(self):
        self.meter.charge("client", "used", 8)
        self.meter.configure("client", quota=5)
        self.assertEqual(self.meter.status("client")["remaining"], 0)
        with self.assertRaises(QuotaExceeded):
            self.meter.charge("client", "new")

    def test_csv_exports_escape_formula_like_labels(self):
        self.meter.charge("client", "formula", label=" =SUM(1,2)")
        rows = list(csv.reader(io.StringIO(self.meter.export_csv("client"))))
        self.assertTrue(rows[1][3].startswith("'"))
        self.assertEqual(rows[1][2], "1")

    def test_restart_and_export_pagination(self):
        self.meter.charge("client", "one")
        self.meter.charge("client", "two")
        again = Meter(self.path, clock=lambda: self.now, window=60)
        self.assertEqual(again.status("client")["used"], 2)
        self.assertEqual(again.events("client", limit=1, offset=1)[0]["event_id"], "two")
        with self.assertRaises(ValueError):
            Meter(self.path, window=120)

    def test_invalid_values_do_not_change_usage(self):
        for units in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                self.meter.charge("client", "bad", units)
        with self.assertRaises(ValueError):
            self.meter.charge("client", "../bad")
        self.assertEqual(self.meter.status("client")["used"], 0)

    def test_real_http_auth_charge_usage_and_quota(self):
        server = make_server(self.meter, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(path, body=None, key=None):
            req = urllib.request.Request(f"http://127.0.0.1:{server.server_port}{path}", data=None if body is None else json.dumps(body).encode(), headers={"Authorization": "Bearer " + (key or self.key)})
            try:
                with urllib.request.urlopen(req, timeout=3) as response:
                    return response.status, response.read()
            except urllib.error.HTTPError as error:
                return error.code, error.read()
        try:
            self.assertEqual(request("/usage", key="bad")[0], 401)
            self.assertEqual(request("/charge", {"event_id": "http", "units": 10})[0], 201)
            self.assertEqual(request("/charge", {"event_id": "http", "units": 10})[0], 200)
            self.assertEqual(request("/charge", {"event_id": "over"})[0], 429)
            self.assertEqual(json.loads(request("/usage")[1])["used"], 10)
            self.assertIn(b"event_id", request("/export.csv")[1])
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
