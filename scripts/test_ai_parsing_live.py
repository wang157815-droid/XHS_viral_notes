#!/usr/bin/env python
"""
实时测试AI响应解析

用于诊断产品分析和AI深度分析的解析问题
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI
import json
import re

def test_product_analysis_parsing():
    """测试产品分析的AI响应解析"""
    print("\n" + "="*60)
    print("测试1：产品分析AI响应解析")
    print("="*60)

    # 使用真实的API调用
    client = OpenAI(
        api_key=os.getenv('OPENAI_API_KEY'),
        base_url=os.getenv('OPENAI_API_BASE')
    )

    model = os.getenv('MODEL_NAME', 'deepseek-chat')
    print(f"使用模型: {model}")
    print(f"API Base: {os.getenv('OPENAI_API_BASE')}")

    # 简单的测试提示，期望返回JSON
    test_prompt = """请分析以下笔记内容，判断产品引出的时机。

笔记标题：夏日美白攻略｜一周让你白到发光
笔记内容：姐妹们谁懂啊！最近发现了一个超好用的美白方法...中间省略...这款欧莱雅美白精华真的太绝了！

请返回JSON格式：
```json
{
    "notes_analysis": [
        {
            "title": "标题",
            "timing": "middle",
            "product_mentioned": true
        }
    ],
    "summary": {
        "early_count": 0,
        "middle_count": 1,
        "late_count": 0,
        "title_mention_count": 0,
        "no_mention_count": 0
    }
}
```"""

    print("\n发送请求...")
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是一个专业的内容分析助手。请严格按照JSON格式返回结果。"},
                {"role": "user", "content": test_prompt}
            ],
            temperature=0.3,
            max_tokens=1000
        )

        ai_response = response.choices[0].message.content
        print(f"\n原始AI响应长度: {len(ai_response)} 字符")
        print(f"\n原始AI响应（前2000字符）:")
        print("-" * 40)
        print(ai_response[:2000])
        print("-" * 40)

        # 检测是否有think标签
        has_think = '<think>' in ai_response
        print(f"\n包含<think>标签: {has_think}")

        # 尝试解析
        from viral_agent.prompts.product_prompts import parse_batch_response
        result = parse_batch_response(ai_response)

        print(f"\n解析结果:")
        print(f"  - 是否有error: {'error' in result}")
        if 'error' in result:
            print(f"  - error内容: {result.get('error')}")
        print(f"  - notes_analysis数量: {len(result.get('notes_analysis', []))}")
        print(f"  - summary: {result.get('summary', {})}")

        return result

    except Exception as e:
        print(f"\n请求失败: {e}")
        return None


def test_manual_parsing():
    """测试手动解析不同格式的响应"""
    print("\n" + "="*60)
    print("测试2：手动解析测试")
    print("="*60)

    # 模拟DeepSeek Reasoner响应
    test_cases = [
        ("纯JSON", '{"notes_analysis": [{"timing": "middle"}], "summary": {"early_count": 0}}'),
        ("```json代码块", '```json\n{"notes_analysis": [{"timing": "middle"}], "summary": {"early_count": 0}}\n```'),
        ("DeepSeek带think", '<think>\n我来分析一下...\n</think>\n\n```json\n{"notes_analysis": [{"timing": "middle"}], "summary": {"early_count": 0}}\n```'),
        ("深度思考后JSON", '<think>\n思考过程...\n</think>\n\n{"notes_analysis": [{"timing": "middle"}], "summary": {"early_count": 0}}'),
    ]

    from viral_agent.prompts.product_prompts import parse_batch_response

    for name, response in test_cases:
        result = parse_batch_response(response)
        has_error = 'error' in result
        print(f"  {name}: {'❌ 失败' if has_error else '✅ 成功'}")
        if has_error:
            print(f"    错误: {result.get('error')}")


def test_multimodal_parsing():
    """测试多模态分析的响应解析"""
    print("\n" + "="*60)
    print("测试3：多模态分析AI响应")
    print("="*60)

    client = OpenAI(
        api_key=os.getenv('OPENAI_API_KEY'),
        base_url=os.getenv('OPENAI_API_BASE')
    )

    model = os.getenv('MODEL_NAME', 'deepseek-chat')

    test_prompt = """请分析这张图片的封面设计特点。

返回JSON格式：
```json
{
    "visual_style": "风格描述",
    "key_elements": ["元素1", "元素2"],
    "appeal_factors": ["吸引力因素1"]
}
```"""

    print("发送请求...")
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": test_prompt}
            ],
            temperature=0.3,
            max_tokens=500
        )

        ai_response = response.choices[0].message.content
        print(f"\n原始响应长度: {len(ai_response)} 字符")
        print(f"\n原始响应（前1500字符）:")
        print("-" * 40)
        print(ai_response[:1500])
        print("-" * 40)

        # 检测think标签
        has_think = '<think>' in ai_response
        print(f"\n包含<think>标签: {has_think}")

        # 尝试解析
        from viral_agent.services.multimodal_analyzer import MultimodalAnalyzer
        analyzer = MultimodalAnalyzer()
        result = analyzer._parse_multimodal_response(ai_response)

        print(f"\n解析结果:")
        print(f"  - parsed标记: {result.get('parsed', True)}")
        if 'raw_insights' in result:
            print(f"  - 回退到raw_insights: 是")
        else:
            print(f"  - 成功解析为结构化数据")

    except Exception as e:
        print(f"\n请求失败: {e}")


if __name__ == '__main__':
    print("="*60)
    print("AI响应解析实时测试")
    print("="*60)

    test_manual_parsing()
    test_product_analysis_parsing()
    test_multimodal_parsing()

    print("\n" + "="*60)
    print("测试完成")
    print("="*60)
