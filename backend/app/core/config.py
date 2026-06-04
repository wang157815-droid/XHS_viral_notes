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
    # Minimax Chat API 配置
    # ------------------------------------------------------------------ #
    minimax_api_key: str = os.getenv("MINIMAX_API_KEY", "")
    minimax_api_base: str = os.getenv("MINIMAX_API_BASE", "https://api.minimax.chat/v1")
    minimax_model: str = os.getenv("MINIMAX_MODEL", "abab6.5s-chat")
    minimax_max_tokens: int = int(os.getenv("MINIMAX_MAX_TOKENS", "1024"))
    # 意图识别专用模型（建议用非推理型模型，避免 think 标签干扰 JSON 解析）
    minimax_intent_model: str = os.getenv("MINIMAX_INTENT_MODEL", "")
    # 系统角色指令（可在 .env 中自定义）
    minimax_system_prompt: str = os.getenv(
        "MINIMAX_SYSTEM_PROMPT",
        "你是一个专业的小红书内容运营助手，擅长爆文分析、内容策划和创作建议。请用简洁友好的中文回答用户问题。",
    )


settings = Settings()

