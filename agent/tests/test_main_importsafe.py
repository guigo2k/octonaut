def test_import_is_safe_without_required_env_vars(monkeypatch):
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AGENT_CONFIG", raising=False)

    import agent.main  # noqa: F401 - must not raise despite missing env vars/network

    assert hasattr(agent.main, "main")
