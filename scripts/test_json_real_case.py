# -*- coding: utf-8 -*-
"""
使用真实AI返回内容测试JSON解析
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 真实的AI返回内容（柯南案例）
REAL_AI_RESPONSE = '''```json
{
  "title_strategy": {
    "conclusions": [
      {
        "point": "标题长度追求"精悍"，以10-20字为主流，且10字以内同样具有竞争力。",
        "reasoning": "数据显示平均标题长度为10.3字，长度分布中"<10字"和"10-20字"区间各占5篇，合计占比超过90%。这说明在小红书"柯南"话题下，用户偏好信息密度高、直奔主题的标题。过短的标题可能包含"柯南""无标题"等关键词，依赖封面视觉冲击；适中的标题则有空间补充事件或情绪描述（如"感叹号结尾"占18.2%），能快速抓住用户注意力，符合平台快节奏的浏览习惯。"
      },
      {
        "point": "标题必须包含核心话题词"柯南"，并搭配"侦探"、"灰原哀"等角色名或事件关键词。",
        "reasoning": "高频关键词TOP10中，"柯南"作为绝对核心词必然出现。"侦探"、"灰原哀"、"毛利兰"、"工藤新"等高频词汇表明，用户不仅关注IP本身，更对具体角色、职业属性和事件充满兴趣。这反映了粉丝圈层的讨论热点，标题中融入这些词能精准吸引目标受众，并提高在相关搜索中的曝光率。"
      }
    ],
    "templates": [
      "情绪+角色+事件！| 感叹柯南里XXX的XXX瞬间！",
      "干货/盘点+核心词 | 柯南里那些细思极恐的侦探排名",
      "场景+互动 | 谁懂啊！在漫展遇到XXX版柯南！"
    ],
    "keywords_must_have": ["柯南", "侦探"]
  },
  "content_strategy": {
    "conclusions": [
      {
        "point": "内容以简短、直接的"分享体"或"观后感体"为主，无需复杂结构，关键在于引发共鸣和互动。",
        "reasoning": "平均内容长度仅126.5字，且内容结构特征数据显示，所有结构化元素（列表、步骤、总结等）占比均为0%，而"has_cta"（行动号召）占比9.1%。这表明爆款内容并非教程或深度解析，而是个人情绪、观点或经历的快速分享。高频词汇"话题"、"事件"、"动漫"、"二次元"也印证了内容围绕IP衍生话题展开讨论，短平快的内容更容易被即时消费和互动。"
      }
    ],
    "structure_guide": "采用"钩子（情绪/事件）+ 细节描述（角色/场景/感受）+ 互动引导（提问/求认同）"的简单三段式。",
    "hooks": ["看到柯南里这一幕，我直接破防了！", "永远会被灰原哀的XXX细节打动！"]
  },
  "cover_strategy": {
    "conclusions": [
      {
        "point": "封面以真人出镜（90.9%）为主导策略，个人形象与IP结合是获得高互动的关键。",
        "reasoning": "真人出镜比例高达90.9%，远超普通笔记水平。结合视频AI洞察中"个人形象类封面最常见"，可以推断这些爆款多为创作者本人的Cosplay（如漫展）、反应表情或与柯南周边的合影。"
      }
    ],
    "text_guide": "在视觉冲击力足够的封面上，叠加1-4个情绪化或悬念式的文字",
    "visual_guide": "首选真人出镜（尤其是表情生动的半身或特写）"
  },
  "product_strategy": {
    "conclusions": [
      {
        "point": "在"柯南"话题下，产品植入需高度场景化，作为内容体验的一部分自然呈现，而非生硬推销。",
        "reasoning": "产品植入分析数据显示所有"提及率"均为0%，但AI洞察却指出"流程中植入产品最有效"。这看似矛盾，实则揭示了一种高级的植入方式。"
      }
    ],
    "timing_guide": "避免在标题或文案开头生硬提及产品",
    "scene_guide": "聚焦于"二次元生活化"场景"
  },
  "checklist": [
    {
      "item": "标题是否在20字以内，并包含"柯南"及至少一个角色/事件关键词？",
      "reason": "数据证明短标题和精准关键词是吸引目标粉丝点击的第一步。"
    },
    {
      "item": "封面是否有真人出镜或极具张力的视觉画面？",
      "reason": "真人出镜率超90%，是建立信任和引发共鸣的关键。"
    }
  ],
  "success_pattern_summary": "这批"柯南"爆款笔记的成功核心在于"真人化IP共鸣"与"圈层化轻量分享"的结合。创作者通过高比例的真人出镜（如Cosplay、情绪反应），将自身化为IP与粉丝之间的情感桥梁，极大提升了内容的亲和力与可信度。"
}
```'''


def test_real_case():
    """测试真实AI返回内容的解析"""
    from viral_agent.services.synthesis_service import SynthesisService

    print("=" * 60)
    print("测试真实AI返回内容解析")
    print("=" * 60)

    service = SynthesisService()
    result = service._parse_ai_response(REAL_AI_RESPONSE)

    # 检查提取结果
    expected_fields = ['title_strategy', 'content_strategy', 'cover_strategy',
                       'product_strategy', 'checklist', 'success_pattern_summary']

    print("\n字段提取结果:")
    all_success = True
    for field in expected_fields:
        has_field = field in result
        status = "✅" if has_field else "❌"
        print(f"  {status} {field}")
        if not has_field:
            all_success = False

    # 检查子字段
    print("\n子字段检查:")
    if 'title_strategy' in result:
        ts = result['title_strategy']
        print(f"  title_strategy.conclusions: {len(ts.get('conclusions', []))}个")
        print(f"  title_strategy.templates: {len(ts.get('templates', []))}个")
        print(f"  title_strategy.keywords_must_have: {len(ts.get('keywords_must_have', []))}个")

    if 'content_strategy' in result:
        cs = result['content_strategy']
        print(f"  content_strategy.conclusions: {len(cs.get('conclusions', []))}个")
        print(f"  content_strategy.hooks: {len(cs.get('hooks', []))}个")
        print(f"  content_strategy.structure_guide: {'有' if cs.get('structure_guide') else '无'}")

    if 'checklist' in result:
        print(f"  checklist: {len(result['checklist'])}项")

    if 'success_pattern_summary' in result:
        summary = result['success_pattern_summary']
        print(f"  success_pattern_summary: {len(summary)}字")

    print("\n" + "=" * 60)
    if all_success:
        print("🎉 所有字段提取成功！")
    else:
        print("⚠️ 部分字段提取失败")
    print("=" * 60)

    return all_success


if __name__ == "__main__":
    success = test_real_case()
    sys.exit(0 if success else 1)
