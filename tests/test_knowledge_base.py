"""
Verify legacy domain knowledge entry points are disabled.
"""
from viral_agent.prompts.knowledge_base import DOMAIN_KEYWORDS, detect_domain, get_domain_knowledge
from viral_agent.prompts.video_timeline_prompts import get_timeline_analysis_prompt
from viral_agent.config.knowledge_loader import get_knowledge_config, reload_knowledge_config


def test_domain_detection_disabled():
    assert DOMAIN_KEYWORDS == {}
    assert detect_domain("eye cream", "dark circles") == []
    assert get_domain_knowledge(["eye_care"]) == ""


def test_dynamic_prompt_uses_generic_knowledge_only():
    prompt = get_timeline_analysis_prompt(
        title="eye massage tutorial",
        description="care routine",
    )

    assert "eye massage tutorial" in prompt
    assert "eye_care" not in prompt
    assert "face_care" not in prompt
    assert "makeup" not in prompt


def test_knowledge_config_compatibility_stub():
    config = reload_knowledge_config()
    assert config is get_knowledge_config()
    assert config.get_all_domains() == []
    assert config.get_enabled_domains() == []
    assert config.get_domain_by_id("eye_care") is None
    assert config.detect_domain("eye cream", "dark circles") == []
    assert config.get_domain_knowledge_text(["eye_care"]) == ""
    assert config.add_domain({"id": "x"}) is False
    assert config.update_domain("x", {}) is False
    assert config.delete_domain("x") is False
    assert config.export_config()["domains"] == []


def main():
    test_domain_detection_disabled()
    test_dynamic_prompt_uses_generic_knowledge_only()
    test_knowledge_config_compatibility_stub()
    print("Legacy domain knowledge compatibility stub verified")


if __name__ == "__main__":
    main()
