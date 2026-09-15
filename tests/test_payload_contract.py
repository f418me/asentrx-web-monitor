import importlib.util
import sys
import types
from pathlib import Path


def _load_engine_web_monitor_payload_model():
    engine_models_path = (
        Path(__file__).resolve().parents[2]
        / "asentrx-trade-decision-engine"
        / "app"
        / "models.py"
    )
    spec = importlib.util.spec_from_file_location("trade_engine_models", engine_models_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.WebMonitorPayload


def _load_monitor_module():
    if "dotenv" not in sys.modules:
        dotenv_module = types.ModuleType("dotenv")
        dotenv_module.load_dotenv = lambda: None
        sys.modules["dotenv"] = dotenv_module

    if "bs4" not in sys.modules:
        bs4_module = types.ModuleType("bs4")

        class DummyBeautifulSoup:
            def __init__(self, *args, **kwargs):
                pass

        bs4_module.BeautifulSoup = DummyBeautifulSoup
        sys.modules["bs4"] = bs4_module

    spec = importlib.util.spec_from_file_location(
        "web_monitor_main",
        Path(__file__).resolve().parents[1] / "main.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_monitor_web_monitor_payload_model():
    return _load_monitor_module().WebMonitorPayload


def test_monitor_payload_matches_trade_engine_contract():
    EngineWebMonitorPayload = _load_engine_web_monitor_payload_model()
    MonitorWebMonitorPayload = _load_monitor_web_monitor_payload_model()
    payload = MonitorWebMonitorPayload(
        ip="127.0.0.1",
        url="https://www.federalreserve.gov/newsevents/pressreleases/monetary20260429a.htm",
        content_id="monetary20260429a",
        content="Federal Reserve issues FOMC statement.",
    )

    payload_dict = payload.model_dump(by_alias=True)
    validated = EngineWebMonitorPayload.model_validate(payload_dict)

    assert validated.type == "web-monitor"
    assert validated.url == payload.url
    assert validated.content_id == "monetary20260429a"
    assert validated.content == payload.content
    assert validated.ip == payload.ip


def test_monitor_payload_uses_content_id_alias_expected_by_engine():
    MonitorWebMonitorPayload = _load_monitor_web_monitor_payload_model()
    payload = MonitorWebMonitorPayload(
        ip="127.0.0.1",
        url="https://www.bls.gov/news.release/empsit.htm",
        content_id="bls-empsit-august-2026-released-2026-09-04",
        content="Employment Situation News Release.",
    )

    payload_dict = payload.model_dump(by_alias=True)

    assert "content-id" in payload_dict
    assert "content_id" not in payload_dict


def test_send_data_to_webservice_uses_bearer_auth_header(monkeypatch):
    monitor_module = _load_monitor_module()
    payload = monitor_module.WebMonitorPayload(
        ip="127.0.0.1",
        url="https://www.federalreserve.gov/newsevents/pressreleases/monetary20260429a.htm",
        content_id="monetary20260429a",
        content="Federal Reserve issues FOMC statement.",
    )
    captured = {}

    class DummyResponse:
        status_code = 200
        text = "{}"

        def raise_for_status(self):
            return None

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr(monitor_module.requests, "post", fake_post)

    monitor_module.send_data_to_webservice(
        payload,
        "https://engine.example/notify/web-monitor",
        "secret-token",
    )

    assert captured["headers"] == {"Authorization": "Bearer secret-token"}
    assert captured["json"]["content-id"] == "monetary20260429a"


def test_resolve_monitor_instance_id_falls_back_to_fly_machine_id(monkeypatch):
    monitor_module = _load_monitor_module()
    monkeypatch.delenv("FLY_PUBLIC_IP", raising=False)
    monkeypatch.setenv("FLY_MACHINE_ID", "machine-123")

    assert monitor_module.resolve_monitor_instance_id() == "machine-123"


def test_extract_bls_employment_report_metadata_builds_stable_content_id(monkeypatch):
    monitor_module = _load_monitor_module()

    class FakeSoup:
        def __init__(self, html_content, parser):
            self.html_content = html_content
            self.parser = parser

        def get_text(self, separator="\n", strip=True):
            return (
                "Transmission of material in this news release is embargoed until "
                "USDL-26-1400 8:30 a.m. (ET) Friday, September 4, 2026\n"
                "THE EMPLOYMENT SITUATION - AUGUST 2026"
            )

    monkeypatch.setattr(monitor_module, "BeautifulSoup", FakeSoup)
    monkeypatch.setattr(
        monitor_module,
        "extract_article_content",
        lambda html: "Total nonfarm payroll employment changed little in August (+55,000), and the unemployment rate was 4.2 percent.",
    )

    metadata = monitor_module.extract_bls_employment_report_metadata(
        b"<html></html>",
        "https://www.bls.gov/news.release/empsit.htm",
    )

    assert metadata is not None
    assert metadata["title"] == "Employment Situation - August 2026"
    assert metadata["content_id"] == "bls-empsit-august-2026-released-2026-09-04"
    assert metadata["url"] == "https://www.bls.gov/news.release/empsit.htm"
    assert "Total nonfarm payroll employment changed little" in metadata["content"]


def test_extract_bls_cpi_report_metadata_builds_stable_content_id(monkeypatch):
    monitor_module = _load_monitor_module()

    class FakeSoup:
        def __init__(self, html_content, parser):
            self.html_content = html_content
            self.parser = parser

        def get_text(self, separator="\n", strip=True):
            return (
                "Transmission of material in this news release is embargoed until "
                "USDL-26-1500 8:30 a.m. (ET) Friday, September 11, 2026\n"
                "CONSUMER PRICE INDEX - AUGUST 2026"
            )

    monkeypatch.setattr(monitor_module, "BeautifulSoup", FakeSoup)
    monkeypatch.setattr(
        monitor_module,
        "extract_article_content",
        lambda html: "The Consumer Price Index for All Urban Consumers rose 0.2 percent in August. The index for all items less food and energy rose 0.2 percent.",
    )

    metadata = monitor_module.extract_bls_cpi_report_metadata(
        b"<html></html>",
        "https://www.bls.gov/news.release/cpi.htm",
    )

    assert metadata is not None
    assert metadata["title"] == "Consumer Price Index - August 2026"
    assert metadata["content_id"] == "bls-cpi-august-2026-released-2026-09-11"
    assert metadata["url"] == "https://www.bls.gov/news.release/cpi.htm"
    assert "Consumer Price Index for All Urban Consumers" in metadata["content"]
