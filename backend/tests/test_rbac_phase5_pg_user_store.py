from __future__ import annotations

from backend.app.infrastructure.db.schema import business_schema_sql
from backend.app.services.redmuse_auth import user_store as user_store_mod
from backend.app.services.redmuse_auth.user_store import RedMuseUserStore


def test_phase5_schema_contains_redmuse_users_and_project_reservation():
    sql = business_schema_sql()
    assert "CREATE TABLE IF NOT EXISTS redmuse_users" in sql
    assert "CREATE TABLE IF NOT EXISTS redmuse_projects" in sql
    assert "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS project_id" in sql
    assert "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS project_id" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_projects_owner" in sql


def test_json_user_store_accepts_phase5_roles(tmp_path):
    store = RedMuseUserStore(store_file=str(tmp_path / "users.json"))
    analyst = store.create_user(username="analyst", password="pw1234", role="analyst")
    viewer = store.create_user(username="viewer", password="pw1234", role="viewer")
    assert analyst.role == "analyst"
    assert viewer.role == "viewer"


def test_user_store_backend_flag_selects_pg(monkeypatch):
    class FakePgRedMuseUserStore:
        pass

    monkeypatch.setenv("REDMUSE_USER_STORE_BACKEND", "pg")
    monkeypatch.setattr(user_store_mod, "_default_store", None, raising=False)
    import backend.app.infrastructure.repository.pg_user_store as pg_mod

    monkeypatch.setattr(pg_mod, "PgRedMuseUserStore", FakePgRedMuseUserStore)
    store = user_store_mod.get_user_store()
    assert isinstance(store, FakePgRedMuseUserStore)


def test_user_store_backend_flag_defaults_to_json(monkeypatch):
    monkeypatch.delenv("REDMUSE_USER_STORE_BACKEND", raising=False)
    monkeypatch.setattr(user_store_mod, "_default_store", None, raising=False)
    store = user_store_mod.get_user_store()
    assert isinstance(store, RedMuseUserStore)
