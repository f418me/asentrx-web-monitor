from datetime import datetime, timezone
from pathlib import Path
import tomllib
from unittest.mock import Mock

from bs4 import BeautifulSoup
from dotenv import dotenv_values
import pytest
import requests

import main as monitor

RELEASE = "2026-11-25T13:30:00+00:00"
URL = "https://www.bea.gov/news/2026/personal-income-and-outlays-october-2026"
ENGINE = "https://engine.test/notify/web-monitor"
PRIOR_URL = URL.replace("october", "september")
PRIOR_HTML = (Path(__file__).parent / "fixtures/bea_pio_september_2026.html").read_bytes()
# The positive event fixture is explicitly synthetic, derived from observed BEA
# markup. Published September figures are not predictions for October.
HTML = (PRIOR_HTML.replace(b"september-2026", b"october-2026")
        .replace(b"September", b"October")
        .replace(b"EDT, Thursday, October 29, 2026", b"EST, Wednesday, November 25, 2026"))


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
    return monitor.PceEventMonitor(RELEASE, "2026-10", URL, set())


def test_observed_september_bea_fixture_parses_with_historical_configuration():
    historical = monitor.PceEventMonitor("2026-10-29T12:30:00Z", "2026-09", PRIOR_URL, set())
    content = historical.valid_content(PRIOR_HTML, PRIOR_URL)
    assert content is not None
    # The actual inline link boundary inserts a space before the period.
    assert "0.5 percent . Excluding food and energy" in content
    assert "3.6 percent . Excluding food and energy" in content
    assert "3.1 percent from one year ago" in content


def test_prior_release_with_upcoming_date_in_next_release_notice_is_rejected(event):
    assert b"November 25, 2026" in PRIOR_HTML
    assert b"Personal Income and Outlays, October 2026" in PRIOR_HTML
    assert event.valid_content(PRIOR_HTML, URL) is None


def test_direct_authenticated_delivery_and_restart_dedup(event, monkeypatch):
    get = Mock(return_value=Mock(status_code=200, content=HTML, headers={}))
    post = Mock(return_value=Mock(status_code=200, text='{"status":"received"}'))
    monkeypatch.setattr(monitor.SOURCE_SESSION, "get", get)
    monkeypatch.setattr(monitor.ENGINE_SESSION, "post", post)
    event.poll(ENGINE, "test-token", "machine")
    event.poll(ENGINE, "test-token", "machine")
    restarted = monitor.PceEventMonitor(RELEASE, "2026-10", URL, monitor.load_processed_urls())
    restarted.poll(ENGINE, "test-token", "machine")
    get.assert_called_once()
    assert get.call_args.args[0] == URL
    assert get.call_args.kwargs["allow_redirects"] is False
    assert get.call_args.kwargs["timeout"] == (2, 3)
    assert "Authorization" not in get.call_args.kwargs["headers"]
    post.assert_called_once()
    assert post.call_args.args[0] == ENGINE
    assert post.call_args.kwargs["headers"] == {"Authorization": "Bearer test-token"}
    payload = post.call_args.kwargs["json"]
    assert payload["source"] == "pce"
    assert payload["type"] == "web-monitor"
    assert payload["content-id"] == "bea-pce-2026-10"
    assert payload["url"] == URL
    assert "From the preceding month" in payload["content"]
    assert monitor.load_processed_urls() == {URL}


@pytest.mark.parametrize("stamp,expected", [
    ("2026-11-25T13:29:59.999999Z", False),
    ("2026-11-25T13:30:00Z", True),
    ("2026-11-25T13:34:59.999999Z", True),
    ("2026-11-25T13:35:00Z", False),
    ("2026-11-26T13:30:00Z", False),
])
def test_window_boundaries(event, clock, monkeypatch, stamp, expected):
    clock.value = datetime.fromisoformat(stamp)
    fetch = Mock(return_value=HTML)
    send = Mock(return_value=True)
    monkeypatch.setattr(monitor, "get_page_content", fetch)
    monkeypatch.setattr(monitor, "send_data_to_webservice", send)
    event.poll(ENGINE, "test-token", "machine")
    assert fetch.called is expected
    assert send.called is expected


@pytest.mark.parametrize("stage", ["fetch", "parse", "log"])
def test_expiry_during_cycle_prevents_send(event, clock, monkeypatch, stage):
    original_parse = event.valid_content

    def expire(result):
        clock.value = event.expires_at
        return result

    monkeypatch.setattr(monitor, "get_page_content", Mock(
        side_effect=lambda *a, **k: expire(HTML) if stage == "fetch" else HTML))
    if stage == "parse":
        monkeypatch.setattr(event, "valid_content", lambda *a: expire(original_parse(*a)))
    if stage == "log":
        monkeypatch.setattr(monitor, "timing_event", lambda name, **k:
                            expire(None) if name == "monitor_release_detected" else None)
    send = Mock()
    monkeypatch.setattr(monitor, "send_data_to_webservice", send)
    event.poll(ENGINE, "test-token", "machine")
    send.assert_not_called()
    assert not event.processed_urls


@pytest.mark.parametrize("old,new", [
    (b"Outlays, October 2026", b"Outlays, September 2026"),
    (b"Outlays, October 2026", b"Outlays, October 2025"),
    (b"Personal Income and Outlays, October 2026", b"Gross Domestic Product, Third Quarter 2026"),
    (b"November 25, 2026", b"November 24, 2026"),
    (b"November 25, 2026", b"November 25, 2025"),
    (b"November 25, 2026", b"November 99, 2026"),
    (b"8:30 a.m.", b"10:00 a.m."),
    (b"a.m. EST", b"a.m. EDT"),
    (b"field--name-field-release-date", b"missing-date"),
    (b"field--name-body", b"missing-body"),
    (b"for October increased", b"for September increased"),
    (b"From the preceding month", b"Historical monthly comparison"),
    (b"From the same month one year ago", b"Historical annual comparison"),
    (b"Excluding food and energy", b"Core figures pending"),
    (b"0.3 percent", b"not yet available"),
    (b'outlays-october-2026">', b'outlays-september-2026">'),
])
def test_wrong_or_incomplete_content_not_sent(event, monkeypatch, old, new, caplog):
    assert old in HTML
    monkeypatch.setattr(monitor, "get_page_content", Mock(return_value=HTML.replace(old, new)))
    send = Mock()
    monkeypatch.setattr(monitor, "send_data_to_webservice", send)
    event.poll(ENGINE, "test-token", "machine")
    send.assert_not_called()
    assert not event.processed_urls
    assert "PCE candidate rejected" in caplog.text


def test_matching_slug_heading_and_date_without_release_body_are_rejected(event):
    soup = BeautifulSoup(HTML, "html.parser")
    body = soup.select_one(".field--name-body")
    body.clear()
    body.append("Release pending. " * 100)
    assert event.valid_content(str(soup).encode(), URL) is None


@pytest.mark.parametrize("old,new", [
    (b" ", b"\n\t "),
    (b" ", b"&nbsp;"),
    (b"8:30 a.m. EST,", b"08:30 AM EST ,"),
    (b"8:30 a.m. EST,", b"8:30 <span>a. m.</span> EST,"),
    (b"Outlays, October", b"Outlays, <span>October</span>"),
    (b"index increased", b"index also increased"),
    (b"increased 0.3 percent", b"was unchanged"),
    (b"increased 0.3 percent", b"decreased less than 0.1 percent"),
    (b"release-body", b"new-layout-wrapper"),
])
def test_harmless_format_and_published_wording_variants(event, old, new):
    # Whitespace substitutions affect text nodes only, not HTML attributes.
    soup = BeautifulSoup(HTML, "html.parser")
    if old == b" ":
        for node in list(soup.find_all(string=True)):
            node.replace_with(str(node).replace(" ", "\n\t " if new != b"&nbsp;" else "\u00a0"))
        variant = str(soup).encode()
    else:
        variant = HTML.replace(old, new)
    assert event.valid_content(variant, URL) is not None


@pytest.mark.parametrize("url", [
    URL.replace("https:", "http:"), URL.replace("www.bea.gov", "evil.test"),
    PRIOR_URL, URL.replace("2026", "2025"), URL + "?preview=1",
    "https://www.bea.gov/news/2026/gross-domestic-product-third-quarter-2026",
])
def test_wrong_url_rejected_before_parsing(event, url):
    assert event.valid_content(HTML, url) is None
    with pytest.raises(ValueError, match="canonical"):
        monitor.PceEventMonitor(RELEASE, "2026-10", url, set())


@pytest.mark.parametrize("status", [404, 429, 302, 503])
def test_unavailable_page_retried_only_in_window(event, clock, monkeypatch, status):
    response = Mock(status_code=status, headers={})
    if status == 503:
        response.text = "unavailable"
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
    get = Mock(return_value=response)
    monkeypatch.setattr(monitor.SOURCE_SESSION, "get", get)
    send = Mock()
    monkeypatch.setattr(monitor, "send_data_to_webservice", send)
    event.poll(ENGINE, "test-token", "machine")
    event.poll(ENGINE, "test-token", "machine")
    clock.value = event.expires_at
    event.poll(ENGINE, "test-token", "machine")
    assert get.call_count == 2
    send.assert_not_called()
    assert not event.processed_urls


@pytest.mark.parametrize("error", [requests.Timeout, requests.ConnectionError])
def test_source_errors_do_not_mark_processed(event, monkeypatch, error):
    monkeypatch.setattr(monitor.SOURCE_SESSION, "get", Mock(side_effect=error))
    send = Mock()
    monkeypatch.setattr(monitor, "send_data_to_webservice", send)
    event.poll(ENGINE, "test-token", "machine")
    send.assert_not_called()
    assert not event.processed_urls


@pytest.mark.parametrize("status,body", [
    (200, '{"status":"received","analysis_result":{"type":"Failed"}}'),
    (200, '{"status":"received","analysis_result":{"type":"Irrelevant"}}'),
    (409, '{"detail":"Already processed"}'),
])
def test_received_analysis_failure_or_duplicate_is_not_retried(event, monkeypatch, status, body):
    monkeypatch.setattr(monitor, "get_page_content", Mock(return_value=HTML))
    post = Mock(return_value=Mock(status_code=status, text=body))
    monkeypatch.setattr(monitor.ENGINE_SESSION, "post", post)
    event.poll(ENGINE, "test-token", "machine")
    event.poll(ENGINE, "test-token", "machine")
    post.assert_called_once()
    assert event.done
    assert monitor.load_processed_urls() == {URL}


def test_failed_transport_retries_without_persisting_then_stops(event, monkeypatch):
    monkeypatch.setattr(monitor, "get_page_content", Mock(return_value=HTML))
    post = Mock(side_effect=[requests.Timeout, Mock(status_code=200, text="received")])
    monkeypatch.setattr(monitor.ENGINE_SESSION, "post", post)
    event.poll(ENGINE, "test-token", "machine")
    assert not event.processed_urls
    assert not monitor.load_processed_urls()
    event.poll(ENGINE, "test-token", "machine")
    assert event.done
    assert post.call_count == 2


@pytest.mark.parametrize("title,keyword", [("fomc", ""), ("", "not-in-release")])
def test_optional_filters_preserved(event, monkeypatch, title, keyword):
    monkeypatch.setattr(monitor, "get_page_content", Mock(return_value=HTML))
    send = Mock()
    monkeypatch.setattr(monitor, "send_data_to_webservice", send)
    event.poll(ENGINE, "test-token", "machine", title, keyword)
    send.assert_not_called()


def test_missing_auth_never_fetches_or_sends(event, monkeypatch):
    fetch = Mock()
    monkeypatch.setattr(monitor, "get_page_content", fetch)
    event.poll(ENGINE, "", "machine")
    fetch.assert_not_called()


@pytest.mark.parametrize("release,month", [
    ("", "2026-10"), ("2026-11-25T13:30:00", "2026-10"),
    (RELEASE, ""), (RELEASE, "2026-13"), (RELEASE, "2026-1"),
])
def test_invalid_configuration_is_rejected(release, month):
    with pytest.raises(ValueError):
        monitor.PceEventMonitor(release, month, URL, set())


def test_zurich_offset_matches_utc():
    event = monitor.PceEventMonitor("2026-11-25T14:30:00+01:00", "2026-10", URL, set())
    assert event.release_at == datetime.fromisoformat(RELEASE)
    assert event.expires_at == datetime.fromisoformat("2026-11-25T13:35:00Z")


@pytest.fixture
def configured_main(event, monkeypatch):
    values = tomllib.loads((Path(__file__).parents[1] / "fly.toml").read_text())["env"]
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("WEBSERVICE_URL", ENGINE)
    monkeypatch.setenv("TRADE_ENGINE_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(monitor.time, "sleep", Mock(side_effect=KeyboardInterrupt))
    return values


def test_fly_and_example_config_align_with_contract(configured_main):
    example = dotenv_values(Path(__file__).parents[1] / ".env-example")
    for key in ("MONITOR_TARGET_KIND", "PCE_RELEASE_AT", "PCE_REFERENCE_MONTH",
                "MONITOR_BASE_URL", "MONITOR_MAIN_PAGE_PATH", "MONITOR_TITLE_KEYWORD",
                "MONITOR_MODE", "MONITOR_PROD_MIN_SECONDS", "MONITOR_PROD_MAX_SECONDS"):
        assert example[key] == configured_main[key]
    assert configured_main["PCE_RELEASE_AT"] == RELEASE
    assert configured_main["PCE_REFERENCE_MONTH"] == "2026-10"
    assert configured_main["MONITOR_BASE_URL"] + configured_main["MONITOR_MAIN_PAGE_PATH"] == URL


def test_main_pce_bypasses_legacy_bootstrap(configured_main, monkeypatch):
    fetch = Mock(return_value=HTML)
    send = Mock(return_value=True)
    monkeypatch.setattr(monitor, "get_page_content", fetch)
    monkeypatch.setattr(monitor, "send_data_to_webservice", send)
    with pytest.raises(KeyboardInterrupt):
        monitor.main()
    fetch.assert_called_once()
    send.assert_called_once()
    assert send.call_args.args[0].source == "pce"


@pytest.mark.parametrize("key", ["PCE_RELEASE_AT", "PCE_REFERENCE_MONTH", "TRADE_ENGINE_AUTH_TOKEN"])
def test_main_pce_missing_config_has_no_legacy_fallback(configured_main, monkeypatch, key):
    monkeypatch.delenv(key)
    fetch = Mock()
    monkeypatch.setattr(monitor, "get_page_content", fetch)
    monitor.main()
    fetch.assert_not_called()
