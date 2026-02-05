"""
Cookie 有效性验证模块

提供 Cookie 完整性和有效性的统一检查逻辑。
供 QRCodeLoginService 和 APIQRCodeLoginService 共同使用。

验证策略：
1. 字段完整性检查（无网络开销）
2. 多关键词 API 轮询（防止单关键词限流导致假阴性）
3. 三值结果：valid / invalid / uncertain
"""
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger


# Cookie 中必须存在的关键字段
REQUIRED_COOKIE_FIELDS = ["a1", "web_session"]

# 验证用的关键词列表（热门词，减少因单一关键词限流导致的假阴性）
_VERIFY_KEYWORDS = ["美妆", "穿搭", "美食"]


@dataclass
class CookieValidationResult:
    """Cookie 验证结果（三值逻辑）"""

    status: str  # "valid" | "invalid" | "uncertain"
    reason: str  # 人类可读的原因描述
    missing_fields: Optional[list[str]] = field(default=None)

    @property
    def is_valid(self) -> bool:
        return self.status == "valid"

    @property
    def is_uncertain(self) -> bool:
        return self.status == "uncertain"


def check_cookie_fields(cookies_str: str) -> CookieValidationResult:
    """
    检查 Cookie 字符串中是否包含必需字段（快速、无网络开销）。

    Returns:
        CookieValidationResult: 字段检查结果
    """
    if not cookies_str or not cookies_str.strip():
        return CookieValidationResult(
            status="invalid",
            reason="Cookie 为空",
            missing_fields=list(REQUIRED_COOKIE_FIELDS),
        )

    missing = [f for f in REQUIRED_COOKIE_FIELDS if f"{f}=" not in cookies_str]

    if missing:
        parts = []
        if "web_session" in missing:
            parts.append("缺少 web_session（登录流程可能未完成，请检查是否需要短信验证）")
        if "a1" in missing:
            parts.append("缺少 a1（签名必需字段）")
        return CookieValidationResult(
            status="invalid",
            reason="；".join(parts),
            missing_fields=missing,
        )

    return CookieValidationResult(status="valid", reason="字段完整")


def verify_cookie_with_api(cookies_str: str) -> CookieValidationResult:
    """
    通过实际 API 调用验证 Cookie 是否真正有效。

    验证策略：
    1. 字段完整性（快速排除明显无效的 Cookie）
    2. 多关键词轮询搜索，任意一个关键词有数据即判定有效
    3. 兜底：搜索全空时用 get_homefeed_all_channel 二次确认
    4. 三值结果：
       - valid: 至少一个接口拿到了数据
       - invalid: 字段缺失等确定性失败
       - uncertain: 网络问题或搜索全空（可能是新号/限流）
    """
    # 第一层：字段完整性
    field_check = check_cookie_fields(cookies_str)
    if not field_check.is_valid:
        logger.warning(f"Cookie 字段检查失败: {field_check.reason}")
        return field_check

    # 第二层：多关键词 API 轮询
    try:
        from apis.xhs_pc_apis import XHS_Apis

        xhs = XHS_Apis()
        all_success_but_empty = True  # 跟踪是否全部 success=True 但数据为空
        has_api_failure = False  # 跟踪是否有 API 调用失败

        for keyword in _VERIFY_KEYWORDS:
            try:
                success, msg, data = xhs.search_some_note(keyword, 1, cookies_str)

                if success and data and len(data) > 0:
                    # 搜到了数据，Cookie 确认有效
                    logger.info(f"Cookie 验证通过（关键词「{keyword}」返回 {len(data)} 条结果）")
                    return CookieValidationResult(status="valid", reason="验证通过")

                if not success:
                    # API 调用失败，可能是网络问题
                    has_api_failure = True
                    all_success_but_empty = False
                    logger.debug(f"验证关键词「{keyword}」失败: {msg}")
                else:
                    # success=True 但数据为空
                    logger.debug(f"验证关键词「{keyword}」: 请求成功但无数据")

            except Exception as e:
                has_api_failure = True
                all_success_but_empty = False
                logger.debug(f"验证关键词「{keyword}」异常: {e}")

        # 所有关键词都试过了，没有一个返回数据
        if all_success_but_empty:
            # 兜底：用频道列表接口二次确认（不依赖搜索词，更稳定）
            return _fallback_channel_check(xhs, cookies_str)

        if has_api_failure:
            return CookieValidationResult(
                status="uncertain",
                reason="部分 API 调用失败，可能是网络问题，建议稍后重试",
            )

        # 不应到达此处，但防御性返回
        return CookieValidationResult(status="uncertain", reason="验证结果不确定")

    except Exception as e:
        logger.warning(f"Cookie API 验证异常: {e}")
        return CookieValidationResult(
            status="uncertain",
            reason=f"验证过程出错: {str(e)}",
        )


def _fallback_channel_check(xhs, cookies_str: str) -> CookieValidationResult:
    """
    兜底验证：当搜索全空时，用频道列表接口确认 Cookie 是否可用。

    频道列表不依赖搜索词，对新号/限流/区域限制更稳定。
    - 有数据 → valid
    - success=True 但无数据 → uncertain（可能是新号，留给重试机制）
    - 失败 → uncertain
    """
    try:
        success, msg, data = xhs.get_homefeed_all_channel(cookies_str)
        if success and data:
            logger.info(f"Cookie 兜底验证通过（频道列表返回数据）")
            return CookieValidationResult(status="valid", reason="频道列表验证通过")

        # 频道也为空，大概率是半有效 Cookie，但不能 100% 确定
        logger.warning("搜索和频道列表均无数据，Cookie 可能未完成登录")
        return CookieValidationResult(
            status="uncertain",
            reason=(
                "所有验证接口请求成功但数据均为空，"
                "Cookie 可能未完成登录（请检查是否需要短信验证）"
            ),
        )
    except Exception as e:
        logger.debug(f"兜底频道列表验证异常: {e}")
        return CookieValidationResult(
            status="uncertain",
            reason="验证接口异常，建议稍后重试",
        )
