"""Keep collection and tests offline, including dynamically imported models."""

import dotenv
import pytest
import requests


def pytest_configure(config):
    patch = pytest.MonkeyPatch()
    config._offline_patch = patch
    patch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)

    def reject_network(*args, **kwargs):
        raise AssertionError("Offline test attempted an unmocked HTTP request")

    patch.setattr(requests.sessions.Session, "request", reject_network)


def pytest_unconfigure(config):
    config._offline_patch.undo()
