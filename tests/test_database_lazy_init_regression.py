import importlib


def test_close_does_not_create_database_when_never_initialized(monkeypatch):
    db_module = importlib.import_module("src.database.db_manager")
    monkeypatch.setattr(db_module, "_db_instance", None)

    class UnexpectedDatabaseManager:
        def __init__(self):
            raise AssertionError("shutdown must not initialize SQLite")

    monkeypatch.setattr(db_module, "DatabaseManager", UnexpectedDatabaseManager)
    db_module.close_db_if_initialized()
    assert db_module._db_instance is None


def test_compatibility_proxy_initializes_database_only_on_first_use(monkeypatch):
    db_module = importlib.import_module("src.database.db_manager")
    monkeypatch.setattr(db_module, "_db_instance", None)
    created = []

    class FakeDatabaseManager:
        marker = "ready"

        def __init__(self):
            created.append(self)

    monkeypatch.setattr(db_module, "DatabaseManager", FakeDatabaseManager)

    assert created == []
    assert db_module.db_manager.marker == "ready"
    assert len(created) == 1
    assert db_module.db_manager.marker == "ready"
    assert len(created) == 1
