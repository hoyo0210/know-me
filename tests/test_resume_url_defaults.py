import importlib
from unittest.mock import patch

import pytest


@pytest.fixture(scope="module", autouse=True)
def _isolate_dotenv_from_parent_workspace():
    """Parent workspace `.env` must not re-inject personal resume URL during app import/reload."""
    with patch("dotenv.load_dotenv", lambda *args, **kwargs: False):
        yield


def test_resume_browser_url_default_is_empty(monkeypatch):
    monkeypatch.delenv("KNOW_ME_RESUME_BROWSER_URL", raising=False)
    import know_me.api.app as app

    importlib.reload(app)
    url = app._resume_browser_url()
    assert url == ""
    assert "lihaoxu" not in url.lower()


def test_resume_browser_url_respects_env(monkeypatch):
    monkeypatch.setenv("KNOW_ME_RESUME_BROWSER_URL", "https://example.com/resume")
    import know_me.api.app as app

    importlib.reload(app)
    assert app._resume_browser_url() == "https://example.com/resume"
