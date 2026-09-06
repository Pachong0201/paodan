# 台湾政治爆料邮箱新闻标识规则库 v1.0

文件：
- taxonomy.yaml：分类、证据阶段、字段定义
- keywords.yaml：各类关键词、关系词、证据词、程序词
- pattern_rules.yaml：高价值组合规则
- negative_rules.yaml：反向结果、误报降权和上下文排除
- scoring_rules.yaml：0-100重要性评分
- llm_email_screening_prompt.md：可直接用于LLM初筛的系统提示词

建议执行链：
1. 邮件解析/OCR/附件文本化
2. 人物与机构实体识别
3. keywords.yaml 进行宽召回
4. pattern_rules.yaml 进行组合加权
5. negative_rules.yaml 处理反向结果和误报
6. LLM根据 llm_email_screening_prompt.md 进行语义筛选
7. scoring_rules.yaml 输出优先级
8. S/A级进入人工记者核查

注意：
- 规则用于新闻线索筛选，不用于事实或法律结论。
- E1爆料必须标记待核实。
- EX不起诉/无罪等必须保留并影响状态。
