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
