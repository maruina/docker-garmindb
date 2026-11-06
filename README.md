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
| `SLEEP_SECONDS` | `86400` | Sleep between sync cycles in seconds |

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
