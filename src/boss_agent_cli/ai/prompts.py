"""Prompt templates for AI-powered resume and job analysis.

All templates use Python str.format() placeholders:
- {jd_text} — Job description text
- {resume_text} — Resume plain text

Note: JSON braces in templates are doubled ({{ }}) to avoid format conflicts.
"""

JD_ANALYSIS_PROMPT = """你是一位资深的招聘顾问和职业规划专家。请分析以下职位描述（JD）与候选人简历的匹配程度。

## 职位描述
{jd_text}

## 候选人简历
{resume_text}

## 输出要求
请以 JSON 格式返回分析结果，包含以下字段：
```json
{{
  "match_score": 85,
  "match_analysis": "整体匹配度分析...",
  "matching_points": ["匹配点1", "匹配点2"],
  "gap_points": ["差距点1", "差距点2"],
  "suggestions": ["建议1", "建议2"],
  "risk_factors": ["风险因素1"],
  "overall_recommendation": "推荐/谨慎推荐/不推荐"
}}
```

只返回 JSON，不要包含其他内容。"""

RESUME_POLISH_PROMPT = """你是一位专业的简历优化顾问。请对以下简历进行润色和优化，重点关注：

1. 使用 STAR 法则（情境-任务-行动-结果）重新组织工作经历
2. 量化成果：尽可能用数据说明影响力
3. 关键词优化：确保包含行业常用术语
4. 语言精炼：去除冗余表述，突出核心价值

## 当前简历
{resume_text}

## 输出要求
请以 JSON 格式返回优化后的内容：
```json
{{
  "polished_sections": [
    {{
      "section": "模块名称",
      "original": "原始内容",
      "polished": "优化后内容",
      "changes": ["修改说明1", "修改说明2"]
    }}
  ],
  "general_suggestions": ["全局建议1", "全局建议2"],
  "keyword_additions": ["建议添加的关键词1", "关键词2"]
}}
```

只返回 JSON，不要包含其他内容。"""

RESUME_OPTIMIZE_FOR_JD_PROMPT = """你是一位资深的求职顾问。请针对指定职位优化候选人的简历，提升匹配度。

## 目标职位描述
{jd_text}

## 当前简历
{resume_text}

## 优化要求
1. 调整简历措辞以匹配 JD 中的关键技能和经验要求
2. 突出与目标职位最相关的经历
3. 弱化或省略不相关的内容
4. 确保真实性 — 只调整表述，不捏造经历

## 输出要求
请以 JSON 格式返回：
```json
{{
  "match_score_before": 65,
  "match_score_after": 82,
  "optimized_sections": [
    {{
      "section": "模块名称",
      "original": "原始内容",
      "optimized": "优化后内容",
      "reason": "优化理由"
    }}
  ],
  "key_adjustments": ["关键调整1", "关键调整2"],
  "warnings": ["注意事项1"]
}}
```

只返回 JSON，不要包含其他内容。"""

RESUME_SUGGEST_PROMPT = """你是一位职业发展顾问。请根据候选人的简历和目标职位，提供具体的改进建议。

## 目标职位描述
{jd_text}

## 候选人简历
{resume_text}

## 输出要求
请以 JSON 格式返回改进建议，按优先级排序：
```json
{{
  "suggestions": [
    {{
      "priority": "high",
      "category": "技能补充",
      "suggestion": "具体建议内容",
      "action_items": ["行动项1", "行动项2"],
      "expected_impact": "预期效果说明"
    }}
  ],
  "short_term_plan": "1-2周内可完成的改进计划",
  "long_term_plan": "1-3个月的提升方案"
}}
```

priority 取值: "high", "medium", "low"

只返回 JSON，不要包含其他内容。"""

CHAT_REPLY_PROMPT = """你是一位资深求职顾问。请根据招聘者消息和可选上下文，生成 2-3 条高质量回复草稿。

## 招聘者消息
{recruiter_message}

## 上下文（可选）
{context}

## 候选人简历摘要（可选）
{resume_text}

## 语气偏好
{tone}

## 输出要求
请以 JSON 格式返回回复草稿，长度控制在 30-80 字：
```json
{{
  "intent_analysis": "招聘者意图判断",
  "reply_drafts": [
    {{
      "style": "简洁专业",
      "text": "回复正文",
      "suitable_when": "适用场景说明"
    }}
  ],
  "key_points": ["应该覆盖的要点1", "要点2"],
  "avoid": ["应避免的表达1"]
}}
```

style 取值建议: "简洁专业", "热情积极", "谨慎确认"。

只返回 JSON，不要包含其他内容。"""


INTERVIEW_PREP_PROMPT = """你是一位资深的技术/HR 面试官，同时熟悉候选人视角的面试准备。请基于目标职位描述生成面试准备材料。

## 目标职位描述
{jd_text}

## 候选人简历（可选）
{resume_text}

## 题量
{count}

## 输出要求
请以 JSON 格式输出。题量严格等于「题量」指定的数字，按 技术 / 行为 / 情景 三类合理分布。
```json
{{
  "job_summary": "一句话概括职位画像（岗位方向、核心技能、经验层级）",
  "questions": [
    {{
      "category": "技术",
      "question": "问题正文",
      "framework": "建议回答结构（例如 STAR / 先定义再举例 / 对比分析）",
      "evaluation_points": ["考察点1", "考察点2"],
      "difficulty": "简单/中等/高"
    }}
  ],
  "preparation_tips": ["高优先级准备项1", "准备项2"]
}}
```

category 取值限定: "技术", "行为", "情景"。结合简历时请针对其真实经历生成问题，不要凭空捏造。

只返回 JSON，不要包含其他内容。"""


CHAT_COACH_PROMPT = """你是一位专注招聘场景的沟通教练。请基于候选人与招聘者的聊天记录，分析当前沟通状态并给出可执行建议。

## 聊天记录
{chat_text}

## 候选人简历摘要（可选）
{resume_text}

## 沟通风格偏好
{style}

## 输出要求
请以 JSON 格式输出诊断和行动方案：
```json
{{
  "stage_analysis": "当前阶段判断（如 初次接触 / 意向确认 / 薪资讨论 / 面试安排）",
  "recruiter_intent": "招聘者当前意图",
  "strengths": ["沟通中做得好的点1", "点2"],
  "weaknesses": ["可以改进的点1", "点2"],
  "next_action_recommendation": "下一步最重要的动作（具体可执行）",
  "message_templates": [
    {{
      "scenario": "应用场景说明",
      "text": "可直接发送的消息文本（30-80 字）"
    }}
  ],
  "avoid_pitfalls": ["应避免的雷区1"]
}}
```

诊断要紧贴聊天记录里出现的具体句子。message_templates 提供 2-3 条不同场景的现成文案，照顾到用户指定的沟通风格。

只返回 JSON，不要包含其他内容。"""
