# fishing-conditions-notice

Fishing conditions notice for **Goleta Pier** (Santa Barbara, CA).

Data sources closest to the pier:

- **Sofar Spotter SPOT-1644** — offshore Santa Barbara; accessible via [Aqualink Site 2986][aqualink-site] and the [Sofar Dashboard][sofar-dashboard]
- **NDBC Station 46053** — NOAA buoy, Santa Barbara Channel

## Fishing Conditions Notice

Sends a weekly email via SMTP with water temperature, wind, wave, and moon conditions. Timezone is derived automatically from `LOCATION`. Fridays always send. Tuesdays send only if the 4-hour time-weighted average exceeds the configured water temperature threshold.

Water temperature is sourced from the spotter data endpoint (raw readings, ~10-minute cadence). Wind and wave conditions are sourced from the latest data endpoint (most recent reading per metric).

## Scripts

### `scripts/fishing-conditions-notice.py`

Fetches spotter data from the [Aqualink API][aqualink-api-docs], checks the recent temperature window, and sends an HTML email with a 7-day temperature plot, current wind and wave conditions, and moon phase data.

```shell
uv run scripts/fishing-conditions-notice.py --help
```

| Flag | Default | Description |
|---|---|---|
| `--location` | env: `LOCATION` | Fishing location (geocoded for moon data) |
| `--site-id` | env: `AQUALINK_SITE_ID` | Aqualink site ID |
| `--water-temperature-threshold` | none | Only send if water temperature (°F) exceeds this value; omit to always send |
| `--check-hours` | `4` | Window (hours) for time-weighted average |
| `--plot-days` | `7` | Days of history shown in plot |
| `--email-to` | env: `EMAIL_TO` | Override recipient(s) — useful for local testing |
| `--dry-run` | — | Generate email without sending; opens preview in browser |
| `--output` / `-o` | — | Write HTML to a file; suppresses browser open in `--dry-run` |
| `--dev` | — | Watch source files and serve live-reloading preview at `http://127.0.0.1:5500` |

### `scripts/find-nearest-site.py`

Finds the nearest Aqualink sites to a given location.

```shell
uv run scripts/find-nearest-site.py --location "Goleta, CA"
uv run scripts/find-nearest-site.py --lat 34.418 --lon -119.827
```

### `scripts/setup-notice.py`

Interactive setup — collects configuration, writes `.env`, and pushes vars/secrets to GitHub Actions.

```shell
make setup-notice
```

## Development

```shell
make dev
```

Starts a live-reloading preview server. Template changes rebuild immediately; script changes restart the process.

## Configuration

### Local development

Copy `.env.example` to `.env` and fill in values:

```shell
cp .env.example .env
```

`.env` is gitignored.

| Variable | Description |
|---|---|
| `LOCATION` | Fishing location (e.g. `Goleta, CA`) — used for moon data, timezone, and site search |
| `AQUALINK_SITE_ID` | Aqualink site ID |
| `WATER_TEMPERATURE_THRESHOLD` | Alert threshold in °F — stored in `.env` for `setup-notice.py` to push to GitHub; passed as `--water-temperature-threshold` in workflow, not read by the script from env |
| `SMTP_HOST` | SMTP server (e.g. `smtp.resend.com`) |
| `SMTP_PORT` | SMTP port (e.g. `587`) |
| `SMTP_USER` | SMTP username |
| `SMTP_PASSWORD` | SMTP password or API key |
| `EMAIL_FROM` | Sender address — quote if using display name: `"Fishing Conditions Notice <noreply@example.com>"` |
| `EMAIL_TO` | Recipient address(es), comma-separated |

### GitHub Actions

`.env` is not used by the workflow — set configuration via **Settings → Secrets and variables → Actions**.

Run `make setup-notice` to push all values automatically.

**Variables** (non-sensitive):

| Variable | Value |
|---|---|
| `LOCATION` | Fishing location |
| `AQUALINK_SITE_ID` | Aqualink site ID |
| `WATER_TEMPERATURE_THRESHOLD` | Alert threshold in °F |
| `SMTP_HOST` | SMTP server |
| `SMTP_PORT` | SMTP port |
| `SMTP_USER` | SMTP username |

**Secrets** (sensitive):

| Secret | Value |
|---|---|
| `SMTP_PASSWORD` | SMTP password or API key |
| `EMAIL_FROM` | Sender address |
| `EMAIL_TO` | Recipient address(es) |

The workflow runs daily via cron but only sends on Fridays (always) and Tuesdays (if water temperature exceeds `WATER_TEMPERATURE_THRESHOLD`). Trigger manually via **Actions → Fishing Conditions Notice → Run workflow** — requires specifying recipient(s) at dispatch time.

## Links

- [Aqualink Site 2986][aqualink-site]
- [Aqualink API Docs][aqualink-api-docs]
- [Aqualink OpenAPI JSON][aqualink-openapi-json]
- [Sofar Spotter SPOT-1644 dashboard][sofar-dashboard]
- [NDBC Station 46053][ndbc-46053]

[aqualink-site]: https://aqualink.org/sites/2986
[aqualink-api-docs]: https://production-dot-ocean-systems.uc.r.appspot.com/api/docs
[aqualink-openapi-json]: https://production-dot-ocean-systems.uc.r.appspot.com/api/docs-json
[sofar-dashboard]: https://spotter.sofarocean.com/public/SPOT-1644
[ndbc-46053]: https://www.ndbc.noaa.gov/station_page.php?station=46053
