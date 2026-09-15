
## Overview

`asentrx-web-monitor` polls configured official macro-release pages and forwards matching content to the `asentrx-trade-decision-engine` at `/notify/web-monitor`.

The monitor supports three target modes:

* `fomc`: official Federal Reserve FOMC press-release index
* `nfp`: official BLS Employment Situation release page
* `cpi`: official BLS Consumer Price Index release page

The current Fly.io configuration is tuned for the September 16, 2026 FOMC decision:

* It watches `https://www.federalreserve.gov/newsevents/pressreleases/2026-press-fomc.htm`
* It filters for titles containing `fomc statement`
* It sends the extracted FOMC statement text to the trade engine when a new matching press-release link appears

## Features

* **Macro-event monitoring**: Supports FOMC, NFP, and CPI targets.
* **Robust fallback extraction**: If the old `Recent Developments` homepage structure is missing, the monitor falls back to generic press-release link extraction.
* **Cold-start protection**: Can prevent replaying old releases when a machine starts with empty local state.
* **Structured JSON payload**: Sends the exact payload shape expected by the trade-decision engine.
* **Authenticated delivery**: Adds a bearer token to each trade-engine notification.
* **Configurable polling**: Supports normal interval mode and randomized production polling.
* **HTTPS support**: Designed to send payloads to HTTPS endpoints.
* **Poetry-based workflow**: Uses Poetry for dependency management and local test execution.

## Payload Contract

The monitor sends a `web-monitor` payload with the following fields:

```json
{
  "uuid": "generated-uuid",
  "type": "web-monitor",
  "url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260429a.htm",
  "content-id": "monetary20260429a",
  "content": "Full extracted article text...",
  "ip": "public-ip-of-the-monitor"
}
```

This shape is contract-tested against the `WebMonitorPayload` model in the trade-decision engine.

## Important Environment Variables

* `WEBSERVICE_URL` – full destination URL of the trade-decision engine endpoint.
* `TRADE_ENGINE_AUTH_TOKEN` – bearer token shared with the trade-decision engine's `NOTIFY_AUTH_TOKEN`.
* `MONITOR_TARGET_KIND` – `fomc`, `nfp`, or `cpi`.
* `MONITOR_BASE_URL` – base website URL, currently `https://www.federalreserve.gov` for FOMC.
* `MONITOR_MAIN_PAGE_PATH` – monitored page path, currently `/newsevents/pressreleases/2026-press-fomc.htm`.
* `MONITOR_TITLE_KEYWORD` – title substring filter used before sending; current value: `fomc statement`.
* `MONITOR_MODE` – set to `production` for faster checks, otherwise `normal`.
* `MONITOR_INTERVAL_SECONDS` – interval in seconds when in normal mode.
* `MONITOR_PROD_MIN_SECONDS` and `MONITOR_PROD_MAX_SECONDS` – random interval range used in production mode.
* `MONITOR_RECENT_ARTICLES_COUNT` – number of most recent extracted links to inspect each cycle.
* `MONITOR_BOOTSTRAP_SKIP_EXISTING` – when `true`, a cold start with empty state marks currently visible items as already seen and only sends genuinely new items that appear later.

## Cold Start Behavior

On a fresh machine with no `processed_article_urls.txt`, the monitor would normally treat the current visible release as new. To prevent replaying an old release on restart, `MONITOR_BOOTSTRAP_SKIP_EXISTING=true` enables a bootstrap mode:

* on the first successful scrape, currently visible matching links are marked as processed
* nothing is sent downstream during that bootstrap cycle
* only newly appearing links after that point are forwarded to the trade engine

For event-day deployments after the official release time, set `MONITOR_BOOTSTRAP_SKIP_EXISTING=false`; otherwise the just-published release can be marked as seen without being sent.

## Multi-Machine Note

Running multiple `asentrx-web-monitor` machines is fine if the goal is fast detection from different IPs.

However, the trade-decision engine currently deduplicates incoming `web-monitor` payloads in memory by URL. That means:

* multiple monitor machines are acceptable
* the trade-decision engine should ideally run on a single active machine if you want strict first-writer-wins behavior

## Healthy Logs

In the current FOMC configuration, these log patterns are expected and healthy:

* `Monitoring target kind: 'fomc'.`
* `Webpage monitoring: Base URL='https://www.federalreserve.gov', Main Page='https://www.federalreserve.gov/newsevents/pressreleases/2026-press-fomc.htm'.`
* `Filtering new articles by TITLE keyword: 'fomc statement'.`
* `Generic fallback extracted ... press-release links.`
* `Bootstrap protection marked currently visible articles as processed.`
* `No new matching articles found in this cycle.`

## Testing

Run the payload contract test:

```bash
poetry run pytest tests/test_payload_contract.py -q
```

This verifies that the monitor's outgoing JSON still matches the trade-decision engine's `WebMonitorPayload` schema.
