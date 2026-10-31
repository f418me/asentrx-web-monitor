from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
import requests

import main as monitor

RELEASE = "2026-10-28T18:00:00+00:00"
INDEX = "https://www.federalreserve.gov/newsevents/pressreleases/2026-press-fomc.htm"
# Synthetic fixture matching the published Fed HTML structure, not a real release.
HTML = b"""<html><div id="article">
<p class="article__time">October 28, 2026</p>
<h3>Federal Reserve issues FOMC statement</h3>
<p>The Committee decided to maintain the target range for the federal funds rate
at 3-3/4 to 4 percent. Inflation remains elevated and employment is resilient.
The Committee continues to assess the incoming data and balance of risks.
Economic activity has continued to expand and household spending remains strong.
The Committee remains committed to returning inflation to its objective.</p>
<p>Voting for the monetary policy action were the participating members.</p>
</div></html>"""


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        value = datetime.fromisoformat(RELEASE)

        @classmethod
        def now(cls, tz=None):
            return cls.value.astimezone(tz or timezone.utc)

    monkeypatch.setattr(monitor, "datetime", Clock)
    return Clock


@pytest.fixture
def event(clock, monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "PROCESSED_URLS_FILE", str(tmp_path / "seen.txt"))
    return monitor.FomcEventMonitor(RELEASE, INDEX, set())


def test_direct_delivery_and_dedup(event, monkeypatch):
    fetch = Mock(return_value=HTML)
    send = Mock(return_value=True)
    monkeypatch.setattr(monitor, "get_page_content", fetch)
    monkeypatch.setattr(monitor, "send_data_to_webservice", send)
    event.poll("https://engine.test", "secret", "machine")
    event.poll("https://engine.test", "secret", "machine")
    fetch.assert_called_once()
    send.assert_called_once()
    assert send.call_args.args[0].content_id == "monetary20261028a"
    assert event.direct_url in monitor.load_processed_urls()


@pytest.mark.parametrize("html", [
    HTML.replace(b"October 28", b"October 27"),
    HTML.replace(b"FOMC statement", b"FOMC minutes"),
    HTML.replace(b"decided to maintain", b"may decide to maintain"),
    HTML.replace(b"Voting for", b"Partial text"),
    b"<html><h3>Federal Reserve issues FOMC statement</h3></html>",
])
def test_invalid_content_not_sent(event, monkeypatch, html):
    monkeypatch.setattr(monitor, "get_page_content", Mock(return_value=html))
    send = Mock()
    monkeypatch.setattr(monitor, "send_data_to_webservice", send)
    event.poll("https://engine.test", "secret", "machine")
    send.assert_not_called()
    assert not event.processed_urls


@pytest.mark.parametrize("stamp", ["2026-10-28T17:59:59+00:00",
                                  "2026-10-28T18:10:00+00:00",
                                  "2026-10-29T18:00:00+00:00"])
def test_outside_window_does_not_fetch(event, clock, monkeypatch, stamp):
    clock.value = datetime.fromisoformat(stamp)
    fetch = Mock()
    monkeypatch.setattr(monitor, "get_page_content", fetch)
    event.poll("https://engine.test", "secret", "machine")
    fetch.assert_not_called()


def test_failed_delivery_retries_without_marking_processed(event, monkeypatch):
    monkeypatch.setattr(monitor, "get_page_content", Mock(return_value=HTML))
    send = Mock(side_effect=[False, True])
    monkeypatch.setattr(monitor, "send_data_to_webservice", send)
    event.poll("https://engine.test", "secret", "machine")
    assert not event.processed_urls
    event.poll("https://engine.test", "secret", "machine")
    assert event.done
    assert send.call_count == 2


def test_index_fallback_validates_and_sends_alternate_url(event, monkeypatch):
    alternate = event.direct_url.replace("28a.htm", "28c.htm")
    index = f'<body><a href="{alternate}">Federal Reserve issues FOMC statement</a></body>'.encode()
    fetch = Mock(side_effect=[None, index, HTML])
    send = Mock(return_value=True)
    monkeypatch.setattr(monitor, "get_page_content", fetch)
    monkeypatch.setattr(monitor, "send_data_to_webservice", send)
    event.poll("https://engine.test", "secret", "machine")
    assert send.call_args.args[0].url == alternate


def test_fallback_throttled(event, monkeypatch):
    fetch = Mock(return_value=None)
    monkeypatch.setattr(monitor, "get_page_content", fetch)
    event.poll("https://engine.test", "secret", "machine")
    event.poll("https://engine.test", "secret", "machine")
    assert fetch.call_count == 3  # two direct requests, one index request


def test_reject_ambiguous_timezone():
    with pytest.raises(ValueError):
        monitor.FomcEventMonitor("2026-10-28T18:00:00", INDEX, set())
    event = monitor.FomcEventMonitor("2026-10-28T19:00:00+01:00", INDEX, set())
    assert event.release_at == datetime.fromisoformat(RELEASE)


@pytest.mark.parametrize("url", [
    "https://evil.test/newsevents/pressreleases/monetary20261028a.htm",
    "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm",
])
def test_wrong_source_or_url_date_rejected(event, url):
    assert event.valid_content(HTML, url) is None


def test_keyword_filter_preserved(event, monkeypatch):
    monkeypatch.setattr(monitor, "get_page_content", Mock(return_value=HTML))
    send = Mock()
    monkeypatch.setattr(monitor, "send_data_to_webservice", send)
    event.poll("https://engine.test", "secret", "machine", keyword="nonexistent")
    send.assert_not_called()


def test_existing_seen_url_is_not_replayed(event, monkeypatch):
    event.processed_urls.add(event.direct_url)
    fetch = Mock()
    monkeypatch.setattr(monitor, "get_page_content", fetch)
    event.poll("https://engine.test", "secret", "machine")
    fetch.assert_not_called()


def test_main_event_mode_bypasses_legacy_bootstrap(monkeypatch, clock, tmp_path):
    monkeypatch.setenv("FOMC_RELEASE_AT", RELEASE)
    monkeypatch.setenv("MONITOR_TARGET_KIND", "fomc")
    monkeypatch.setenv("MONITOR_BASE_URL", "https://www.federalreserve.gov")
    monkeypatch.setenv("WEBSERVICE_URL", "https://engine.test")
    monkeypatch.setenv("TRADE_ENGINE_AUTH_TOKEN", "test")
    monkeypatch.setenv("MONITOR_BOOTSTRAP_SKIP_EXISTING", "true")
    monkeypatch.setenv("MONITOR_KEYWORD", "")
    monkeypatch.setenv("MONITOR_TITLE_KEYWORD", "fomc statement")
    monkeypatch.setattr(monitor, "PROCESSED_URLS_FILE", str(tmp_path / "seen.txt"))
    fetch = Mock(return_value=HTML)
    send = Mock(return_value=True)
    monkeypatch.setattr(monitor, "get_page_content", fetch)
    monkeypatch.setattr(monitor, "send_data_to_webservice", send)
    monkeypatch.setattr(monitor.time, "sleep", Mock(side_effect=KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        monitor.main()
    send.assert_called_once()
    fetch.assert_called_once()


@pytest.mark.parametrize("status", [404, 429, 302])
def test_expected_http_failures_and_timing(monkeypatch, caplog, status):
    response = Mock(status_code=status, headers={"Age": "5"})
    get = Mock(return_value=response)
    monkeypatch.setattr(monitor.SOURCE_SESSION, "get", get)
    with caplog.at_level("INFO"):
        assert monitor.get_page_content(INDEX, timeout=(2, 3)) is None
    assert get.call_args.kwargs["allow_redirects"] is False
    assert "monitor_http" in caplog.text and "elapsed_ms" in caplog.text


def test_timeout(monkeypatch):
    monkeypatch.setattr(monitor.SOURCE_SESSION, "get", Mock(side_effect=requests.Timeout))
    assert monitor.get_page_content(INDEX) is None


@pytest.mark.parametrize("status,expected", [(200, True), (409, True), (503, False)])
def test_delivery_ack(monkeypatch, status, expected):
    response = Mock(status_code=status, text="response")
    if status == 503:
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
    monkeypatch.setattr(monitor.ENGINE_SESSION, "post", Mock(return_value=response))
    payload = monitor.WebMonitorPayload(ip="test", content="test")
    assert monitor.send_data_to_webservice(payload, "https://engine.test", "secret") is expected
