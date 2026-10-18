"""Exercise the HTTP API, storage, and local demo boundary without dependencies."""

from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import make_server


class AppointmentAPITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "appointments.sqlite3"
        self.start_server()

    def start_server(self, seed_demo=False):
        self.server = make_server(
            host="127.0.0.1", port=0, database=self.database, seed_demo=seed_demo
        )
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True
        )
        self.thread.start()
        self.port = self.server.server_address[1]

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def tearDown(self):
        self.stop_server()
        self.directory.cleanup()

    def request(self, method, path, payload=None, *, raw=None, headers=None):
        request_headers = dict(headers or {})
        body = raw
        if payload is not None:
            body = json.dumps(payload).encode()
            request_headers.setdefault("Content-Type", "application/json")
        connection = HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request(method, path, body, request_headers)
            response = connection.getresponse()
            data = response.read()
            content_type = response.getheader("Content-Type", "")
            decoded = json.loads(data) if "application/json" in content_type else data.decode()
            return response.status, decoded, dict(response.getheaders())
        finally:
            connection.close()

    def appointment(self, **overrides):
        return {
            "patient_alias": "Demo visitor 12",
            "clinician": "Demo clinician A",
            "starts_at": "2030-05-20T09:00",
            "reason": "Fictional follow-up",
            **overrides,
        }

    def create(self, **overrides):
        status, body, _ = self.request("POST", "/api/appointments", self.appointment(**overrides))
        self.assertEqual(status, 201, body)
        return body["appointment"]

    def test_create_and_list_preserve_data_and_sort_by_time(self):
        later = self.create()
        earlier = self.create(starts_at="2030-05-19T10:30", patient_alias="Demo visitor 13")
        status, body, _ = self.request("GET", "/api/appointments")
        self.assertEqual(status, 200)
        self.assertEqual([row["id"] for row in body["appointments"]], [earlier["id"], later["id"]])
        self.assertEqual(later["status"], "scheduled")
        self.assertEqual(later["reason"], "Fictional follow-up")

    def test_cancel_frees_slot_and_restoring_conflicting_visit_is_rejected(self):
        first = self.create()
        route = f"/api/appointments/{first['id']}"
        status, body, _ = self.request("PATCH", route, {"status": "cancelled"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["appointment"]["status"], "cancelled")
        self.create(patient_alias="Demo visitor 99")
        status, body, _ = self.request("PATCH", route, {"status": "scheduled"})
        self.assertEqual(status, 409, body)
        _, listing, _ = self.request("GET", "/api/appointments")
        saved = next(row for row in listing["appointments"] if row["id"] == first["id"])
        self.assertEqual(saved["status"], "cancelled")

    def test_completed_visit_persists_after_server_restart(self):
        item = self.create()
        status, body, _ = self.request("PATCH", f"/api/appointments/{item['id']}", {"status": "completed"})
        self.assertEqual(status, 200, body)
        self.stop_server()
        self.start_server()
        _, body, _ = self.request("GET", "/api/appointments")
        self.assertEqual(len(body["appointments"]), 1)
        self.assertEqual(body["appointments"][0]["status"], "completed")

    def test_concurrent_booking_has_one_winner(self):
        def book(index):
            return self.request("POST", "/api/appointments", self.appointment(patient_alias=f"Demo {index}"))[0]
        with ThreadPoolExecutor(max_workers=6) as pool:
            outcomes = list(pool.map(book, range(6)))
        self.assertEqual(outcomes.count(201), 1, outcomes)
        self.assertEqual(outcomes.count(409), 5, outcomes)

    def test_different_clinicians_can_share_a_time(self):
        self.create()
        self.create(clinician="Demo clinician B")

    def test_invalid_fields_are_rejected_without_inserting_rows(self):
        invalid = [
            {"patient_alias": " "}, {"patient_alias": "a" * 61},
            {"patient_alias": 123}, {"clinician": ""},
            {"clinician": "a" * 61}, {"reason": ""}, {"reason": "a" * 241},
            {"starts_at": "2030-02-29T09:00"}, {"starts_at": "2030-05-20"},
            {"starts_at": "2030-05-20T25:00"},
        ]
        for replacement in invalid:
            with self.subTest(replacement=replacement):
                status, body, _ = self.request("POST", "/api/appointments", self.appointment(**replacement))
                self.assertEqual(status, 400, body)
                self.assertIn("error", body)
        _, listing, _ = self.request("GET", "/api/appointments")
        self.assertEqual(listing["appointments"], [])

    def test_missing_fields_and_nonobject_json_are_rejected(self):
        for payload in ({}, [], "demo", {"patient_alias": "Demo"}):
            with self.subTest(payload=payload):
                status, body, _ = self.request("POST", "/api/appointments", payload)
                self.assertEqual(status, 400, body)

    def test_invalid_status_and_extra_patch_fields_are_rejected(self):
        item = self.create()
        route = f"/api/appointments/{item['id']}"
        for payload in ({"status": "unknown"}, {"status": 1}, {}, {"status": "completed", "clinician": "Other"}):
            with self.subTest(payload=payload):
                status, body, _ = self.request("PATCH", route, payload)
                self.assertEqual(status, 400, body)

    def test_missing_appointment_returns_not_found(self):
        status, body, _ = self.request("PATCH", "/api/appointments/99999", {"status": "completed"})
        self.assertEqual(status, 404, body)

    def test_malformed_json_and_wrong_media_type(self):
        status, body, _ = self.request("POST", "/api/appointments", raw=b"{", headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400, body)
        status, body, _ = self.request("POST", "/api/appointments", raw=b"{}", headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415, body)

    def test_large_body_is_rejected(self):
        status, body, _ = self.request("POST", "/api/appointments", raw=b" " * 9000, headers={"Content-Type": "application/json"})
        self.assertEqual(status, 413, body)

    def test_cross_origin_mutations_rejected_but_same_origin_works(self):
        status, body, _ = self.request("POST", "/api/appointments", self.appointment(), headers={"Origin": "https://example.invalid"})
        self.assertEqual(status, 403, body)
        status, body, _ = self.request("POST", "/api/appointments", self.appointment(), headers={"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(status, 201, body)

    def test_static_assets_health_and_private_files(self):
        status, body, headers = self.request("GET", "/api/health")
        self.assertEqual((status, body), (200, {"status": "ok"}))
        self.assertIn("no-store", headers.get("Cache-Control", ""))
        for route, content_type in (("/", "text/html"), ("/app.js", "javascript"), ("/styles.css", "text/css")):
            with self.subTest(route=route):
                status, body, headers = self.request("GET", route)
                self.assertEqual(status, 200)
                self.assertIn(content_type, headers.get("Content-Type", ""))
                self.assertTrue(body.strip())
                self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        for route in ("/app.py", "/../app.py", "/.local/appointments.sqlite3", "/api/unknown"):
            with self.subTest(route=route):
                status, _, _ = self.request("GET", route)
                self.assertEqual(status, 404)

    def test_demo_seed_is_repeatable_without_duplicate_rows(self):
        self.stop_server()
        self.database = Path(self.directory.name) / "seeded.sqlite3"
        self.start_server(seed_demo=True)
        _, before, _ = self.request("GET", "/api/appointments")
        self.assertEqual(len(before["appointments"]), 4)
        self.stop_server()
        self.start_server(seed_demo=True)
        _, after, _ = self.request("GET", "/api/appointments")
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
