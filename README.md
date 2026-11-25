
## Overview

`asentrx-web-monitor` polls configured official macro-release pages and forwards matching content to the `asentrx-trade-decision-engine` at `/notify/web-monitor`.

The monitor supports four target modes:

* `fomc`: official Federal Reserve FOMC press-release index
* `nfp`: official BLS Employment Situation release page
* `cpi`: official BLS Consumer Price Index release page
* `pce`: one explicitly scheduled BEA Personal Income and Outlays release

The current `fly.toml` and `.env-example` target **Personal Income and Outlays,
October 2026**, scheduled for **November 25, 2026, 14:30 Europe/Zurich
(13:30 UTC / 08:30 EST)**. The existing engine endpoint is unchanged. This is
configuration only; no deployment or machine scheduling is performed here.

## Bounded PCE Event Mode

Required settings for the approved event:

```dotenv
MONITOR_TARGET_KIND=pce
PCE_RELEASE_AT=2026-11-25T13:30:00+00:00
PCE_REFERENCE_MONTH=2026-10
MONITOR_BASE_URL=https://www.bea.gov
MONITOR_MAIN_PAGE_PATH=/news/2026/personal-income-and-outlays-october-2026
MONITOR_TITLE_KEYWORD=personal income and outlays
MONITOR_KEYWORD=
```

PCE fetches and initiates delivery only in **[13:30:00, 13:35:00) UTC** on
November 25. A fresh clock check immediately before delivery prevents a slow
fetch or parse from sending after expiry. There is no late-start replay after
13:35, no rolling-page/index fallback, and no attempt to discover GDP releases.
Missing event settings or a target URL inconsistent with the month fail closed.
The process remains idle outside the window; stopping machines is the
orchestrator's responsibility. Both configured examples use 3-8 second polling
plus request time. Source requests use the existing session and 2s connect / 3s
read timeouts; 404, 429, redirects and transport failures retry only in-window.

Validation requires the exact canonical HTTPS BEA URL, a matching report heading
inside the article, and the displayed embargo timestamp matching the configured
release instant. The article body must contain substantive personal income and
consumption text plus both monthly and annual headline/core PCE price changes
for the expected month. A matching slug/title, an upcoming-release notice, or
an old page is insufficient. Optional canonical metadata must also match.
Whitespace, inline links/spans, nonbreaking spaces, common time formatting,
"also increased", "less than", and unchanged-price wording are tolerated.
The parser uses BeautifulSoup and observed BEA Drupal fields, not a new parser
dependency. Rejected candidates emit a warning and remain eligible for retry.
These checks are conservative, not proof that BEA has finished editing a page.

The observed markup was checked against BEA's actual
[September 2026 release](https://www.bea.gov/news/2026/personal-income-and-outlays-september-2026),
retrieved November 24. A sanitized excerpt is retained in
`tests/fixtures/bea_pio_september_2026.html`, preserving published dates, figures,
field wrappers and inline link boundaries. It passes with the historical event
configuration and is rejected for the October event, despite its next-release
notice naming October. Other event fixtures are explicitly synthetic derivatives,
not actual October release data. Future changes to BEA's wording or DOM may still
cause a safe rejection and missed event; inspect `PCE candidate rejected` logs.

PCE delivery contract at the existing `/notify/web-monitor` endpoint:

```json
{
  "uuid": "generated-uuid",
  "type": "web-monitor",
  "source": "pce",
  "url": "https://www.bea.gov/news/2026/personal-income-and-outlays-october-2026",
  "content-id": "bea-pce-2026-10",
  "content": "Validated heading, release timestamp and article body...",
  "ip": "monitor-instance-id"
}
```

The existing `TRADE_ENGINE_AUTH_TOKEN` is sent only to the engine as a bearer
token, never to BEA. No credentials belong in `fly.toml`. HTTP success acknowledges
receipt, not a trade: **a received analysis failure or abstention is not retried**.
HTTP 409 is also terminal. Only acknowledged deliveries persist the canonical
URL to local deduplication state; transport/HTTP failures remain retryable while
the window is open. A timeout can be ambiguous, so the engine must deduplicate
by canonical URL regardless of payload ID. Local state is not a distributed
exactly-once guarantee and may be lost on redeploy.

The engine independently checks the same window before and after analysis.
An in-flight delivery can arrive or finish outside the window and be rejected;
the monitor does not extend its deadline to compensate. Baselines, confidence
gates, analysis and trading remain engine responsibilities. Bootstrap skipping
does not apply to this targeted event, so a valid release can be sent on a fresh
instance during the window.

To return to FOMC, select `MONITOR_TARGET_KIND=fomc`, restore base URL
`https://www.federalreserve.gov`, path
`/newsevents/pressreleases/2026-press-fomc.htm`, title filter `fomc statement`,
and set `FOMC_RELEASE_AT` for the next event (or leave it blank for legacy index
polling). PCE settings are ignored in FOMC mode. FOMC validation and its existing
ten-minute event window are unchanged.

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

* **Macro-event monitoring**: Supports FOMC, NFP, CPI, and bounded PCE targets.
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
The optional `source` field accepts `fed`, `cpi`, `nfp`, or `pce`. Legacy sends
omit it; PCE always sends `source="pce"` with the canonical URL and ID above.

## Important Environment Variables

* `WEBSERVICE_URL` – full destination URL of the trade-decision engine endpoint.
* `TRADE_ENGINE_AUTH_TOKEN` – bearer token shared with the trade-decision engine's `NOTIFY_AUTH_TOKEN`.
* `MONITOR_TARGET_KIND` – `fomc`, `nfp`, `cpi`, or `pce`.
* `PCE_RELEASE_AT` – required explicit-offset release instant in PCE mode; the window expires five minutes later.
* `PCE_REFERENCE_MONTH` – required `YYYY-MM` report month in PCE mode.
* `MONITOR_BASE_URL` – base website URL, currently `https://www.bea.gov`.
* `MONITOR_MAIN_PAGE_PATH` – monitored page path, currently `/news/2026/personal-income-and-outlays-october-2026`.
* `MONITOR_TITLE_KEYWORD` – title substring filter used before sending; current value: `personal income and outlays`.
* `MONITOR_MODE` – set to `production` for faster checks, otherwise `normal`.
* `MONITOR_INTERVAL_SECONDS` – interval in seconds when in normal mode.
* `MONITOR_PROD_MIN_SECONDS` and `MONITOR_PROD_MAX_SECONDS` – random interval range used in production mode.
* `MONITOR_RECENT_ARTICLES_COUNT` – number of most recent extracted links to inspect each cycle.
* `MONITOR_BOOTSTRAP_SKIP_EXISTING` – when `true`, a cold start with empty state marks currently visible items as already seen and only sends genuinely new items that appear later.

## Cold Start Behavior

In legacy modes, on a fresh machine with no `processed_article_urls.txt`, the monitor would normally treat the current visible release as new. To prevent replaying an old release on restart, `MONITOR_BOOTSTRAP_SKIP_EXISTING=true` enables a bootstrap mode:

* on the first successful scrape, currently visible matching links are marked as processed
* nothing is sent downstream during that bootstrap cycle
* only newly appearing links after that point are forwarded to the trade engine

For legacy event-day deployments after the official release time, set `MONITOR_BOOTSTRAP_SKIP_EXISTING=false`; otherwise the just-published release can be marked as seen without being sent. Targeted PCE and fast FOMC event modes bypass this rule and enforce their event windows instead.

## Multi-Machine Note

Running multiple `asentrx-web-monitor` machines is fine if the goal is fast detection from different IPs.

However, the trade-decision engine currently deduplicates incoming `web-monitor` payloads in memory by URL. That means:

* multiple monitor machines are acceptable
* the trade-decision engine should ideally run on a single active machine if you want strict first-writer-wins behavior

## Healthy Logs

In PCE mode, expect `PCE event mode: release=... expires=...`, `monitor_http`
with stage `pce_direct`, and `monitor_parse` with source `pce`. A validated
release produces `monitor_release_detected` and `monitor_delivery`. Out-of-window
polls and already-acknowledged events do not fetch or send.

When configured for legacy FOMC, these log patterns are expected and healthy:

* `Monitoring target kind: 'fomc'.`
* `Webpage monitoring: Base URL='https://www.federalreserve.gov', Main Page='https://www.federalreserve.gov/newsevents/pressreleases/2026-press-fomc.htm'.`
* `Filtering new articles by TITLE keyword: 'fomc statement'.`
* `Generic fallback extracted ... press-release links.`
* `Bootstrap protection marked currently visible articles as processed.`
* `No new matching articles found in this cycle.`

## Testing

Run the complete offline suite:

```bash
poetry run pytest -q
```

Tests block unmocked HTTP and disable dotenv loading before module collection,
so no local secret values are loaded. They cover the observed BEA fixture,
formatting variations, stale/month-mismatched/GDP/placeholder rejection, exact
window boundaries, expiry during processing, authentication isolation, receipt
versus transport failures, persisted deduplication, configuration parity, and
FOMC regression behavior. Payload tests import only the sibling engine's model
and verify its optional `source` enum and `content-id` alias; they do not start
the engine or call its live endpoint.
