# GreenWatch

A small, self-hosted GreenCloud APAC KVM stock monitor. One Docker container serves the dashboard, scrapes the store, sends Telegram channel alerts, and maintains a SQLite database. No browser engine, Node runtime, Redis, external database, or separate cron container is needed.

## Deploy

Requires Docker Engine with Docker Compose v2.

```sh
cp .env.example .env
# Edit .env and add your Telegram credentials.
docker compose up -d --build
```

Open **http://localhost:8080**. The container checks the live store on first startup; the page fills automatically. No credentials are needed to try the dashboard locally. Without Telegram credentials, matching events stay pending and the dashboard displays setup instructions.

```sh
docker compose logs -f --tail=100
docker compose ps
```

The default port is **8080**, bound to localhost. Set `PORT=8090` to use another host port. For access from another computer, set `BIND_ADDRESS=0.0.0.0` and a strong `DASHBOARD_PASSWORD` in `.env`, then recreate the container. Put a public deployment behind an HTTPS reverse proxy so Basic authentication is encrypted. A remote server can also remain bound to localhost and be accessed over an SSH tunnel:

```sh
ssh -L 8080:127.0.0.1:8080 user@your-server
```

## Connect Telegram

1. Create a bot using [@BotFather](https://t.me/BotFather) and copy its bot token into `TELEGRAM_BOT_TOKEN` in `.env`.
2. Add the bot to your destination channel as an administrator with permission to **Post Messages**.
3. Set `TELEGRAM_CHAT_ID` to `@your_public_channel`. For a private channel, use its numeric ID starting with `-100`. You can obtain that ID from `channel_post.chat.id` in the bot's [getUpdates](https://core.telegram.org/bots/api#getupdates) response after posting a new channel message; getUpdates requires that the bot has no active webhook. Do not share the token or paste it into public tools.
4. Apply environment changes and click **Send test notification** on the dashboard:

```sh
docker compose up -d --force-recreate
```

The test button sends a real message to your configured channel, with a 60-second cooldown. Its result appears on the page. A configured status means the variables are present; the test confirms Telegram permissions and delivery. Test messages do not count toward availability alert totals.

Telegram errors (including missing permissions or an invalid channel) appear in the notification log. The bot token is never returned through the dashboard API or logged in request errors. Both Telegram variables must be set together, or both left empty.

## Scrape completion notifications

Every completed scrape sends a Telegram report with the completion time, trigger, total products, APAC plan count, and matching $25/year plans. You receive a report even when no matching stock is available. Failed scrapes send a failure report and preserve previous inventory. Reports enter the persistent notification log and use the same five-attempt retry policy. They describe a historical run and can deliver even while availability alerts are paused. Reports are only created when Telegram is configured. Set `NOTIFY_SCRAPE_SUMMARY=false` to disable them.

## What triggers an alert

The scraper reads only the public [Budget KVM Sale](https://greencloudvps.com/billing/store/budget-kvm-sale) listing. It does not log in, add items to a cart, or buy anything.

A plan must satisfy all of these rules:

- Its location matches a recognized **Asia-Pacific** location and its name contains **KVM**.
- Its listed price is **exactly $25.00 USD, billed annually**.
- Its explicit stock count is **at least 1**, and it has an enabled GreenCloud order link.

All APAC plans are saved for analysis; the default table filters to $25 annual plans. Switch to **All APAC** to see other prices. The stock chart counts matching plans per successful check, rather than individual available units. The dashboard includes plan specifications, stock, order links, check history, delivery history, next-run countdowns, and location filters.

The actual store fixture captured on 2026-10-23 has 44 plans, including 10 APAC plans across Singapore DC1/DC2, Tokyo IIJ/Softbank, and Hong Kong DC2. Five of those APAC plans cost $25/year. Location detection also covers common APAC cities and countries if the listing expands; add new location phrases with `APAC_EXTRA_KEYWORDS`.

An alert is created the first time a matching plan is observed available. The same plan stays silent while it remains eligible, even across restarts and weekly cleanup. Once an observed stock or price change makes it ineligible, a subsequent qualifying observation creates a new alert. A disappeared card has unknown stock and does not count as a confirmed restock.

Failed Telegram deliveries retry after 1 minute, 5 minutes, 30 minutes, and 1 hour, for **at most five attempts per stock event**. A sold-out, removed, or otherwise ineligible plan cancels pending alerts. Delivery pauses after failed scrapes or when the last successful data is over six hours old. Pending events created before Telegram setup can deliver after configuration, provided fresh data still qualifies. Exhausted events remain visible as failed and are not automatically recreated while the same stock event continues.

Telegram has no idempotency key for `sendMessage`: a connection timeout after Telegram accepted a message, or a process failure between Telegram delivery and the SQLite commit, can produce a duplicate on retry. Scrapes can also miss stock that appears and disappears between checks. Availability is a snapshot and can change before checkout.

## Schedule and data retention

- **Four scheduled checks daily:** 00:00, 06:00, 12:00, and 18:00 in `TZ`, default `Asia/Bangkok`.
- **First startup:** one immediate check, then the clock schedule. Restarting before the next due check preserves that schedule. After downtime, one catch-up check replaces missed slots.
- **Manual checks:** the **Scrape now** button starts an extra check immediately and does not move the regular schedule. Overlapping scrapes are rejected.
- **Weekly cleanup:** Sundays at 03:00 in `TZ`; missed cleanup runs once after restart.
- **Retention:** delete completed scrape runs and their observations older than `RETENTION_DAYS` (default 30), plus old inactive notification records and expired absent catalog entries. Current catalog data, pending events, and active alert state are preserved. SQLite reuses freed pages and performs incremental reclamation and WAL checkpointing.
- **One process/replica:** the container uses one Gunicorn worker with four HTTP threads and an internal background scheduler. Do not increase worker count or run multiple replicas against the same volume.

The container stays running all day to serve the dashboard. Upstream scraping normally takes only a few seconds per six-hour slot. Manual checks are additional; there is no continuous polling of GreenCloud. Dashboard refreshes read only your local SQLite data. A blocked or malformed store page is recorded as an error and preserves the last known inventory.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | empty | BotFather token |
| `TELEGRAM_CHAT_ID` | empty | Channel username or numeric channel ID |
| `NOTIFY_SCRAPE_SUMMARY` | `true` | Send a report after each successful or failed scrape |
| `TZ` | `Asia/Bangkok` | IANA timezone for scheduling and display |
| `RETENTION_DAYS` | `30` | History retained after weekly cleanup, 1–3650 days |
| `PORT` | `8080` | Published host port |
| `BIND_ADDRESS` | `127.0.0.1` | Host interface for the dashboard |
| `DASHBOARD_USER` | `admin` | Basic authentication username |
| `DASHBOARD_PASSWORD` | empty | Set to enable authentication |
| `APAC_EXTRA_KEYWORDS` | empty | Comma-separated additional location phrases |
| `DATABASE_PATH` | `/data/greenwatch.sqlite3` in Docker | SQLite file; Compose fixes this to the mounted volume |

Settings are environment-based. After changing `.env`, run `docker compose up -d --force-recreate`; `docker compose restart` alone does not update the environment. Secrets are excluded from the Docker build and Git.

The container runs as UID/GID 10001, with a read-only application filesystem, writable SQLite volume and small `/tmp`, all capabilities dropped, log rotation, a health check, a 128 MiB memory limit, and a 0.5 CPU limit. Actual image size and memory depend on architecture and workload.

## Data, backup, and updates

SQLite lives in the named `greenwatch-data` volume, mounted at `/data`. Container replacement and normal `docker compose down` preserve it. **`docker compose down -v` deletes the database.**

To make a consistent online backup using SQLite's backup API and copy it to the host:

```sh
docker compose exec greenwatch python -c "import sqlite3; source=sqlite3.connect('/data/greenwatch.sqlite3'); dest=sqlite3.connect('/tmp/greenwatch-backup.sqlite3'); source.backup(dest); dest.close(); source.close()"
docker compose cp greenwatch:/tmp/greenwatch-backup.sqlite3 ./greenwatch-backup.sqlite3
```

To update after source changes:

```sh
docker compose up -d --build
```

There is no schema migration system beyond initial `CREATE TABLE IF NOT EXISTS`. Back up the database before future code changes that modify the schema. The `/healthz` endpoint checks database access and scheduler liveness; scrape failures are shown separately in `/api/status` and the dashboard so a temporary store outage does not make the container unhealthy.

## Local development and tests

Python 3.12+ on Linux or macOS:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m greenwatch
```

Local development binds to `127.0.0.1:8080`, uses `data/greenwatch.sqlite3`, and reads the process environment. `.env` is consumed by Docker Compose; for local development, export the variables explicitly. The application has no JavaScript build step or external font/chart dependencies.

Tests use a trimmed real HTML fixture and mocked Telegram delivery. They cover exact price/region/stock filtering, changed markup, initial alerts and restocks, persisted deduplication, missing cards, retry exhaustion, stale data, cleanup, scheduling, manual overlap protection, authentication, action tokens, and credential redaction. They do not send messages to a real channel.

## HTTP endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | Dashboard |
| GET | `/api/status` | APAC inventory, recent runs, alerts, chart data, scheduling |
| GET | `/api/products/<id>/history` | Up to 120 retained observations for a product |
| POST | `/api/scrape` | Queue one manual scrape; HTTP 202 or 409 if busy |
| POST | `/api/telegram/test` | Send a test channel message |
| GET | `/healthz` | Minimal unauthenticated health check |

All endpoints except `/healthz` require Basic authentication when configured. POST actions also require the per-process `X-CSRF-Token` embedded in the dashboard page. Reload an old dashboard after restarting the container to obtain a new action token.
