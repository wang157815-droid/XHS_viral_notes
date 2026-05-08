"""Phase 2-B: XHS 凭据校验 Agent。

在 ``InputParser`` 之后、``Crawler`` 之前的前置 gate：

- 取得当前任务的 ``owner_user_id``（RedMuse user_id）；
- 通过 :class:`XhsCredentialResolver` 拿一份"可用 cookie 字符串"快照
  （不真发请求，仅判断 store 状态 + 文件存在性）；
- 解析失败时抛 :class:`XhsAuthRequiredError`，由 orchestrator 捕获并把任务
  置为 FAILED + ``error_code=AUTH_XHS_NOT_BOUND``，前端拿到该错误码即可
  定向到「数据源授权」UI。

为什么不直接放在 CrawlerAgent 内部？
- CrawlerAgent 的失败语义偏"采集失败 / 风控"，error_code 与"未授权"混在一起对
  前端不友好；
- 抽出独立 stage 让指标 / 日志 / 重试策略都能精准定位"授权阶段"问题；
- Phase 3 起 XhsAuthAgent 可以直接发起重新授权流程（短信、虚拟号），不需要再
  改 CrawlerAgent。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from loguru import logger

from ...domain.error_codes import ErrorCode
from ...domain.task_context import TaskContextWriter
from ...infrastructure.repository import task_repository
from ...services.xhs_auth import (
    get_credential_resolver,
    get_credential_store,
)
from .base import AgentContext, AgentResult, BaseAgent


class XhsAuthRequiredError(RuntimeError):
    """需要重新授权 XHS 数据源时抛出。

    携带 ``code = AUTH_XHS_NOT_BOUND``，让 orchestrator 走统一错误处理路径。
    """

    code: str = ErrorCode.AUTH_XHS_NOT_BOUND.value

    def __init__(
        self,
        message: str,
        *,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.details = dict(details or {})


class XhsAuthAgent(BaseAgent):
    """轻量 cookie 检查 agent。"""

    agent_id = "XhsAuthAgent"
    provides: list[str] = []  # 不产出画布模块
    depends_on: list[str] = []
    write_partition = "xhs_auth_status"

    async def run(self, context: AgentContext) -> AgentResult:
        task_id = context.task_id
        owner = self._resolve_owner_user_id(task_id)

        # 任务无 owner_user_id 时（开发场景 / 旧任务无 RedMuse 关联），不阻断；
        # 让 CrawlerAgent 自行降级 stub。
        if not owner:
            logger.warning(
                "[XhsAuthAgent] task={} 无 owner_user_id，跳过授权检查", task_id
            )
            await self.emit_log(
                task_id, "warn", "任务未关联 RedMuse 用户，跳过 XHS 凭据检查"
            )
            self._write_status(context, status="skipped", message="无 owner_user_id")
            return AgentResult(ok=True, output={"status": "skipped"})

        resolver = get_credential_resolver()
        resolved = resolver.resolve(owner)

        if not resolved.found:
            cred = get_credential_store().get_by_redmuse_user_id(owner)
            details = {
                "redmuse_user_id": owner,
                "credential_status": cred.status if cred else "unbound",
                "is_bound": bool(cred and cred.cookies_path),
            }
            self._write_status(
                context,
                status="not_bound",
                message="未找到可用 cookie，需要重新授权",
                details=details,
            )
            await self.emit_log(
                task_id,
                "error",
                "未绑定可用的小红书数据源，请前往「数据源授权」完成扫码",
            )
            raise XhsAuthRequiredError(
                "当前用户尚未绑定可用的小红书数据源，请先完成扫码授权",
                details=details,
            )

        # store 中如果记录为 expired，也算需要重授权（即使 cookies.json 文件还在）
        if resolved.source == "credential" and resolved.redmuse_user_id:
            cred = get_credential_store().get_by_redmuse_user_id(
                resolved.redmuse_user_id
            )
            if cred and cred.status == "expired":
                details = {
                    "redmuse_user_id": owner,
                    "credential_status": "expired",
                    "is_bound": True,
                }
                self._write_status(
                    context,
                    status="expired",
                    message=cred.status_message or "Cookie 已过期",
                    details=details,
                )
                await self.emit_log(
                    task_id,
                    "error",
                    f"小红书 Cookie 已过期：{cred.status_message or ''}，请重新授权",
                )
                raise XhsAuthRequiredError(
                    "小红书 Cookie 已过期，请重新授权后再发起任务",
                    details=details,
                )

        # 通过
        self._write_status(
            context,
            status="ready",
            message=f"凭据来源={resolved.source}",
            details={
                "redmuse_user_id": owner,
                "source": resolved.source,
                "xhs_user_id": resolved.xhs_user_id,
            },
        )
        await self.emit_progress(
            task_id,
            f"小红书数据源凭据已就绪（来源 {resolved.source}）",
            progress=5,
        )
        return AgentResult(ok=True, output={"status": "ready", "source": resolved.source})

    # ----- helpers -----
    @staticmethod
    def _resolve_owner_user_id(task_id: str) -> str:
        try:
            record = task_repository.get(task_id)
            if record and getattr(record, "owner_user_id", None):
                return str(record.owner_user_id)
        except Exception:
            pass
        return ""

    def _write_status(
        self,
        context: AgentContext,
        *,
        status: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """落到 TaskContext.xhs_auth_status，便于审计 / 前端可视化。"""
        try:
            payload = {
                "status": status,
                "message": message,
                "details": details or {},
            }
            TaskContextWriter(context.task_context).write(
                self.write_partition, payload, agent_id=self.agent_id
            )
        except Exception as exc:
            # 不让 status 写入失败阻塞授权检查本身
            logger.debug(f"[XhsAuthAgent] 写 task_context 失败: {exc}")
