"""对话附件：多模态消息构造与 vision 分片（不调用真实模型）。"""

from __future__ import annotations

import pytest

from backend.app.application.conversation_service import ConversationService
from backend.app.domain.conversation import IntentClassification
from backend.app.services.conversation_store import ConversationStore

# 1x1 透明 PNG
_MINI_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def test_vision_parts_from_attachments_safe_path_and_png(monkeypatch, tmp_path):
    monkeypatch.setattr(ConversationService, "_repo_root", staticmethod(lambda: tmp_path))
    root = tmp_path / "datas" / "conversation_uploads" / "u1"
    root.mkdir(parents=True)
    (root / "f1.png").write_bytes(_MINI_PNG)

    svc = ConversationService()
    parts = svc._vision_parts_from_attachments(
        "u1",
        [{"storage_subpath": "u1/f1.png", "mime_type": "image/png", "filename": "f1.png"}],
    )
    assert len(parts) == 1
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_vision_parts_rejects_wrong_owner_prefix(monkeypatch, tmp_path):
    monkeypatch.setattr(ConversationService, "_repo_root", staticmethod(lambda: tmp_path))
    root = tmp_path / "datas" / "conversation_uploads" / "u2"
    root.mkdir(parents=True)
    (root / "f1.png").write_bytes(_MINI_PNG)

    svc = ConversationService()
    parts = svc._vision_parts_from_attachments(
        "u1",
        [{"storage_subpath": "u2/f1.png", "mime_type": "image/png", "filename": "f1.png"}],
    )
    assert parts == []


def test_build_general_qa_messages_multimodal_last_user():
    recent = [
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "看图", "attachments": []},
    ]
    vision = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}}]
    msgs = ConversationService._build_general_qa_messages(recent, vision_parts=vision)
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["role"] == "user"
    assert isinstance(msgs[-1]["content"], list)
    assert msgs[-1]["content"][0] == {"type": "text", "text": "看图"}
    assert msgs[-1]["content"][1] == vision[0]


@pytest.mark.asyncio
async def test_stream_general_answer_uses_multimodal_when_image(monkeypatch, tmp_path):
    import backend.app.application.conversation_service as cs

    monkeypatch.setattr(ConversationService, "_repo_root", staticmethod(lambda: tmp_path))
    root = tmp_path / "datas" / "conversation_uploads" / "u1"
    root.mkdir(parents=True)
    (root / "f1.png").write_bytes(_MINI_PNG)

    store = ConversationStore(store_dir=str(tmp_path / "conv"))
    conv = store.create(owner_user_id="u1", title="t")
    svc = ConversationService(store=store)

    captured: dict = {}

    async def fake_chat_stream(agent_id, messages, *, modality="text", **kwargs):
        captured["agent_id"] = agent_id
        captured["modality"] = modality
        captured["messages"] = messages
        yield {"type": "message_done", "message_id": "x", "content": "ok"}

    monkeypatch.setattr(cs.model_gateway, "chat_stream", fake_chat_stream)

    intent = IntentClassification(intent="general_qa", confidence=1.0, reason="test")
    recent_messages = [
        {
            "role": "user",
            "content": "描述图片",
            "attachments": [
                {"storage_subpath": "u1/f1.png", "mime_type": "image/png", "filename": "f1.png"},
            ],
        }
    ]
    async for _ in svc._stream_general_answer(
        conv.conversation_id,
        "u1",
        intent,
        recent_messages,
        None,
    ):
        pass

    assert captured["modality"] == "multimodal"
    assert captured["agent_id"] == "ConversationQAAgent"
    last = captured["messages"][-1]
    assert last["role"] == "user"
    assert isinstance(last["content"], list)
    assert last["content"][0]["type"] == "text"


@pytest.mark.asyncio
async def test_stream_general_answer_text_when_no_image(monkeypatch, tmp_path):
    import backend.app.application.conversation_service as cs

    monkeypatch.setattr(ConversationService, "_repo_root", staticmethod(lambda: tmp_path))
    store = ConversationStore(store_dir=str(tmp_path / "conv"))
    conv = store.create(owner_user_id="u1", title="t")
    svc = ConversationService(store=store)

    captured: dict = {}

    async def fake_chat_stream(agent_id, messages, *, modality="text", **kwargs):
        captured["modality"] = modality
        captured["messages"] = messages
        yield {"type": "message_done", "message_id": "x", "content": "ok"}

    monkeypatch.setattr(cs.model_gateway, "chat_stream", fake_chat_stream)

    intent = IntentClassification(intent="general_qa", confidence=1.0, reason="test")
    recent_messages = [{"role": "user", "content": "纯文字", "attachments": []}]
    async for _ in svc._stream_general_answer(conv.conversation_id, "u1", intent, recent_messages, None):
        pass

    assert captured["modality"] == "text"
    assert isinstance(captured["messages"][-1]["content"], str)
