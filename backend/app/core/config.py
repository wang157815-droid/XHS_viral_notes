import os

from pydantic import BaseModel


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings(BaseModel):
    app_name: str = "RedMuse API"
    app_version: str = "0.1.0-phase0"
    api_prefix: str = "/api/v1"
    conversation_os_enabled: bool = _env_bool("CONVERSATION_OS_ENABLED", True)
    conversation_knowledge_qa_enabled: bool = _env_bool("CONVERSATION_KNOWLEDGE_QA_ENABLED", True)

    # ------------------------------------------------------------------ #
    # 自主规划 Agent 运行时（phase3）
    # ------------------------------------------------------------------ #
    # 总开关：关闭后 agent_task 意图降级为普通问答，xhs/comment 不再反问执行模式
    agent_runtime_enabled: bool = _env_bool("AGENT_RUNTIME_ENABLED", True)
    # 命中成熟 workflow（爆文/评论分析）时是否反问"定制 workflow vs AI 自主分析"
    agent_runtime_offer_choice: bool = _env_bool("AGENT_RUNTIME_OFFER_CHOICE", True)
    # loop 护栏
    agent_runtime_max_iters: int = int(os.getenv("AGENT_RUNTIME_MAX_ITERS", "8"))
    agent_runtime_tool_budget: int = int(os.getenv("AGENT_RUNTIME_TOOL_BUDGET", "16"))
    # 昂贵工具"调用次数"上限（批量工具算 1 次；批量化后此值不再是瓶颈，作安全上限）
    agent_runtime_max_expensive_calls: int = int(os.getenv("AGENT_RUNTIME_MAX_EXPENSIVE_CALLS", "12"))
    # 昂贵工具"单位"总预算（≈上游 API 请求数）：单位制让逐条拆解 N 篇不被按次数误伤
    agent_runtime_expensive_unit_budget: int = int(os.getenv("AGENT_RUNTIME_EXPENSIVE_UNIT_BUDGET", "60"))
    # 单工具超时按成本分级：cheap（知识库/联网检索）短，expensive（三方采集/多模态）长。
    # 旧变量 agent_runtime_per_tool_timeout 保留为未分级时的兜底默认。
    agent_runtime_per_tool_timeout: float = float(os.getenv("AGENT_RUNTIME_PER_TOOL_TIMEOUT", "120"))
    agent_runtime_per_tool_timeout_cheap: float = float(os.getenv("AGENT_RUNTIME_PER_TOOL_TIMEOUT_CHEAP", "60"))
    agent_runtime_per_tool_timeout_expensive: float = float(os.getenv("AGENT_RUNTIME_PER_TOOL_TIMEOUT_EXPENSIVE", "200"))

    # ------------------------------------------------------------------ #
    # 企业微信回调配置
    # ------------------------------------------------------------------ #
    # 企微后台「开发者接口」→「接收消息」中填写的 Token
    wxwork_token: str = os.getenv("WXWORK_TOKEN", "")
    # 企微后台生成的 EncodingAESKey（43位，不含末尾'='）
    wxwork_encoding_aes_key: str = os.getenv("WXWORK_ENCODING_AES_KEY", "")
    # 企业 ID（企微后台「我的企业」→「企业ID」）
    wxwork_corp_id: str = os.getenv("WXWORK_CORP_ID", "")
    # 应用 AgentID（企微后台「应用管理」→ 对应应用）
    wxwork_agent_id: int = int(os.getenv("WXWORK_AGENT_ID", "0"))
    # 应用 Secret
    wxwork_corp_secret: str = os.getenv("WXWORK_CORP_SECRET", "")
    # 查看消息记录的管理密钥（自定义一个复杂字符串即可）
    wxwork_admin_secret: str = os.getenv("WXWORK_ADMIN_SECRET", "")
    # 每个用户保留的最大对话轮数（1轮 = 用户1条+AI1条），超出后裁剪最早的
    wxwork_max_history_turns: int = int(os.getenv("WXWORK_MAX_HISTORY_TURNS", "10"))

    # ------------------------------------------------------------------ #
    # 企业微信会话内容存档配置
    # ------------------------------------------------------------------ #
    # 管理后台「管理工具 → 聊天内容存档」里的专用 Secret（与应用 Secret 不同）
    wxwork_finance_secret: str = os.getenv("WXWORK_FINANCE_SECRET", "")
    # 官方 C++ SDK .so 文件路径（Linux x86：libWeWorkFinanceSdk_C.so）
    wxwork_finance_sdk_path: str = os.getenv("WXWORK_FINANCE_SDK_PATH", "/app/WeWorkFinanceSdk_C.so")
    # RSA 私钥文件路径（与上传到企微后台的公钥配对）
    wxwork_rsa_private_key_path: str = os.getenv("WXWORK_RSA_PRIVATE_KEY_PATH", "wxwork_private_key.pem")
    # RSA 私钥内容（与 _PATH 二选一，内容中 \n 用 \\n 转义）
    wxwork_rsa_private_key: str = os.getenv("WXWORK_RSA_PRIVATE_KEY", "")

    # ------------------------------------------------------------------ #
    # 三方 Redbook API 配置（评论 Skills 搜索 + 评论采集）
    # ------------------------------------------------------------------ #
    redbook_api_base: str = os.getenv("REDBOOK_API_BASE", "http://115.190.137.33:10158/redbook")
    redbook_api_key: str = os.getenv("REDBOOK_API_KEY", "")

    # ------------------------------------------------------------------ #
    # Minimax Chat API 配置
    # ------------------------------------------------------------------ #
    minimax_api_key: str = os.getenv("MINIMAX_API_KEY", "")
    minimax_api_base: str = os.getenv("MINIMAX_API_BASE", "https://api.minimax.chat/v1")
    minimax_model: str = os.getenv("MINIMAX_MODEL", "abab6.5s-chat")
    minimax_max_tokens: int = int(os.getenv("MINIMAX_MAX_TOKENS", "1024"))
    # 系统角色指令（可在 .env 中自定义）
    minimax_system_prompt: str = os.getenv(
        "MINIMAX_SYSTEM_PROMPT",
        "你是一个专业的小红书内容运营助手，擅长爆文分析、内容策划和创作建议。请用简洁友好的中文回答用户问题。",
    )


settings = Settings()

