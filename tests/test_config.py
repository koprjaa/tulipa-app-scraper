"""Unit tests for Settings — defaults, env overrides, full_url composition."""


from tulipa_app_scraper.infrastructure.config import Settings


def test_defaults_have_required_fields():
    s = Settings()
    assert s.base_url.startswith("https://")
    assert s.endpoint.startswith("/")
    assert s.username
    assert s.password
    assert s.request_timeout > 0
    assert len(s.main_groups) > 0
    assert len(s.known_categories) > 0
    assert len(s.action_subgroups) == 36  # GUID format
    assert s.session_timeout_minutes > 0


def test_full_url_composition():
    s = Settings(base_url="https://host", endpoint="/api")
    assert s.full_url == "https://host/api"


def test_session_file_is_under_data_dir():
    s = Settings()
    assert str(s.data_dir) in str(s.session_file)


def test_from_env_uses_defaults_when_env_missing(monkeypatch):
    monkeypatch.delenv("HELIOS_URL", raising=False)
    monkeypatch.delenv("HELIOS_USERNAME", raising=False)
    monkeypatch.delenv("HELIOS_PASSWORD", raising=False)
    s = Settings.from_env()
    defaults = Settings()
    assert s.base_url == defaults.base_url
    assert s.username == defaults.username
    assert s.password == defaults.password


def test_from_env_overrides(monkeypatch):
    monkeypatch.setenv("HELIOS_URL", "https://custom.example")
    monkeypatch.setenv("HELIOS_USERNAME", "alice")
    monkeypatch.setenv("HELIOS_PASSWORD", "secret")
    s = Settings.from_env()
    assert s.base_url == "https://custom.example"
    assert s.username == "alice"
    assert s.password == "secret"
