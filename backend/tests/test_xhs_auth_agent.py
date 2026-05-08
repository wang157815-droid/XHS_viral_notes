"""Phase 2-B: XhsAuthAgent 单元测试。

不启动完整 orchestrator，仅构造 AgentContext + mock task_repository / 注入隔离 store。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import pytest

from backend.app.application.agents import (
    AgentContext,
    XhsAuthAgent,
    XhsAuthRequiredError,
)
from backend.app.domain.error_codes import ErrorCode
from backend.app.domain.task_context import TaskContext


@dataclass
class _FakeRecord:
    owner_user_id: Optional[str] = None


@pytest.fixture()
def patch_repository(monkeypatch):
    """伪造 task_repository.get(task_id) → owner_user_id."""

    repo = {"current": _FakeRecord(owner_user_id="u_alice")}

    def fake_get(task_id: str):
        return repo["current"]

    from backend.app.application.agents import xhs_auth_agent as mod

    monkeypatch.setattr(mod.task_repository, "get", fake_get, raising=True)
    return repo


@pytest.fixture()
def patch_repository_no_owner(monkeypatch):
    from backend.app.application.agents import xhs_auth_agent as mod

    monkeypatch.setattr(mod.task_repository, "get", lambda _t: _FakeRecord(owner_user_id=None))


def _ctx(task_id: str = "t1") -> AgentContext:
    return AgentContext(task_id=task_id, task_context=TaskContext(task_id=task_id))


@pytest.mark.asyncio
async def test_unbound_raises_required_error(patch_repository, monkeypatch):
    """resolver 找不到 cookie → 抛 XhsAuthRequiredError 且 code 为 AUTH_XHS_NOT_BOUND。"""
    from backend.app.services.xhs_auth import credential_resolver as r_mod

    class FakeResolver:
        def resolve(self, _owner):
            from backend.app.services.xhs_auth.credential_resolver import (
                ResolvedCookie,
            )

            return ResolvedCookie(cookies_str="", source="not_found")

    monkeypatch.setattr(r_mod, "_default_resolver", FakeResolver(), raising=False)

    agent = XhsAuthAgent()
    with pytest.raises(XhsAuthRequiredError) as exc_info:
        await agent.run(_ctx())
    assert exc_info.value.code == ErrorCode.AUTH_XHS_NOT_BOUND.value
    assert "u_alice" in (exc_info.value.details or {}).get("redmuse_user_id", "")


@pytest.mark.asyncio
async def test_active_credential_passes(patch_repository, monkeypatch):
    from backend.app.services.xhs_auth import credential_resolver as r_mod
    from backend.app.services.xhs_auth import credential_store as s_mod
    from backend.app.services.xhs_auth.credential_resolver import ResolvedCookie

    class FakeResolver:
        def resolve(self, _owner):
            return ResolvedCookie(
                cookies_str="a1=ok",
                source="credential",
                cookies_path="datas/users/xhs_a/cookies.json",
                redmuse_user_id="u_alice",
                xhs_user_id="x_a",
            )

    fake_store = s_mod.XhsCredentialStore(store_file=str(_tmp(monkeypatch)))
    fake_store.upsert(
        redmuse_user_id="u_alice",
        cookies_path="datas/users/xhs_a/cookies.json",
        xhs_user_id="x_a",
        status="active",
    )

    monkeypatch.setattr(r_mod, "_default_resolver", FakeResolver(), raising=False)
    monkeypatch.setattr(s_mod, "_default_store", fake_store, raising=False)

    agent = XhsAuthAgent()
    result = await agent.run(_ctx())
    assert result.ok is True
    assert result.output["status"] == "ready"


@pytest.mark.asyncio
async def test_expired_credential_raises(patch_repository, monkeypatch):
    """resolver 找到 cookie 但 store 中状态 expired → 仍要求重新授权。"""
    from backend.app.services.xhs_auth import credential_resolver as r_mod
    from backend.app.services.xhs_auth import credential_store as s_mod
    from backend.app.services.xhs_auth.credential_resolver import ResolvedCookie

    class FakeResolver:
        def resolve(self, _owner):
            return ResolvedCookie(
                cookies_str="a1=stale",
                source="credential",
                cookies_path="datas/users/xhs_a/cookies.json",
                redmuse_user_id="u_alice",
                xhs_user_id="x_a",
            )

    fake_store = s_mod.XhsCredentialStore(store_file=str(_tmp(monkeypatch)))
    fake_store.upsert(
        redmuse_user_id="u_alice",
        cookies_path="datas/users/xhs_a/cookies.json",
        status="expired",
        status_message="测试用 expired",
    )

    monkeypatch.setattr(r_mod, "_default_resolver", FakeResolver(), raising=False)
    monkeypatch.setattr(s_mod, "_default_store", fake_store, raising=False)

    agent = XhsAuthAgent()
    with pytest.raises(XhsAuthRequiredError) as exc_info:
        await agent.run(_ctx())
    assert exc_info.value.code == ErrorCode.AUTH_XHS_NOT_BOUND.value
    assert exc_info.value.details["credential_status"] == "expired"


@pytest.mark.asyncio
async def test_no_owner_skips_check(patch_repository_no_owner, monkeypatch):
    """没有 owner_user_id 的旧任务不阻断（让 CrawlerAgent 自行降级 stub）。"""
    from backend.app.services.xhs_auth import credential_resolver as r_mod

    class _NeverCalled:
        def resolve(self, _owner):  # pragma: no cover - 不应被调用
            raise AssertionError("没有 owner 时不应调用 resolver")

    monkeypatch.setattr(r_mod, "_default_resolver", _NeverCalled(), raising=False)

    agent = XhsAuthAgent()
    result = await agent.run(_ctx())
    assert result.ok is True
    assert result.output["status"] == "skipped"


@pytest.mark.asyncio
async def test_legacy_source_not_blocked_by_store_status(patch_repository, monkeypatch):
    """resolver source=legacy_username（旧 XHS user_id）时不应去查 store 的 status，
    避免误判 expired。"""
    from backend.app.services.xhs_auth import credential_resolver as r_mod
    from backend.app.services.xhs_auth import credential_store as s_mod
    from backend.app.services.xhs_auth.credential_resolver import ResolvedCookie

    class FakeResolver:
        def resolve(self, _owner):
            return ResolvedCookie(
                cookies_str="a1=legacy",
                source="legacy_username",
                cookies_path="datas/users/xhs_legacy/cookies.json",
            )

    # store 中故意写一条 expired 但属于另一个用户，避免被串到
    fake_store = s_mod.XhsCredentialStore(store_file=str(_tmp(monkeypatch)))
    fake_store.upsert(
        redmuse_user_id="u_other",
        cookies_path="datas/users/xhs_other/cookies.json",
        status="expired",
    )
    monkeypatch.setattr(r_mod, "_default_resolver", FakeResolver(), raising=False)
    monkeypatch.setattr(s_mod, "_default_store", fake_store, raising=False)

    agent = XhsAuthAgent()
    result = await agent.run(_ctx())
    assert result.ok is True
    assert result.output["source"] == "legacy_username"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _tmp(monkeypatch) -> str:
    """生成一次性临时 json 路径。"""
    import tempfile

    f = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    f.close()
    return f.name
