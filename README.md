
## Overview

`asentrx-web-monitor` polls configured official macro-release pages and forwards matching content to the `asentrx-trade-decision-engine` at `/notify/web-monitor`.

The monitor supports three target modes:

* `fomc`: official Federal Reserve FOMC press-release index
* `nfp`: official BLS Employment Situation release page
* `cpi`: official BLS Consumer Price Index release page

The current Fly.io configuration uses the 2026 FOMC index:

* It watches `https://www.federalreserve.gov/newsevents/pressreleases/2026-press-fomc.htm`
* It filters for titles containing `fomc statement`
* It sends the extracted FOMC statement text to the trade engine when a new matching press-release link appears

## Fast FOMC Event Mode

Set `FOMC_RELEASE_AT` to the next official release timestamp with an explicit
offset. For example, the **past** October 28 decision was
`2026-10-28T14:00:00-04:00` (18:00 UTC / 19:00 Zurich). Do not reuse that date
for a future event. An empty value keeps the legacy index-polling mode.

In event mode the monitor first requests the expected official statement URL,
derived from the New York calendar date. On failure it checks the configured
index at most once every 30 seconds for alternate same-date statement links.
Both paths require the official HTTPS host, matching URL and displayed release
date, exact statement heading, a substantive body with a rate decision and a
voting paragraph. Redirects, placeholders, wrong dates and incomplete-looking
responses are rejected and retried. These are conservative structural checks,
not a guarantee that a publisher has finished editing a page.

Nothing is fetched or sent before the configured time or from ten minutes after
it onward. Late starts within that window can send the validated event even with
empty local state: event mode replaces the legacy bootstrap rule with the date
and time-window checks. After successful delivery, the URL is persisted and the
event is done. Timeouts and HTTP failures are retried next cycle; HTTP 409 counts
as already received by the engine. Engine HTTP 200 acknowledges receipt, not a
successful trade. Its own deduplication and confidence gates still apply.

Source and engine requests use separate reusable sessions; the authentication
header is only attached to engine requests. Fast source requests have a
2-second connect / 3-second read timeout, without automatic retries. Normal
production polling still sleeps the configured 3-8 seconds after each cycle,
in addition to request time. With ten instances, increasing frequency further
would also increase total load on the source site. No rate-limit bypass is used.

Structured INFO logs:

* `monitor_http`: UTC timestamp, instance, URL, stage, duration, HTTP status and cache headers.
* `monitor_parse`: validation/extraction duration per candidate.
* `monitor_release_detected`: direct/index route, content ID, UUID, cycle duration and time since the scheduled release.
* `monitor_delivery`: content ID, UUID, response status and duration including engine processing.

Elapsed time uses a monotonic clock. Time since the scheduled release is **not**
latency since actual publication, which the source does not expose precisely.
These events go to the monitor's stdout/Fly logs; they are not sent directly to
Logfire. Retain/export the logs to investigate future events beyond Fly's buffer.

Run offline tests: `poetry run pytest -q`. Set the next event time and matching
orchestrator schedule before deployment; this change does not deploy or start machines.

## General Features

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
