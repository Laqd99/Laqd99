#!/usr/bin/env python3
"""A local appointment-tracker demo using fictional data and Python's stdlib."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
from typing import Iterator
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = PROJECT_ROOT / ".local" / "appointments.sqlite3"
MAX_BODY_BYTES = 8 * 1024
STATUSES = frozenset({"scheduled", "completed", "cancelled"})
MINUTE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}\Z")
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)


@contextmanager
def _connection(database: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(database, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _initialize_database(database: Path, *, seed_demo: bool) -> None:
    is_new = not database.exists()
    database.parent.mkdir(parents=True, exist_ok=True)
    with _connection(database) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_alias TEXT NOT NULL,
                clinician TEXT NOT NULL,
                starts_at TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'scheduled'
                    CHECK(status IN ('scheduled', 'completed', 'cancelled'))
            )"""
        )
        connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS scheduled_clinician_time
               ON appointments(clinician, starts_at)
               WHERE status = 'scheduled'"""
        )
        if is_new and seed_demo:
            today = date.today()
            examples = (
                ("Fictional Amber", "Dr. Demo Rowan", -1, "09:00", "Demo follow-up", "completed"),
                ("Fictional Birch", "Dr. Demo Rowan", 0, "14:00", "Demo wellbeing check-in", "scheduled"),
                ("Fictional Cedar", "Dr. Demo Morgan", 1, "10:30", "Demo routine appointment", "scheduled"),
                ("Fictional Dune", "Dr. Demo Morgan", 2, "11:00", "Demo consultation", "cancelled"),
            )
            connection.executemany(
                """INSERT INTO appointments
                   (patient_alias, clinician, starts_at, reason, status)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (alias, clinician, f"{today + timedelta(days=offset)}T{time}", reason, status)
                    for alias, clinician, offset, time, reason, status in examples
                ],
            )


class RequestError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _validate_appointment(payload: dict) -> dict[str, str]:
    expected = {"patient_alias", "clinician", "starts_at", "reason"}
    if payload.keys() != expected:
        raise RequestError(400, "Provide patient_alias, clinician, starts_at, and reason only.")
    result = {}
    for field, limit in (("patient_alias", 60), ("clinician", 60), ("reason", 240)):
        value = payload[field]
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
            raise RequestError(400, f"{field} must contain between 1 and {limit} characters.")
        result[field] = value.strip()
    starts_at = payload["starts_at"]
    if not isinstance(starts_at, str) or not MINUTE_PATTERN.fullmatch(starts_at):
        raise RequestError(400, "starts_at must be a valid date and time in YYYY-MM-DDTHH:MM format.")
    try:
        datetime.strptime(starts_at, "%Y-%m-%dT%H:%M")
    except ValueError:
        raise RequestError(400, "starts_at must be a valid date and time in YYYY-MM-DDTHH:MM format.") from None
    result["starts_at"] = starts_at
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


class AppointmentServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], database: Path) -> None:
        self.database = database
        super().__init__(address, AppointmentHandler)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(10)
        return request, address


class AppointmentHandler(BaseHTTPRequestHandler):
    server: AppointmentServer
    protocol_version = "HTTP/1.1"
    server_version = "AppointmentDemo/1.0"
    sys_version = ""

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_HEAD(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def do_OPTIONS(self) -> None:
        self._dispatch("OPTIONS")

    def send_error(self, code: int, message=None, explain=None) -> None:
        # Keep errors from HTTP parsing and unsupported methods JSON as well.
        self._error(code, self.responses.get(code, ("The request failed.",))[0])

    def _dispatch(self, method: str) -> None:
        try:
            path = urlsplit(self.path).path
            if method == "GET":
                self._get(path)
            elif method == "POST" and path == "/api/appointments":
                self._create()
            elif method == "PATCH" and re.fullmatch(r"/api/appointments/[0-9]+", path):
                self._update(int(path.rsplit("/", 1)[1]))
            elif method in {"POST", "PATCH"}:
                raise RequestError(404, "This endpoint does not exist.")
            else:
                raise RequestError(405, "This HTTP method is not supported.")
        except RequestError as error:
            self._error(error.status, error.message)
        except sqlite3.IntegrityError:
            self._error(409, "This clinician already has a scheduled appointment at that time.")
        except sqlite3.Error:
            self._error(503, "The appointment database is temporarily unavailable. Please try again.")
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except (ValueError, OverflowError):
            self._error(400, "The request is invalid.")

    def _get(self, path: str) -> None:
        if path == "/api/health":
            self._json(200, {"status": "ok"})
            return
        if path == "/api/appointments":
            with _connection(self.server.database) as connection:
                rows = connection.execute("SELECT * FROM appointments ORDER BY starts_at, id").fetchall()
            self._json(200, {"appointments": [dict(row) for row in rows]})
            return
        files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
        }
        if path not in files:
            raise RequestError(404, "This page or endpoint does not exist.")
        filename, content_type = files[path]
        try:
            body = (PROJECT_ROOT / "web" / filename).read_bytes()
        except FileNotFoundError:
            raise RequestError(404, "This page is unavailable.") from None
        self._send(200, body, content_type, api=False)

    def _check_origin(self) -> None:
        origins = self.headers.get_all("Origin", [])
        if not origins:
            return
        hosts = self.headers.get_all("Host", [])
        if len(origins) != 1 or len(hosts) != 1:
            raise RequestError(403, "Requests must come from this application's origin.")
        try:
            origin = urlsplit(origins[0])
            host = urlsplit(f"{origin.scheme}://{hosts[0]}")
            if (
                origin.scheme not in {"http", "https"}
                or not origin.hostname
                or origin.username is not None
                or origin.password is not None
                or origin.path not in {"", "/"}
                or origin.query
                or origin.fragment
                or host.username is not None
                or host.password is not None
                or host.path
                or host.query
                or host.fragment
                or origin.hostname.casefold() != (host.hostname or "").casefold()
                or (origin.port or (443 if origin.scheme == "https" else 80))
                != (host.port or (443 if origin.scheme == "https" else 80))
            ):
                raise ValueError
        except ValueError:
            raise RequestError(403, "Requests must come from this application's origin.") from None

    def _payload(self) -> dict:
        self._check_origin()
        content_types = self.headers.get_all("Content-Type", [])
        if len(content_types) != 1 or content_types[0].partition(";")[0].strip().lower() != "application/json":
            raise RequestError(415, "Send the request as application/json.")
        if self.headers.get("Content-Encoding", "identity").strip().lower() != "identity":
            raise RequestError(415, "Compressed request bodies are not supported.")
        if self.headers.get("Transfer-Encoding"):
            raise RequestError(400, "Use Content-Length instead of Transfer-Encoding.")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]+", lengths[0]):
            raise RequestError(400, "Provide a valid Content-Length for the JSON body.")
        try:
            length = int(lengths[0])
        except ValueError:
            raise RequestError(400, "The request body length is invalid.") from None
        if length > MAX_BODY_BYTES:
            raise RequestError(413, "The request body must be no larger than 8 KiB.")
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError
            payload = json.loads(body.decode("utf-8"), parse_constant=_reject_nonfinite)
        except (UnicodeDecodeError, ValueError, RecursionError, TimeoutError, OSError):
            raise RequestError(400, "Send a complete, valid JSON object.") from None
        if not isinstance(payload, dict):
            raise RequestError(400, "The JSON body must be an object.")
        return payload

    def _create(self) -> None:
        appointment = _validate_appointment(self._payload())
        with _connection(self.server.database) as connection:
            cursor = connection.execute(
                """INSERT INTO appointments (patient_alias, clinician, starts_at, reason)
                   VALUES (:patient_alias, :clinician, :starts_at, :reason)""",
                appointment,
            )
            row = connection.execute("SELECT * FROM appointments WHERE id = ?", (cursor.lastrowid,)).fetchone()
        self._json(201, {"appointment": dict(row)})

    def _update(self, appointment_id: int) -> None:
        payload = self._payload()
        if payload.keys() != {"status"} or not isinstance(payload["status"], str) or payload["status"] not in STATUSES:
            raise RequestError(400, "Provide only status: scheduled, completed, or cancelled.")
        if appointment_id > 9223372036854775807:
            raise RequestError(404, "This appointment does not exist.")
        with _connection(self.server.database) as connection:
            cursor = connection.execute(
                "UPDATE appointments SET status = ? WHERE id = ?", (payload["status"], appointment_id)
            )
            if cursor.rowcount == 0:
                raise RequestError(404, "This appointment does not exist.")
            row = connection.execute("SELECT * FROM appointments WHERE id = ?", (appointment_id,)).fetchone()
        self._json(200, {"appointment": dict(row)})

    def _error(self, status: int, message: str) -> None:
        # Discard unread/invalid request bodies by closing the connection.
        self.close_connection = True
        self._json(status, {"error": message})

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", api=True)

    def _send(self, status: int, body: bytes, content_type: str, *, api: bool) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if api else "no-cache")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def make_server(
    *, host: str = "127.0.0.1", port: int = 8080,
    database: Path | str = DEFAULT_DATABASE, seed_demo: bool = True,
) -> AppointmentServer:
    """Initialize a file database and return a server ready for serve_forever()."""
    database_path = Path(database).expanduser().resolve()
    _initialize_database(database_path, seed_demo=seed_demo)
    return AppointmentServer((host, port), database_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the fictional-data appointment tracker locally.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port (default: 8080)")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE, help="SQLite database file")
    arguments = parser.parse_args()
    try:
        server = make_server(host=arguments.host, port=arguments.port, database=arguments.database)
    except (OSError, sqlite3.Error, ValueError, OverflowError) as error:
        parser.exit(1, f"Could not start the tracker: {error}\n")
    print(f"Fictional-data demo: http://{arguments.host}:{server.server_port}", flush=True)
    print("Use fictional information only. Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping the appointment tracker.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
