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


def _load_monitor_web_monitor_payload_model():
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
    return module.WebMonitorPayload


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
        url="https://www.federalreserve.gov/newsevents/pressreleases/monetary20260429a.htm",
        content_id="monetary20260429a",
        content="Federal Reserve issues FOMC statement.",
    )

    payload_dict = payload.model_dump(by_alias=True)

    assert "content-id" in payload_dict
    assert "content_id" not in payload_dict
