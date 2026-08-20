import importlib


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
