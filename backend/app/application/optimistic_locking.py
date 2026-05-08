"""
If-Match 乐观锁辅助。

写模块接口必须携带 If-Match: <module_version>。
不带或不匹配：抛 MODULE_VERSION_MISMATCH。
"""

from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException

from ..domain.error_codes import ErrorCode, build_error


class ModuleVersionGuard:
    """由调用方从 CanvasSchema 中取到 module.version，与 Header 比较。"""

    def __init__(self, if_match: Optional[str]) -> None:
        self._if_match = if_match

    @property
    def provided(self) -> Optional[int]:
        if self._if_match is None:
            return None
        try:
            return int(self._if_match)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail=build_error(
                    ErrorCode.INPUT_VALIDATION_FAILED,
                    "If-Match 必须是整数 module_version",
                )["error"],
            )

    def ensure_matches(self, current_version: int, *, module_id: str) -> None:
        if self._if_match is None:
            raise HTTPException(
                status_code=400,
                detail=build_error(
                    ErrorCode.INPUT_VALIDATION_FAILED,
                    "模块写操作必须携带 If-Match Header",
                    details={"module_id": module_id, "expected_current_version": current_version},
                )["error"],
            )
        if self.provided != current_version:
            raise HTTPException(
                status_code=409,
                detail=build_error(
                    ErrorCode.INPUT_MODULE_VERSION_MISMATCH,
                    "模块版本不匹配，其他会话已修改此模块，请刷新后重试",
                    details={
                        "module_id": module_id,
                        "client_version": self.provided,
                        "server_version": current_version,
                    },
                )["error"],
            )


def if_match_header(
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
) -> ModuleVersionGuard:
    return ModuleVersionGuard(if_match)
