"""
幂等处理：FastAPI 依赖。

用法：
    @router.post("/...")
    async def endpoint(
        payload: ...,
        idem: IdempotencyContext = Depends(require_idempotency),
    ):
        if idem.cached_response:
            return idem.cached_response
        ...
        idem.record(status_code=200, body=final_body)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from fastapi import Header, HTTPException

from ..domain.error_codes import ErrorCode, build_error
from ..infrastructure.idempotency_store import IdempotencyRecord, idempotency_store


@dataclass
class IdempotencyContext:
    key: Optional[str]
    existing: Optional[IdempotencyRecord] = None

    @property
    def cached_response(self) -> Optional[Dict[str, Any]]:
        if self.existing and self.existing.completed and self.existing.body is not None:
            return self.existing.body
        return None

    async def record(self, *, status_code: int, body: Dict[str, Any]) -> None:
        if not self.key:
            return
        await idempotency_store.complete(self.key, status_code=status_code, body=body)

    async def abort(self) -> None:
        if self.key:
            await idempotency_store.abort(self.key)


async def require_idempotency(
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> IdempotencyContext:
    if not idempotency_key:
        raise HTTPException(
            status_code=400,
            detail=build_error(
                ErrorCode.INPUT_VALIDATION_FAILED,
                "缺少 Idempotency-Key Header",
            )["error"],
        )

    record = await idempotency_store.lookup(idempotency_key)
    if record and not record.completed:
        # 冲突：同一幂等键有尚未完成的请求
        raise HTTPException(
            status_code=409,
            detail=build_error(
                ErrorCode.INPUT_IDEMPOTENCY_CONFLICT,
                "相同 Idempotency-Key 的请求正在处理中，请稍后重试",
                details={"idempotency_key": idempotency_key},
            )["error"],
        )

    if record and record.completed:
        return IdempotencyContext(key=idempotency_key, existing=record)

    if not await idempotency_store.begin(idempotency_key):
        raise HTTPException(
            status_code=409,
            detail=build_error(
                ErrorCode.INPUT_IDEMPOTENCY_CONFLICT,
                "相同 Idempotency-Key 的请求正在处理中，请稍后重试",
                details={"idempotency_key": idempotency_key},
            )["error"],
        )
    return IdempotencyContext(key=idempotency_key)


async def optional_idempotency(
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> IdempotencyContext:
    """对“可选幂等”的接口（例如读接口不需要幂等），有键就注册/命中，没有就无锁。"""
    if not idempotency_key:
        return IdempotencyContext(key=None)

    record = await idempotency_store.lookup(idempotency_key)
    if record and not record.completed:
        raise HTTPException(
            status_code=409,
            detail=build_error(
                ErrorCode.INPUT_IDEMPOTENCY_CONFLICT,
                "相同 Idempotency-Key 的请求正在处理中，请稍后重试",
                details={"idempotency_key": idempotency_key},
            )["error"],
        )
    if record and record.completed:
        return IdempotencyContext(key=idempotency_key, existing=record)
    if not await idempotency_store.begin(idempotency_key):
        raise HTTPException(
            status_code=409,
            detail=build_error(
                ErrorCode.INPUT_IDEMPOTENCY_CONFLICT,
                "相同 Idempotency-Key 的请求正在处理中，请稍后重试",
                details={"idempotency_key": idempotency_key},
            )["error"],
        )
    return IdempotencyContext(key=idempotency_key)
