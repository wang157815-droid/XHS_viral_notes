from __future__ import annotations

from backend.app.domain.conversation import ChatMessage, new_id
from backend.app.services.conversation_store import ConversationStore


def test_conversation_store_persists_messages(tmp_path):
    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    conversation = store.create(owner_user_id="u1", title="测试会话")

    store.append_message(
        conversation.conversation_id,
        ChatMessage(
            message_id=new_id("msg"),
            conversation_id=conversation.conversation_id,
            role="user",
            content="你好",
        ),
    )

    reloaded = ConversationStore(store_dir=str(tmp_path / "conversations"))
    result = reloaded.get_with_messages(conversation.conversation_id)

    assert result is not None
    loaded_conversation, messages = result
    assert loaded_conversation.title == "测试会话"
    assert loaded_conversation.owner_user_id == "u1"
    assert [m.content for m in messages] == ["你好"]


def test_conversation_store_owner_guard(tmp_path):
    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    conversation = store.create(owner_user_id="u1")

    assert store.ensure_owner(conversation.conversation_id, "u1").conversation_id == conversation.conversation_id

    try:
        store.ensure_owner(conversation.conversation_id, "u2")
    except PermissionError:
        pass
    else:
        raise AssertionError("owner mismatch should raise PermissionError")
