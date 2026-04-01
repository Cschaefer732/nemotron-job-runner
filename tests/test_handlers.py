from app.handlers import JobHandler, load_handlers


def test_job_handler_interface_exists():
    assert hasattr(JobHandler, "job_type")
    assert hasattr(JobHandler, "run")


def test_load_handlers_returns_dict():
    handlers = load_handlers()
    assert isinstance(handlers, dict)


def test_load_handlers_includes_smart_connections():
    handlers = load_handlers()
    assert "smart_connections_reembed" in handlers


def test_handler_registry_keyed_by_job_type():
    handlers = load_handlers()
    for key, handler in handlers.items():
        assert handler.job_type == key


def test_smart_connections_handler_returns_dict(tmp_path, monkeypatch):
    import app.handlers.smart_connections as sc_mod
    monkeypatch.setattr(sc_mod, "SENTINEL_FILE", tmp_path / "reembed_trigger")
    handler = sc_mod.SmartConnectionsReembed()
    result = handler.run("test-job-id", {})
    assert result["triggered"] is True
    assert (tmp_path / "reembed_trigger").exists()
