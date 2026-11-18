# docker-garmindb

Docker image for [GarminDB](https://github.com/tcgoetz/GarminDB) — syncs Garmin Connect health data into local SQLite databases.

Public image: `ghcr.io/maruina/docker-garmindb`

## Usage

```console
docker run -e GARMIN_CONNECT_EMAIL=<YOUR_GARMIN_USER> -e GARMIN_CONNECT_PASSWORD=<YOUR_GARMIN_PASSWORD> -v $HOME/.GarminDb:/local-databases/.GarminDb -v $HOME/HealthData:/local-databases/HealthData ghcr.io/maruina/docker-garmindb:latest
```

GarminDB resolves its config, database, and download directories relative to `$HOME` (set to `/local-databases` in the image). Mount at least the config directory (`.GarminDb`, holding `GarminConnectConfig.json` and the token store) and the `HealthData` tree for durable data.

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `GARMIN_CONNECT_EMAIL` | (required by GarminDB) | Garmin account email |
| `GARMIN_CONNECT_PASSWORD` | (required by GarminDB) | Garmin account password (or app token, per upstream) |
| `SYNC_TIMEOUT` | `3600` | Max seconds for one sync cycle (`timeout` bound; a hung run fails loudly) |
| `METRICS_TIMEOUT` | `1800` | Max seconds for the post-sync metrics fetch (`timeout` bound) |
| `SLEEP_SECONDS` | `86400` | Sleep between sync cycles in seconds |

## Endpoint-only metrics

GarminDB imports data that arrives as files (FIT, activity/sleep/weight JSON).
Several Garmin Connect metrics are computed server-side and exist only behind
per-date API endpoints, so they never reach the SQLite databases. The image
ships `fetch_metrics.py`, which the entrypoint runs after each sync cycle and
which stores them in a sixth database, `HealthData/DBs/garmin_metrics.db`:

| Table | Contents | History depth |
|---|---|---|
| `training_status` | Daily status phrase, ACWR status, acute/chronic load, VO2 max | Meaningful from ~2023; older dates store rows without values |
| `training_readiness` | Per-device daily score, level, feedback | From ~2025 (device support); earlier dates return empty |
| `race_predictions` | 5K/10K/half/marathon snapshot per run | Current-only: the endpoint takes no date argument |
| `lactate_threshold` | Lactate threshold heart rate and speed, stored when the value changes | Snapshot endpoint, no per-date history |
| `body_composition` | Daily body fat, muscle, bone, water from the Index Scale | From the scale's first sync; weight itself lives in GarminDB's Weight table |
| `endurance_score` | Weekly group endurance-score averages and max | From ~2025-02; earlier ranges return no values |
| `hill_score` | Per-group hill-score entry (JSON payload) | From the device's hill-score feature window |
| `workouts` | Workout library definitions (name, sport, full JSON) | Current library; the calendar never links these to days, so no scheduling history exists |

Endpoints probed and deliberately skipped because they return nulls for this
account: activity weather (weather is never attached to recorded activities)
and activity exercise sets (gym sessions carry no structured sets).

Incremental mode (entrypoint default) resumes from the last stored date per
table. For manual backfill, exec into the running container:

```console
python3 /usr/local/bin/fetch_metrics.py --start-date 2023-01-01 --end-date 2026-11-15
```

The script is read-only with respect to GarminDB's own databases and writes
only `garmin_metrics.db`.

## Release flow

Only the Docker image is released (no Helm chart).

1. Merge dependency updates (Renovate PRs).
2. Create the release manually:

   ```console
   gh release create vX.Y.Z --target main --generate-notes --notes-start-tag v<PREVIOUS>
   ```

   The `v*` tag triggers `.github/workflows/build.yaml`, which builds, smoke-tests, and pushes `ghcr.io/maruina/docker-garmindb:<version>`. Confirm the image is on ghcr.io before referencing it elsewhere:

   ```console
   docker manifest inspect ghcr.io/maruina/docker-garmindb:X.Y.Z
   ```

## Renovate

`renovate.json` extends `config:recommended` and tracks two surfaces:

- The base-image digest pin in `FROM python@sha256:...` (docker datasource).
- The `garmindb==<version>` pip pin (PyPI datasource via a regex manager — Renovate does not scan bare `pip install` lines without it).

## Sync cadence

The entrypoint runs one sync cycle per loop iteration and sleeps `SLEEP_SECONDS` (~24h) between cycles. The deployed consumer (the `garmin-sqlite` app in `maruina/malazan.xyz`) monitors sync freshness via a Datadog heartbeat monitor that expects a completion log line per cycle; see that repo's DR runbook for login and recovery procedures.
