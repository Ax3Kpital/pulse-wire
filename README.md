# Pulse Wire (local, single-user)

Pulse Wire is a local news aggregator for earnings, companies, geopolitics and monetary policy.
It runs on macOS and Linux, without Docker, and listens exclusively on `127.0.0.1:8765`. (Windows: use WSL.) Only the optional autostart helper is macOS-specific.

- **One story per card.** When several publications report the same event, their reports appear in a collapsible list, each with attribution and an `Open original` link.
- **Explainable categories:** Geopolitics (US, China, Europe, Russia), FED (Rates, CPI, PCE, Jobs, Speeches), Energy, Banking, Biotech, Pharma, Oil & Gas, Tech. Classification is rule-based (section, keywords, entities), and the reason behind each label is stored.
- **Automatic refresh every 15 minutes.** `Fetch now` triggers the same operation and cannot create parallel runs: a request made during a scan joins the scan already in progress.
- **Preconfigured real sources:**
  - **Free publications** (preferred as the primary source): CNBC, BBC, The Guardian, Al Jazeera, Deutsche Welle, NPR, Euronews, BioPharma Dive, STAT, OilPrice.com, Utility Dive, Banking Dive, TechCrunch, Ars Technica.
  - **Subscription publications**, marked `paywall`: Financial Times, Bloomberg, The Washington Post. Public RSS only: title and link.
  - **Official sources:** Federal Reserve, BLS, BEA, ECB, Bank of England, Bank of Canada, EIA, FDA, EMA, SEC, White House, UN News, Council of the EU, Kremlin.
  - **Reuters** has no public RSS: it is available only through Reuters Connect, under a licence.

  The app displays **real data only**. Demo fixtures exist for tests only.

## Contents

1. [Installation](#installation)
2. [Running](#running)
3. [Fetch now and scheduled scans](#fetch-now-and-scheduled-scans)
4. [Configuring an authorised source](#configuring-an-authorised-source)
5. [Storage and retention policies](#storage-and-retention-policies)
6. [Classification and deduplication](#classification-and-deduplication)
7. [Impact score 1–10 and colours](#impact-score-110-and-colours)
8. [Tests](#tests)
9. [SQLite backup and restore](#sqlite-backup-and-restore)
10. [Autostart (LaunchAgent, macOS only)](#autostart-launchagent-macos-only)
11. [Logs and troubleshooting](#logs-and-troubleshooting)
12. [Source access rules](#source-access-rules)

## Installation

Requirements: macOS or Linux, Python 3.12+ (tested with 3.14) and internet access for the first install.

```bash
git clone https://github.com/pingulinux87/pulse-wire.git
cd pulse-wire
scripts/setup.sh
```

The script does the following:

1. creates `.venv` and installs the backend (`backend[dev,extract]`);
2. if `node` is not found, downloads the official **Node LTS** binary into `.tools/` (SHA256 checksum verified; it does not modify your system);
3. installs the frontend dependencies and builds the UI (`frontend/dist`);
4. copies `.env.example` to `.env` if `.env` does not exist.

## Running

```bash
scripts/start.sh            # API + UI on http://127.0.0.1:8765
open http://127.0.0.1:8765
```

Alembic migrations run automatically at startup. The first scan starts after about 3 seconds.

Other useful commands, run from `backend/` with `../.venv/bin/python -m app <command>`:

| Command | What it does |
|---|---|
| `serve` (default) | starts the server |
| `scan` | runs a single scan of all sources, then exits |
| `reclassify` | reapplies the classification rules to all articles (after you edit `categories.yaml` or `entities/*.yaml`) |
| `purge-source <key>` | deletes everything fetched from a source (for example after you disable it) |

For development, `scripts/dev.sh` starts the backend with reload and Vite on `http://127.0.0.1:5173`.

## Fetch now and scheduled scans

- APScheduler runs `scan_all_sources` every **15 minutes** (`NEWS_SCAN_INTERVAL_MINUTES`).
- The **Fetch now** button (or `POST /api/scan`) triggers the *same* operation through the same coordinator.
- If a scan is already running, the request does not start another one: it joins the current scan. The UI shows the message "A scan is already running (#N); your request joined it." and a progress indicator. In the run history (`GET /api/runs`), the `coalesced_requests` field counts the merged requests.
- The top bar shows the time of the last refresh and the time of the next scheduled scan.
- A failing source does not stop the others. The error appears in the sidebar (red dot and message), in `GET /api/sources` and in `logs/app.log`.

Error behaviour is covered by tests (`test_failing_source_does_not_stop_others`). A real source that is down appears with a red dot and its error message in the sidebar.

## Configuring an authorised source

All sources live in [`backend/app/config/sources.yaml`](backend/app/config/sources.yaml). The file is re-read on every scan, so no restart is needed. Private URLs and credentials live **only in `.env`**; the YAML contains only the *name* of the variable.

### Authorised RSS or Atom (for example, a syndication feed included in your licence)

```yaml
  - key: licensed-rss-1
    name: Publisher X (licensed feed)
    publisher: Publisher X
    connector: rss
    enabled: true
    url_env: LICENSED_RSS_1_URL      # the value lives in .env
    policy: metadata_only            # or temporary_excerpt / indexable_content, depending on the licence
    retention_days: 30
    priority: 70
```

```bash
# .env
LICENSED_RSS_1_URL=https://feeds.publisher.example/your-private-token/rss
```

### Generic JSON API

```yaml
  - key: my-api
    name: My News API
    connector: json_api
    enabled: true
    url_env: MY_API_URL
    credentials_env: {api_key: MY_API_KEY}
    policy: temporary_excerpt
    options:
      items_path: data.items
      fields: {id: id, title: headline, url: links.web, published: published_at, summary: standfirst}
      auth: {header: Authorization, template: "Bearer {api_key}"}
      since_param: since
```

### Reuters Connect / Financial Times

The `reuters-connect` and `ft-api` skeletons are already in `sources.yaml` and stay **inactive** until you fill in `REUTERS_CONNECT_API_URL` + `REUTERS_CONNECT_ACCESS_TOKEN`, or `FT_API_URL` + `FT_API_KEY`, in `.env`. The field mapping (`options.fields`, `items_path`) must be adapted to the API documentation from your contract. An individual subscription is not an API licence.

### SEC

Under its fair-access policy, the SEC requires contact details in the User-Agent. Set `NEWS_CONTACT=your-address@example.com` in `.env` and the `sec-press` source is enabled. Until then it stays inactive and the reason is shown in the UI.

### ScraplingConnector (allowlist only)

The connector is disabled by default and exists only for sites that explicitly allow automated access. It is configured in `options`:

- `allowlist`: the permitted URL prefixes;
- `permission_confirmed: true`: your confirmation that the site allows automated access;
- `mode`: `feed` or `page`, plus the CSS selectors.

The connector respects `robots.txt`, uses an identifiable User-Agent and a rate limit, and never starts a browser. Only Scrapling's **parser** is used (the base package, `pip install -e 'backend[scrapling]'`). The `fetchers` extra pulls in Playwright and stealth browsers and is not used. The configuration rejects Reuters, Bloomberg, FT and Washington Post.

## Storage and retention policies

Each source declares a `policy` and `retention_days` (plus an optional `metadata_retention_days`):

| Policy | What is stored |
|---|---|
| `metadata_only` | title, link, date, section, attribution. No summary; only the title is used for grouping and classification. |
| `temporary_excerpt` | + a short excerpt (max. 600 characters), deleted automatically after `retention_days` |
| `indexable_content` | + full text, if the feed provides it (or `extract_full_text: true`, using trafilatura with robots.txt respected), indexed for search |

Expired text is deleted at the end of every scan. Metadata and the link to the original remain, so attribution is preserved. If `metadata_retention_days` is set, the entire row is deleted after that period. US federal government feeds (public domain) use `indexable_content`, and those of other institutions use `temporary_excerpt`.

## Classification and deduplication

**Classification** uses rules editable in [`categories.yaml`](backend/app/config/categories.yaml) and [`entities/*.yaml`](backend/app/config/entities/). Each label stores the signals that produced it (for example `keyword "rate cut" in title +2.0`), visible in `GET /api/stories/{id}`. No external AI model is used.

**Deduplication** has two stages:

1. **Identical replays:** the same external ID, the same canonical URL (without `utm_*` and other tracking parameters) or the same normalised title from the same source. These are ignored.
2. **Same story, different publications**, within a **72-hour** window. When the window holds many stories, MinHash/LSH (`datasketch`) only *preselects* candidates. The decision is made by deterministic rules:
   - IDF-weighted title similarity, so common words count for little;
   - shared entities;
   - shared numbers;
   - closeness in time;
   - the permitted summary.

   Explicit vetoes:
   - `number_conflict`: 3.1% vs 2.1%;
   - `subject_conflict`: steel vs autos, October vs September;
   - `date_conflict`;
   - `same_source`.

   A shared company or institution is never sufficient on its own. The score and the rule applied are stored in `story_members.match_reason` and shown in the UI under each source.

The labelled-pair tests (false positives and false negatives) are in [`backend/tests/fixtures/dedupe_pairs.json`](backend/tests/fixtures/dedupe_pairs.json).

## Impact score 1–10 and colours

Each collected article is analysed separately and receives a **score from 1 to 10**. The analysis is deterministic and explainable, with no external AI, and the rules are edited in [`impact.yaml`](backend/app/config/impact.yaml).

The signals that produce the score, per article:

| Signal | What it measures |
|---|---|
| Tone (sentiment analysis) | financial/geopolitical lexicon with polarity −3…+3, negations ("avoids recession"), intensifiers ("sharply"), phrases ("profit warning", "prices rise"); the title counts fully, the RSS description counts half. A strong tone, positive or negative, raises the impact; negative weighs more |
| Relevance | the article falls into a sidebar category |
| Event | interest rates, war, attacks, sanctions, tariffs, bankruptcy, mergers, resignations, CPI, outage… |
| Market-moving source | FOMC decision, CPI, Employment Situation |
| Magnitude | ≥ $1bn / $10bn, moves ≥ 5% / 10% |
| Major actors | Trump, Xi, Putin, Fed, China, Nvidia… |
| Reductions | analysis and explainers ("Why…", "How…", live) ×0.75; no category (sport, celebrity) ×0.5 |

A story (the card) takes its **best article** plus a bonus for the number of independent publications reporting it.

| Score | Colour | Level |
|---|---|---|
| 1–3 | green | Normal, nearly irrelevant |
| 4–5 | yellow | Moderate |
| 6–7 | orange | Important |
| 8–10 | red, glowing outline | Critical |

In the UI:
- the badge on the card shows the score, level and tone (▲ positive, ● neutral, ▼ negative);
- the badge tooltip explains every point;
- each source under "Show N more reports" shows its own score;
- the **Impact** filter (All / 4+ / 6+ / 8+) and the **Most important first** sort order the list by importance.

After changing the rules, run `python -m app reclassify`.

## Tests

```bash
scripts/test.sh      # ruff + mypy + pytest (backend) and tsc + vitest (frontend)
```

Backend only: `cd backend && ../.venv/bin/pytest`. The tests do not touch the network: they use demo fixtures and a mocked HTTP transport.

The same checks run in CI on every push and pull request (`.github/workflows/ci.yml`).

## SQLite backup and restore

The database is `data/news.db` (SQLite in WAL mode).

```bash
scripts/backup.sh                 # consistent online backup into backups/, with integrity check (keeps the last 14)
scripts/backup.sh ~/Dropbox/news  # or into another directory

scripts/launchagent.sh stop       # stop the app before restoring
scripts/restore.sh backups/news-20261114-111258.db
scripts/launchagent.sh start
```

The backup uses the SQLite backup API, so it is safe while the app is running. Do **not** manually copy only `news.db` while the app is running, because the `-wal` file may contain data that has not been checkpointed yet.

For an automatic daily backup, add to `crontab -e`:

```
0 3 * * * /Users/<user>/pulse-wire/scripts/backup.sh >/dev/null 2>&1
```

## Autostart (LaunchAgent, macOS only)

On Linux, run `scripts/start.sh` from a systemd user unit or any process manager instead.

```bash
scripts/launchagent.sh install    # installs into ~/Library/LaunchAgents and starts now + at every login
scripts/launchagent.sh status
scripts/launchagent.sh stop       # stops until the next login
scripts/launchagent.sh start
scripts/launchagent.sh uninstall  # disables autostart
```

The LaunchAgent starts only the backend (which also serves the UI) on `127.0.0.1`. The browser is not a dependency of the service; open `http://127.0.0.1:8765` whenever you like. The template is [`launchd/com.local.pulse-wire.plist.template`](launchd/com.local.pulse-wire.plist.template).

## Logs and troubleshooting

- `logs/app.log` (rotated at 5 MB × 5): scans, per-source errors, retention.
- `logs/launchd.*.log`: stdout and stderr when running through the LaunchAgent.
- `GET /health`, `GET /api/sources` (status and the last 5 checks per source), `GET /api/runs`.
- Demo data is off by default (`NEWS_DEMO_ENABLED=false`). While it is off, any demo article left in the database is deleted automatically at startup and on every scan, so it never mixes with real news.
- Some sites (BLS, Council of the EU) block automated clients (403 or Cloudflare), but their links open normally in your browser. The app does not try to bypass these protections.

## Source access rules

- No Docker, Playwright, headless browsers, stealth mechanisms, automated login, or bypassing of paywalls or Cloudflare.
- **Reuters, Bloomberg, Financial Times and Washington Post** are accessed *only* through RSS, APIs or licensed syndication feeds that you configure. Their article pages are never downloaded: the rule is enforced in code (`access_policy.py`), including after redirects, and covered by tests.
- If an authorised endpoint or the editorial rights are missing, the source stays inactive. The automated tests (with local fixtures) still verify all functionality.
