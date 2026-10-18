# Care Schedule

A small appointment-tracking demo built with Python’s standard library and SQLite. Create fictional appointments, search and filter the schedule, and mark appointments as scheduled, completed, or cancelled.

![Care Schedule showing fictional appointments and the new appointment form](docs/screenshot.png)

## Run locally

Requires Python 3.10 or newer. No packages, credentials, or external services are needed.

```sh
cd projects/appointment-tracker
python3 app.py
```

Open <http://127.0.0.1:8080> in your browser. The server listens on the local loopback interface by default. Stop it with `Ctrl+C`.

A new database starts with fictional example appointments. Changes persist in `.local/appointments.sqlite3` across restarts. You can choose a different port or database file:

```sh
python3 app.py --port 8081 --database .local/demo.sqlite3
```

Appointment times are local wall-clock dates and times, such as `2030-05-20T09:00`; they do not include a time zone or perform time-zone conversion.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Check server health. |
| `GET` | `/api/appointments` | List appointments. |
| `POST` | `/api/appointments` | Create an appointment. |
| `PATCH` | `/api/appointments/<id>` | Update an appointment’s status. |

Send JSON with `Content-Type: application/json`. For example, create an appointment with:

```json
{
  "patient_alias": "Demo Patient",
  "clinician": "Demo Clinician",
  "starts_at": "2030-05-20T09:00",
  "reason": "Fictional follow-up"
}
```

To update its status, send `{"status": "completed"}` to `/api/appointments/<id>`. Valid statuses are `scheduled`, `completed`, and `cancelled`. Two scheduled appointments for the same clinician at the same time return HTTP `409`; completed and cancelled entries do not reserve that slot.

## Tests

From `projects/appointment-tracker`, run:

```sh
python3 -m unittest discover -s tests -v
```

## Demo scope

This is an educational local demo with no authentication. Use fictional data only; it is not intended to store real patient information or run a clinical service.
