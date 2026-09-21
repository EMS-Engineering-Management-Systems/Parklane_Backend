# Parklane Backend

CRUD backend for the Parklane Integrated Smart Building. FastAPI + PyMongo, two
databases on one MongoDB server:

| Database | Holds | Base path |
| --- | --- | --- |
| `Atal` | BMS readings, one collection per device per granularity | `/api/readings`, `/api/devices` |
| `Atal_Users` | Residents, units, parking, visitors, maintenance, community | `/api/users` |

The real devices are not commissioned yet, so nothing is pre-provisioned: a
collection is created the first time you POST a reading into it, and the device
registers itself at the same moment. Dummy data matching the approved tag ranges is
available via `python -m scripts.seed` until live data arrives.

---

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows;  source .venv/bin/activate elsewhere
pip install -r requirements.txt

copy .env.example .env            # then set MONGODB_URI
python -m scripts.seed            # optional: fill Atal with dummy data
python -m app.main                # http://localhost:8000/api
```

Or run it with uvicorn directly, which is what you want in production:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
uvicorn app.main:app --reload      # development
```

**Interactive docs are generated from the code**: <http://localhost:8000/docs>
(Swagger UI), <http://localhost:8000/redoc>, and the raw schema at `/openapi.json`.
`GET /api` returns a plain-text route index for curl.

```bash
python -m scripts.smoke_test                       # 36 checks over Atal
python -m scripts.smoke_test_users                 # 29 checks over Atal_Users
python -m scripts.smoke_test --mongod <path/to/mongod>   # throwaway database
```

---

## Data model

### Database

`Atal` — set by `MONGODB_DB`.

### Collections

```
<devicetype>_<index>_<granularity>
```

all lower-case, for example:

| Collection | Holds |
| --- | --- |
| `transformer_1_instant` | raw samples, one document per timestamp |
| `transformer_1_monthly` | one document per calendar month |
| `transformer_1_yearly` | one document per calendar year |

Plus one fixed collection, `devices`, the registry of every device the system has
seen (unique on `deviceId`).

The three granularity names come from `GRANULARITIES` in `.env`. The first entry is
the raw tier that rollups read from; change the list there if you want different
names or an extra tier such as `daily`.

### Reading document — `instant`

```json
{
  "_id": "6ab0df984dd2764b7c0f6cd2",
  "deviceId": "Transformer_1",
  "deviceType": "Transformer",
  "index": 1,
  "granularity": "instant",
  "timestamp": "2026-03-15T10:00:00Z",
  "tags": {
    "Voltage": 10476,
    "Current": 912,
    "Frequency": 50,
    "Power": 2253,
    "kWh": 262950628,
    "Temperature": 37,
    "alarms": 0,
    "Trip_Status": 0
  },
  "source": "api",
  "createdAt": "2026-03-15T10:00:01.114Z",
  "updatedAt": "2026-03-15T10:00:01.114Z"
}
```

### Reading document — `monthly` / `yearly`

Same shape, but each tag becomes a summary and the period it covers is stored
alongside, so a client can read both tiers with one code path.

```json
{
  "deviceId": "Transformer_1",
  "granularity": "monthly",
  "timestamp": "2026-03-01T00:00:00Z",
  "period": { "year": 2026, "month": 3 },
  "tags": {
    "Voltage": { "count": 120, "min": 41, "max": 14962, "sum": 901234, "avg": 7510.3, "first": 10476, "last": 9033 }
  },
  "source": "rollup",
  "meta": { "sourceCollection": "transformer_1_instant", "sampleCount": 120 }
}
```

### Tag names

Tags are stored short — `Voltage`, not `Transformer_Voltage`. The API accepts either
form on write (and `Transformer Voltage` with a space, as the BMS export writes it)
and normalises it. A flat export row works as a body too:

```json
{ "Timestamp": "2026-03-15T10:00:00Z", "Voltage": 10476, "Current": 912 }
```

is folded into `tags` automatically, so CSV rows can be posted straight through.

---

## Devices

28 device types and 174 tags are shipped in [app/data/devices.json](app/data/devices.json),
built from `Parklane_Tags_Bool_Min_Max.xlsx`:

| Category | Device types |
| --- | --- |
| Electrical | Transformer, RMU, MDB, Emergency_Electrical_Panel, Generator, ATS, SolarPV, Electrical_Digital_Meters |
| Vertical Transportation | Elevators |
| ELV / Security | CCTV, AI_Video_Analytics, Security_Access_Control |
| ELV | IPTV, Intercom |
| ELV / IT | Data_System |
| Life Safety | Fire_Alarm |
| Plumbing | Water_Tanks, Lifting_Booster_Pumps, Water_Meters |
| Fire Fighting | Fire_Fighting_Tank, Electrical_Fire_Pump, Diesel_Fire_Pump, Jockey_Pump |
| Ventilation | Exhaust_Fans, Fresh_Air_Fans, Jet_Fans, Top_Roof_Fans, Staircase_Fans |

Each entry carries every tag with its `kind` (`boolean` or `numeric`) and, for numeric
tags, the engineering `min`/`max`. That drives three things: dummy-data generation,
range warnings on write, and the `/api/devices/catalogue` endpoints.

Regenerate it after the tag list changes:

```bash
python tools/build_registry.py --input ../Parklane_Tags_Bool_Min_Max.xlsx
```

**Device types outside the catalogue are still accepted.** Posting to
`/api/readings/Chiller/1/instant` creates `chiller_1_instant` and registers `Chiller_1`;
the response carries an `UNKNOWN_DEVICE_TYPE` warning to say the reading was stored
without tag validation. Nothing has to be added to the catalogue first.

---

## API

Base path `/api`. Every response is `{"success": true, ...}`; errors are
`{"success": false, "error": {"status", "message", "details"}}` — including
validation failures, which are returned as **400**, not FastAPI's default 422.

### Health and discovery

| Method | Path | |
| --- | --- | --- |
| GET | `/api` | route index |
| GET | `/api/health` | uptime, DB ping, collection count |
| GET | `/api/collections` | every collection with its document count |
| DELETE | `/api/collections/{name}?confirm=true` | drop a collection |

### Catalogue

| Method | Path | |
| --- | --- | --- |
| GET | `/api/devices/catalogue` | all 28 device types |
| GET | `/api/devices/catalogue/{device_type}` | one type with its full tag list |

### Devices

| Method | Path | |
| --- | --- | --- |
| GET | `/api/devices` | registered devices — filter by `deviceType`, `category`, `status` |
| POST | `/api/devices` | register one ahead of any reading |
| GET | `/api/devices/{device_type}/{index}` | one device, its collections and document counts |
| PATCH / PUT | `/api/devices/{device_type}/{index}` | update `label`, `category`, `location`, `description`, `status`, `meta` |
| DELETE | `/api/devices/{device_type}/{index}` | unregister; add `?dropCollections=true` to also delete its data |

### Readings — the CRUD surface

All under `/api/readings/{device_type}/{index}/{granularity}`.

| Method | Path | |
| --- | --- | --- |
| POST | `…/{granularity}` | **create** one reading or an array; creates the collection if new |
| POST | `…/{granularity}/upsert` | create-or-update keyed on `timestamp` (idempotent ingestion) |
| GET | `…/{granularity}` | **list**, paginated and filterable |
| GET | `…/{granularity}/latest` | newest matching reading |
| GET | `…/{granularity}/stats` | min/max/avg/sum/last per tag over the window |
| GET | `…/{granularity}/{id}` | **read** one |
| PUT | `…/{granularity}/{id}` | **replace** the whole reading |
| PATCH | `…/{granularity}/{id}` | **update** — merges at tag level, so you can change one tag |
| DELETE | `…/{granularity}/{id}` | **delete** one |
| DELETE | `…/{granularity}` | delete a range; refuses without a filter unless `confirm=true` |
| POST | `/api/readings/{device_type}/{index}/rollup/{granularity}` | rebuild `monthly` or `yearly` from `instant` |

### Query parameters on list / stats / bulk delete

| Parameter | Example | |
| --- | --- | --- |
| `page`, `limit` | `?page=2&limit=50` | limit is capped by `MAX_PAGE_SIZE` |
| `sort`, `order` | `?sort=timestamp&order=asc` | default `timestamp` descending |
| `fields` | `?fields=timestamp,tags.Voltage` | projection |
| `from`, `to` | `?from=2026-01-01&to=2026-02-01` | inclusive time window |
| `year`, `month` | `?year=2026&month=3` | for monthly/yearly |
| `source` | `?source=seed` | `api`, `seed` or `rollup` |
| `tags.<Name>` | `?tags.Trip_Status=1` | exact match |
| `tags.<Name>[op]` | `?tags.Voltage[gte]=9000&tags.Voltage[lte]=11000` | `gt` `gte` `lt` `lte` `ne` `in` `nin` |

---

## Examples

Create a reading — the collection does not need to exist:

```bash
curl -X POST http://localhost:8000/api/readings/Transformer/1/instant \
  -H 'content-type: application/json' \
  -d '{
        "timestamp": "2026-03-15T10:00:00Z",
        "tags": { "Voltage": 10476, "Current": 912, "Frequency": 50, "Trip_Status": 0 }
      }'
```

```json
{
  "success": true,
  "collection": "transformer_1_instant",
  "insertedCount": 1,
  "warnings": [],
  "data": { "_id": "...", "deviceId": "Transformer_1", "...": "..." }
}
```

Batch insert (an array body works on the same route):

```bash
curl -X POST http://localhost:8000/api/readings/MDB/1/instant \
  -H 'content-type: application/json' \
  -d '[{"timestamp":"2026-03-15T10:00:00Z","tags":{"Voltage":411,"kW":1905}},
       {"timestamp":"2026-03-15T10:01:00Z","tags":{"Voltage":409,"kW":1880}}]'
```

Update a single tag without touching the rest:

```bash
curl -X PATCH http://localhost:8000/api/readings/Transformer/1/instant/<id> \
  -H 'content-type: application/json' -d '{"tags":{"Voltage":10500}}'
```

Build the monthly and yearly tiers:

```bash
curl -X POST http://localhost:8000/api/readings/Transformer/1/rollup/monthly
curl -X POST http://localhost:8000/api/readings/Transformer/1/rollup/yearly
```

More requests, ready to fire from VS Code's REST Client, are in
[requests.http](requests.http). Everything is also runnable straight from `/docs`.

---

## Atal_Users - residents and community

### Where it came from

`Parklane_Dummy_Data_youssef.csv` and `Parklane_Dummy_Data_Ali.csv` are one
resident's app screen each, re-snapshotted every 15 minutes: 142 columns x
10,000 rows, from 2026-09-21 to 2027-01-03.

Only **25 of the 142 columns ever change**. The other 117 - unit price, contract
details, the technician's phone number - are byte-identical on all 10,000 rows.
Loading the files row-for-row would have stored each of those 10,000 times, so
the flat rows are decomposed into the entities they actually describe. 20,000
rows become 36 entity documents plus the readings that genuinely vary.

Three quirks in the source worth knowing:

- **Duplicated columns.** `Home.Unit.Code/Bedrooms/Tower` are exact copies of
  `Unit.Code/Bedrooms/Tower`, and `Parking.MyParking.SlotCode` copies
  `Unit.ParkingSlotCode`. Only the `Unit.*` set is imported.
- **The two files disagree on building-wide numbers.** At the same moment
  Youssef's file says 44 spaces free and Ali's says 45; EV chargers in use are
  1 vs 9. The files were generated independently, so they are merged into one
  reading per 15-minute bucket - see below.
- **`Login.Password` is plaintext**, cycling `DummyPass#1000` to `#1999`. It is
  hashed with bcrypt on import and never leaves the database.

### Merging the two feeds

Youssef samples at :16/:31/:46 and Ali at :17/:32/:47, so the two never share a
timestamp. Both are floored to the 15-minute boundary, numeric fields averaged,
and `sources` records which feeds contributed:

```json
{
  "timestamp": "2027-01-03T19:00:00Z",
  "availableSpaces": 74, "totalSpaces": 120,
  "evUsedChargers": 2, "evTotalChargers": 12,
  "occupancyPercent": 38.3,
  "sources": ["ali", "youssef"]
}
```

`occupancyPercent` is **recomputed** from the merged counts rather than averaged,
so the stored percentage always agrees with the stored spaces.

### Collections

| Resource | Collection | Key | Scope |
| --- | --- | --- | --- |
| `residents` | `residents` | `residentId` | per resident |
| `units` | `units` | `unitCode` | per unit |
| `vehicles` | `vehicles` | `plate` | per resident |
| `parking-slots` | `parking_slots` | `slotCode` | per resident |
| `parking-occupancy` | `parking_occupancy` | `timestamp` | **building-wide** |
| `parking-slot-status` | `parking_slot_status` | `residentId`+`timestamp` | per resident |
| `parking-activity` | `parking_activity` | `residentId`+`type`+`eventAt` | per resident |
| `parking-guest-requests` | `parking_guest_requests` | `residentId`+`date`+`startTime` | per resident |
| `visitor-passes` | `visitor_passes` | `residentId`+`name`+`visitDate`+`visitTime` | per resident |
| `visitor-pass-requests` | `visitor_pass_requests` | `residentId`+`mobileNumber`+`visitDate` | per resident |
| `maintenance-requests` | `maintenance_requests` | `requestId` | per resident |
| `community-announcements` | `community_announcements` | `key` | **shared** |
| `community-events` | `community_events` | `key` | **shared** |
| `community-event-participation` | `community_event_participation` | `residentId`+`eventKey` | per resident |
| `community-alerts` | `community_alerts` | `key` | **shared** |
| `community-polls` | `community_polls` | `key` | **shared** |
| `community-poll-votes` | `community_poll_votes` | `residentId`+`pollKey` | per resident |
| `weather` | `weather` | `timestamp` | **building-wide** |

`GET /api/users` returns this table live, with document counts.

### CRUD

Every resource gets the same operations under `/api/users/{resource}`:

| Method | Path | |
| --- | --- | --- |
| GET | `/api/users/{resource}` | list, filtered and paginated |
| POST | `/api/users/{resource}` | create one or an array |
| DELETE | `/api/users/{resource}` | delete a filtered set; `confirm=true` required without a filter |
| GET | `/api/users/{resource}/{id}` | read one |
| PUT | `/api/users/{resource}/{id}` | replace |
| PATCH | `/api/users/{resource}/{id}` | update the supplied fields only |
| DELETE | `/api/users/{resource}/{id}` | delete one |

`{id}` accepts either the business key or the ObjectId, so
`/api/users/residents/youssef` and `/api/users/residents/6ab125...` both work.

These are generated from one registry in
[app/services/resources.py](app/services/resources.py) rather than written out
eighteen times, so the contract cannot drift between collections. Adding a
collection means adding one `Resource` entry.

Query parameters: `page`, `limit`, `sort`, `order`; a `?q=` substring search over
each resource's searchable fields; `?from=` / `?to=` on resources that have a
time field; and exact match on the declared filterable fields. **An undeclared
filter is rejected with the list of allowed ones**, rather than silently matching
nothing.

```bash
curl "http://localhost:8000/api/users/visitor-passes?residentId=youssef&status=Scheduled"
curl "http://localhost:8000/api/users/maintenance-requests?q=oven"
curl "http://localhost:8000/api/users/weather?from=2026-10-01&to=2026-10-02"

curl -X PATCH http://localhost:8000/api/users/units/H-601 \
  -H "content-type: application/json" \
  -d "{\"finance\": {\"paidPercent\": 70.0}}"
```

### Passwords

`residents.passwordHash` holds a bcrypt hash. It is excluded by a projection on
every read path, so no route can return it - the smoke test asserts this. A PUT
or PATCH that omits `password` leaves the existing hash untouched; supplying
`password` re-hashes it. There is still **no authentication on the API itself**;
this only means credentials are stored correctly when you add it.

### Importing

```bash
python -m scripts.import_users            # upserts, safe to re-run
python -m scripts.import_users --drop     # empty the collections first
python -m scripts.import_users --files ../Parklane_Dummy_Data_Ali.csv
```

---

## Write-time validation

Writes are deliberately permissive — commissioning data is messy and rejecting it
loses it. A reading is **rejected** (400) only when it is structurally wrong: no tags,
a non-finite number, a bad timestamp, an unknown granularity, or an attempt to change
an immutable field (`_id`, `deviceId`, `deviceType`, `index`, `granularity`, `createdAt`).

Anything else is stored and reported back in `warnings`:

| Code | Meaning |
| --- | --- |
| `UNKNOWN_TAGS` | tag is not in the approved list for this device type |
| `OUT_OF_RANGE` | numeric tag falls outside its engineering `min`/`max` |
| `UNKNOWN_DEVICE_TYPE` | device type is not in the catalogue, so tags were not checked |

---

## CLI

| Command | |
| --- | --- |
| `python -m app.main` | run the server |
| `uvicorn app.main:app --reload` | run with auto-reload |
| `python -m scripts.seed` | dummy data for all 28 devices, then roll up |
| `python -m scripts.seed --devices Transformer,MDB --instances 2 --rows 2000` | a subset |
| `python -m scripts.seed --drop` | same, dropping the device's collections first |
| `python -m scripts.rollup` | rebuild monthly/yearly for every device |
| `python -m scripts.rollup --device Transformer --index 1 --granularity monthly --from 2026-01-01` | one device, one window |
| `python -m scripts.list_collections` | list collections and document counts |
| `python -m scripts.smoke_test` | end-to-end suite for `Atal` |
| `python -m scripts.import_users` | load the resident CSVs into `Atal_Users` |
| `python -m scripts.smoke_test_users` | end-to-end suite for `Atal_Users` |

`seed` flags: `--rows` (per device, default 480), `--months` (history window, default 13),
`--instances`, `--devices`, `--seed` (PRNG seed, so runs are reproducible), `--drop`,
`--no-rollup`.

Values follow the same rules as the original `generate_parklane_data.py`: boolean tags
get 0 or 1, numeric tags a value inside their Min/Max.

---

## Configuration

| Variable | Default | |
| --- | --- | --- |
| `MONGODB_URI` | `mongodb://localhost:27017` | a literal `<password>` in it is replaced with `MONGODB_PASSWORD`, URL-encoded |
| `MONGODB_PASSWORD` | — | kept out of the URI |
| `MONGODB_DB` | `Atal` | BMS readings |
| `MONGODB_USERS_DB` | `Atal_Users` | residents and community |
| `HOST` | `0.0.0.0` | |
| `PORT` | `8000` | |
| `APP_ENV` | `development` | `production` disables reload |
| `CORS_ORIGIN` | `*` | comma-separated list |
| `GRANULARITIES` | `instant,monthly,yearly` | first entry is the raw tier |
| `DEFAULT_PAGE_SIZE` | `100` | |
| `MAX_PAGE_SIZE` | `1000` | |

For Atlas:

```
MONGODB_URI=mongodb+srv://<user>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority
MONGODB_PASSWORD=your-password
```

---

## Layout

```
app/
  main.py                   app factory, lifespan, uvicorn entrypoint
  core/
    config.py               pydantic-settings, typed environment
    database.py             one client, two databases, <password> substitution
    errors.py               ApiError and the handlers that render the envelope
    security.py             bcrypt hashing for resident passwords
  data/devices.json         28 devices / 174 tags, generated from the XLSX
  models/
    reading.py              request models (ReadingIn, ReadingPatch, DeviceIn, DevicePatch)
    responses.py            response models, which is what /docs describes
    users.py                entity models for Atal_Users
  routers/
    meta.py                 health, route index
    devices.py              catalogue and device registry
    readings.py             the CRUD routes
    collections.py          raw collection view
    users.py                Atal_Users CRUD, generated from the resource registry
  services/
    naming.py               naming, parsing, device-type canonicalisation
    registry.py             catalogue + self-registration
    schema.py               payload -> document, validation, warnings
    filters.py              query string -> pagination, projection, Mongo filter
    readings.py             the CRUD against MongoDB
    rollup.py               instant -> monthly / yearly
    resources.py            the Atal_Users resource registry and its CRUD engine
scripts/                    seed, rollup, list_collections, import_users, smoke tests
tools/build_registry.py     regenerates app/data/devices.json from the XLSX
```

### Why synchronous PyMongo

Every path operation is a plain `def`, not `async def`, so FastAPI runs it in its
worker threadpool and the blocking driver calls never touch the event loop. That
keeps the service code straightforward at Parklane's scale. Switching to Motor later
means making the routers `async def` and awaiting the driver calls — the structure
does not otherwise change.

---

## Indexes

Created on each reading collection at first write:

- `timestamp` descending — every list and `latest` query sorts on it
- `deviceId` + `timestamp` — compound
- `period.year` + `period.month` descending — monthly and yearly only

And `deviceId` unique on `devices`.

---

## Notes for the next stage

- **Granularity name.** The brief wrote `instat`; this ships as `instant`. If the
  upstream system really does use `instat`, change `GRANULARITIES` in `.env` — nothing
  else needs touching.
- **Authentication.** There is none. Everything is open, which is fine behind the BMS
  VLAN but needs an API key or OAuth before anything reaches a public interface.
  FastAPI's `Security` dependencies are the natural place to add it.
- **Rollups are manual.** They run from the API or the CLI. Schedule
  `python -m scripts.rollup` (cron / Task Scheduler) once ingestion is live, or call
  the rollup endpoint from the ingestion job.
- **Instance counts.** Every device is modelled as `_1` until the real quantities are
  confirmed from the shop drawings; the index in the route is free-form, so `_2`, `_3`
  and so on work the moment you post to them.
