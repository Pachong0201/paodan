# 台湾政治新闻爆料邮箱智能筛选引擎 V4.2.1（Dashboard Privacy Hotfix）

围绕「台湾政治负面新闻标识规则库 V1.0」（`config/news_signal/`）构建的**爆料邮箱新闻线索筛选引擎**：
从爆料邮箱材料（邮件正文 + 附件）中自动发现值得记者核查的高价值线索，输出 0-100 评分、
S/A/B/C/D 优先级队列、记者摘要与核查清单；**V2 在 S/A/B 线索之上增加首发渠道推荐**
（社交平台/媒体独家/深度调查/记者会/正式检举/高敏感核验六类渠道建议），帮助记者判断
「这条线索适合怎么公开、是否该先检举、是否该先非公开核验」；**V3 在 V2 之上进一步
推荐具体媒体、揭弊政治人物/媒体人、放大者、正式检举机关与平台**，把「建议 R3 深度调查」
升级为「建议交给镜周刊调查线，同步考虑地检署正式检举」。

> 定位：发现「值得记者打开、核查、进一步调查」的新闻线索，**不是**认定犯罪；
> 未经核实的邮件内容一律以「邮件指称/爆料人称/尚待核实」表述，最终新闻判断由记者人工完成。
> 首发渠道模块只输出「建议」：不自动发布、不自动检举、不向任何第三方转发材料，
> 最终发布与提交决定一律由编辑部人工确认。

## 系统链路

```
邮件/附件(.eml+PDF/DOCX/XLSX/CSV/TXT/图片)
→ 文本解析(HTML转文本/附件文本层/OCR可选/表格保留行列)
→ 繁简归一 + 金额/账户/日期实体抽取
→ V1.0 新闻标识词典粗筛(H/M/C/E/S/X)
→ Pattern 新闻指纹匹配(P01-P20, 句/段/相邻3句四级窗口)
→ Negative Rules 反向判断(N01-N08 + X 词 + 上下文排除)
→ LLM 语义研判(OpenAI-compatible API 或离线模板后端)
→ 0-100 综合评分(scoring_rules 七维度 + 阶段/负向调整)
→ S/A/B/C/D 优先级队列
→ 记者摘要(80-180字) + 核查清单(verification_targets)
→ 【V2 首发渠道推荐（仅 S/A/B 且 final_score>=60）】
   ReleaseDecisionFeatures(证据形态+决策特征)
   → ReleaseRouteRuleEngine(RR01-RR27 确定性规则)
   → LLM Release Advisor(语义修正)
   → primary/secondary/avoid 渠道 + 风险矩阵 + 核验清单 + 多阶段发布序列
→ 【V3 具名渠道推荐（依赖 V2 输出）】
   channel_entities(媒体/政治人物/媒体人/平台/机关实体库)
   → NamedChannelEngine(NC01-NC30 规则 → 候选池)
   → fit_score(route/category/evidence/source_protection/location…)
   → LLM 排序/剔除/解释（仅候选池内，schema 拦截名单外实体）
   → recommended_media/actors/amplifiers/formal_channels/platforms + avoid
→ JSONL/CSV 报告 + SQLite 归档(release_recommendations / named_channel_recommendations)
→ 【V3.1 Excel 台账：S/A/B 或 >=60 → 去重 → append/update → important_email_register.xlsx】
→ 【V3.2 Review Queue：S/A/B 或 >=60 → 去重 → 复制 .eml + .review.json 到 data/review_queue/<S/A/B>/】
→ 【V4 治理民生投诉（G01-G12 并行）：G-H/M/C/E/X + GP01-GP10 + GN01-GN04 + GovernanceComplaintScorer → 双轨评分 → S/A/B/C/D → Excel/Review Queue】
```

## 安装与运行

```bash
pip install -r requirements.txt        # 见下方依赖清单
cp .env.example .env                    # 配置 LLM（不配则离线模板模式）
python tests/fixtures/generate_samples.py          # 生成 60 条 V1 筛选样本(可选)
python tests/fixtures/generate_release_samples.py  # 生成 42 条 V2 渠道样本(可选)

# 批量
python -m app.main --input data/inbox/
# 单文件
python -m app.main --file sample.eml
# 纯规则模式（不调用 LLM）
python -m app.main --file sample.eml --no-llm
# 启用首发渠道推荐（S/A/B 且分数>=60 输出渠道建议 + 记者工作台文本）
python -m app.main --file sample.eml --enable-release-advisor
# 启用具名渠道推荐 V3（需 V2 输出，推荐具体媒体/人物/机构）
python -m app.main --file sample.eml --enable-release-advisor --enable-named-advisor
# 只输出 B 及以上
python -m app.main --input data/inbox/ --min-priority B
# 规则包自检
python -m app.main --selfcheck
```

输出：`data/reports/screening_results.jsonl`、`data/reports/priority_queue.csv`；
日志：`logs/app.log`；SQLite：`data/news_screening.db`。

## 目录

```
app/
├── main.py            CLI 入口
├── config.py          配置(阈值/路径/.env)
├── models.py          EmailDocument/RuleResult/LLMResult/FinalScore…
├── parsers/           EML/PDF/DOCX/XLSX/CSV/TXT/图片 解析
├── preprocessing/     清洗/繁转简/金额账户日期抽取
├── rules/             词典/Pattern/Negative/RuleEngine(全部动态加载 YAML)
├── llm/               client/schemas(JSON校验+重试)/screener(规则包提示词)
├── scoring/           FinalScorer(七维度)/KnownNewsMatcher
├── release_advisor/   【V2】首发渠道推荐模块
│   ├── models.py          ReleaseDecisionFeatures/ReleaseRecommendation
│   ├── feature_builder.py 证据形态(17类)与决策特征抽取
│   ├── rule_engine.py      RR01-RR27 确定性渠道规则+风险矩阵+核验清单
│   ├── llm_advisor.py      特征+规则结果注入提示词，LLM 语义修正
│   ├── scorer.py           规则层与 LLM 融合(安全硬约束优先)
│   ├── schema.py           渠道输出 JSON Schema 校验
│   └── formatter.py        记者工作台文本渲染
  ├── named_channel/      【V3】具名渠道推荐模块
│   ├── models.py           NamedChannelHit/NamedChannelRecommendation
│   ├── entity_loader.py    channel_entities/rules/historical_cases 加载+状态过滤+stale检查
│   ├── candidate_engine.py NC 规则→候选池 + fit_score + 画像兜底 + location路由
│   ├── advisor.py          安全约束→LLM 重排→输出组组装（end-to-end）
│   ├── schema.py           LLM 输出校验（候选池外实体拦截）
│   └── formatter.py        记者工作台具名渠道文本渲染
├── storage/           SQLite Database/Repository(去重/渠道建议落库)
├── pipeline/          端到端编排
├── reports/           JSONL/CSV 导出 + 【V3.1】excel_register.py 重要爆料邮件台账
├── review_queue/      【V3.2】manager.py/models.py 待审查文件夹（复制+去重+优先级同步+sidecar）
config/excel_register.yaml     【V3.1】台账开关/路径/阈值/备份
config/review_queue.yaml       【V3.2】待审开关/路径/阈值/同步/sidecar
config/news_signal/    规则包(唯一知识底座，代码不硬编码关键词)
config/release_routes.yaml       【V2】六类首发渠道 taxonomy(R1-R6)
config/release_route_rules.yaml  【V2】渠道规则 RR01-RR27 + 特征映射 + 风险矩阵
config/channel_entities.yaml     【V3】具名渠道实体库（媒体/政治人物/媒体人/平台/机关）
config/named_channel_rules.yaml  【V3】具名渠道规则 NC01-NC30
config/channel_historical_cases.yaml 【V3】近5年 30 个真实案例（首发/放大/核验分类）
prompts/release_route_advisor_prompt.md 【V2】首发渠道顾问 LLM 提示词
prompts/named_channel_advisor_prompt.md 【V3】具名渠道顾问 LLM 提示词
tests/                 155 项测试 + V1 60 条 + V2 42 条 + V3 71 条标注样本(fixtures)
data/                  inbox/processed/reports/news_screening.db + review_queue/S/A/B
```

## V2 首发渠道推荐（Release Route Advisor）

### 六类主渠道（config/release_routes.yaml）

- **R1 社交平台首发型**：第一人称受害/性骚/职场霸凌/组织吃案，当事人亲自发声
  （Facebook/Threads/X，系统不指定账号）。
- **R2 媒体独家型**：照片/视频/内部文件/私德争议/权势关系，需记者先核验来源、
  控制披露节奏。
- **R3 深度调查报道型**：土地/光电/标案/献金/补助/复杂政商网络，需数据交叉验证。
- **R4 记者会/公开展示型**：论文比对/合同/时间线/数据表等可对照证据；真实未确认
  不得优先。
- **R5 正式检举优先型**：收贿/洗钱/金流/灭证串证风险/组织犯罪/贿选/国安，
  建议先向检调/监察/学伦/主管机关提交（只建议、不代送）。
- **R6 高敏感核验型**：机密/国安/匿名文件/疑似黑客资料，先核真后决定公开，
  默认禁 R1/R4。

### 关键设计

- **只服务 S/A/B 且 final_score >= 60**（阈值 `release_advisor_min_score` 在
  `release_route_rules.yaml`，默认 60）；C/D 不进入渠道判断。
- **证据形态 17 类多选**：FIRST_PERSON_TESTIMONY / CHAT_RECORD / AUDIO / VIDEO /
  PHOTO / BANK_RECORD / CONTRACT / INTERNAL_DOCUMENT / OFFICIAL_DOCUMENT /
  PROCUREMENT_FILE / SPREADSHEET / DATABASE_RECORD / ACADEMIC_DOCUMENT /
  LOCATION_DATA / CLASSIFIED_DOCUMENT / ANONYMOUS_DOCUMENT / MULTI_SOURCE_PACKAGE。
- **多渠道输出**：每条线索输出 primary_route + secondary_routes + avoid_routes；
  高敏感/匿名场景 R6/R2 的 avoid（R1/R4）为硬约束，不受 LLM 改写。
- **规则与 LLM 双轨**：RR01-RR27 确定性规则（YAML 配置驱动，权重投票+冲突消解），
  LLM 顾问做语义修正（api/template 双模式），失败回退规则结果。
- **风险矩阵 12 类**：SOURCE_EXPOSURE / PRIVACY / DEFAMATION / EVIDENCE_AUTHENTICITY /
  CONTEXT_LOSS / RETALIATION / DESTRUCTION_OF_EVIDENCE / WITNESS_COLLUSION /
  CLASSIFIED_INFORMATION / LEGAL_PROCESS_INTERFERENCE / MISLEADING_OLD_NEWS /
  DOCUMENT_FORGERY。
- **多阶段发布序列**：recommended_release_sequence 支持
  非公开核验 → 正式检举 → 媒体独家 → 记者会的分步编排。
- **输出**：SQLite `release_recommendations` 表；
  `priority_queue.csv` 增加 recommended_release_route / secondary_release_routes /
  formal_referral_recommended / release_risk / release_reason 五列；
  CLI 工作台文本（推荐首发/后续公开/不建议/原因/发布前核验/建议发布序列）。

## V3 具名渠道推荐（Named Channel Recommender）

在 V2 渠道类型之上推荐**具体媒体、揭弊政治人物/媒体人、放大者、正式机关、平台**。

### 实体库（config/channel_entities.yaml，54 个实体）

- **媒体 17 家**：镜周刊/TVBS/联合报/自由时报/中国时报/ETtoday/三立/民视/公视/
  风传媒/Newtalk/壹苹/CTWANT/上报/太报/美丽岛/菱传媒（historical，不参与 Top 推荐）
- **政治人物 10 人**：黄国昌/王鸿薇/徐巧芯/凌涛/谢龙介/陈椒华/邱显智/陈琬惠/
  游淑慧/侯汉廷（职位不硬编码，current_role 字段维护）
- **媒体人 3 人**：黄扬明/吴子嘉/邱毅（吴/邱标注 verification_required，不得作单一核验源）
- **平台 5 个**：Facebook/Threads/PTT/X/YouTube（PTT 标 source_authenticity_risk: high）
- **正式机关 15 个**：各地检署/调查局/廉政署/监察院/学伦/劳动/性平/NCC/采购审计…
  （支持 location-aware：台南案自动带出台南地检署）
- **地方渠道 3 类 + 内部编辑部**：LOCAL_WHISTLEBLOWER_CHANNEL 为类型化建议，不虚构人名

每个实体带 0-100 六维评分（reach/verification/source_protection/speed/complexity/
controversy_risk）、status（active/inactive/historical/unknown）与
last_verified_date（超过 180 天启动时输出 stale 警告）。

### 匹配与打分

- **NC01-NC30 规则**（config/named_channel_rules.yaml）生成候选池；
- **fit_score 0-100**：route_match + category_match + evidence_match +
  source_protection + complexity + speed + location + role + risk_penalty，
  传播力(reach)权重极低，**传播能力≠可信度**；
- **画像兜底**：规则未命中时按类别/证据形态逐组补候选，保证有 V2 路线即有具名推荐；
- **角色分流**：media=首发/调查，disclosure actors=揭弊/资料分析，amplifiers=放大
  （吴子嘉/邱毅类只进放大器或需高核验门槛），formal=FORMAL_REFERRAL/VERIFIER；
- **政治立场不参与匹配**：只按议题类型/证据形态/擅长领域/角色。

### 安全硬约束

- A17/机密/匿名材料：禁止 PTT/Threads/政论放大者/社交平台，只推内部编辑部+调查机关；
- 匿名来源（明示或材料匿名）：揭弊政治人物与公开个人社媒不进 Top 推荐；
- 性骚实名（NC07 victim_first）：当事人平台/媒体优先，政治人物不默认排在当事人前；
- LLM 只能从候选池排序/剔除/解释；schema 校验拦截名单外实体（unlisted_channel_suggestion
  仅提示不进入正式推荐）。

### 输出与存储

- SQLite `named_channel_recommendations` 表（email_id/entity_id/entity_name/
  entity_type/channel_group/channel_role/fit_score/rank/reason）；
- `priority_queue.csv` 增加 top_media/top_disclosure_actor/top_amplifier/
  top_formal_channel/named_channel_reason 五列；
- CLI `--enable-named-advisor` 输出工作台块（具名媒体/适配揭弊渠道/放大渠道/
  正式渠道/不建议/原因/建议顺序）。

### 核心设计

- **规则包为纲**：taxonomy/keywords/pattern/negative/scoring 全部从 YAML 动态加载；
  词典含 H(高强度)/M(隐性)/C(场景)/E(原始证据)/S(程序)/X(反向) 六类，权重不等。
- **规则负责高召回，LLM 负责语义**：默认 `rule_score>=35`(可配)或命中高价值 Pattern /
  「E类证据+目标+金额」才调用 LLM；LLM 必须同时参考规则命中结果与原文。
- **Pattern 上下文窗口**：同一句 > 同段 > 相邻3句 > 全文，命中保留证据片段。
- **反向结果处理**：不起诉/无罪/查无不法 + 无新证据 → P20/EX 降权进入 C/D；
  旧案反向但提供新证据(如新银行流水) → 记录反向状态但**不**过滤，恢复调查价值。
- **繁简归一**：邮件文本繁转简后匹配，词典自动对齐；亲属称谓等少量口语变体有兜底。
- **LLM 输出校验**：JSON Schema 校验失败自动重试一次，二次失败 `llm_status=failed`
  单封跳过不中断整批。
- **容错**：损坏 PDF、OCR 失败、附件解析异常、单封乱码均记录后继续。
- **数据安全**：原始邮件只读不删不改不转发；日志掩码银行账号/身份证/手机号；
  API Key 仅环境变量；分析结果区分「爆料内容」与「已核实事实」。

## 依赖

python-dotenv、PyYAML、requests、pypdf、PyMuPDF、python-docx、openpyxl、Pillow、
opencc-python-reimplemented（繁转简）、pytest。
OCR 需另行安装 tesseract（可选，缺失时图片/扫描 PDF 标记 skipped 并记录 warning）。

## 测试

```bash
python -m pytest tests -q
```

95 项测试覆盖：规则包加载、词典/Pattern/Negative 引擎、解析器(EML/PDF/DOCX/XLSX/CSV)、
LLM JSON 校验、金额抽取、60 条端到端 V1 样本质量门槛、42 条 V2 渠道样本门禁
（**Top-1 ≥85%、Top-2 ≥95%、高敏感 R1/R4 错误首推 0、匿名 R1 首推≤5%、检举召回≥90%**）、
以及 V3 具名渠道门禁（71 条标注样本）：
**Named Top-1 ≥80%（实测 95.7%）、Top-3 Coverage ≥95%（实测 100%）、
Actor Top3 ≥90%（实测 97.4%）、Formal Top2 ≥95%（实测 98.6%）、
Role Accuracy ≥95%、Sensitive Safety(PTT/Threads 0 错误首推)、
匿名保护(公开社媒 0 错误首推)、Top3 媒体多样性 ≥12 实体（实测 12）、
C/D 不产生具名推荐、LLM 候选池外实体拦截、台南 location-aware 路由、历史案例库 30 条。

 另有 V3.1 Excel 台账 27 项（`tests/test_excel_register.py`，全部临时目录，不污染 `data/reports/`）：
 S/A/B 进入、C/D 不进入、阈值配置化、Message-ID/BodyHash 去重、重跑更新系统字段、
 人工字段保护、V2/V3/附件/核查重点写入、自动创建/追加、占用不崩溃、损坏备份、批量 100 单次保存、
 表头/冻结/筛选/换行/编号门禁；V3.2 待审查文件夹 24 项（`tests/test_review_queue.py`，全部临时目录）：
 S/A/B 进入对应目录、C/D 不进入、阈值配置化、Message-ID/Hash 去重、重跑不重复、A↔S 同步迁移、
 降级移出、原邮件保护、Windows 文件名安全、超长主题、sidecar 生成与内容、source 缺失/复制异常不崩、
 批量 100、CLI enable/disable（含 disable 优先）、Excel 待审查路径回填兼容、配置加载。
 全量共 155 项（`python -m pytest tests -q` 须 0 failed）。

## V3.1 重要爆料邮件 Excel 台账（Important Email Register）

登记层：`V1 → V2 → V3 → SQLite → JSONL → CSV → Important Email Register.xlsx`，
只登记结果，不改 V1 评分/S/A/B/Pattern/Negative/V2/V3/LLM 语义。

### 默认路径

```text
data/reports/important_email_register.xlsx
```

工作表名 `重要爆料邮件`（另有可选 `说明` 表：用途/字段/S/A/B/状态值/生成时间）。

### 默认进入条件

```text
优先级 S/A/B，或 final_score >= 60
```

配置 `config/excel_register.yaml`：

```yaml
excel_register:
  enabled: true
  path: data/reports/important_email_register.xlsx
  min_score: 60
  priorities: [S, A, B]
  backup_before_batch: true
```

`.env` 可用 `EXCEL_REGISTER_ENABLED/PATH/MIN_SCORE/PRIORITIES/BACKUP` 覆盖；
CLI `--enable-excel-register / --disable-excel-register` 覆盖配置文件（`--disable` 优先）。
`enabled: false` 时不生成不更新。

```bash
python -m app.main --input data/inbox/ --enable-excel-register
python -m app.main --input data/inbox/ --disable-excel-register
```

### 去重与更新

- `register_key = message_id if message_id else content_hash(body_hash)`，不用 subject/sender/date 去重；
- 同一封重跑默认不新增，只更新系统字段（优先级/评分/类别/摘要/V2/V3/核查重点等），编号不变；
- 批量一次打开、批量 append/update、保存一次；单文件模式直接保存。

### 人工字段（不会被系统后续运行覆盖）

```text
处理状态（默认 待看，建议值：待看/已看/跟进中/已采用/暂缓/排除）
责任编辑（默认 ""）
人工标签（默认 ""）
记者备注（默认 ""）
```

**这些人工字段不会被系统后续运行覆盖。** 更新已有行时编号、登记时间、人工字段一律保留。

### Excel 字段（38 列固定顺序）

```text
编号/登记时间/邮件日期/收件时间/发件人/邮件主题/优先级/最终评分/主要类别/子类别/
涉及人物/涉及机构/涉及公司/涉及项目/一句话摘要/为什么值得看/命中Pattern/关键证据/
金额/利益/新增信息/已知旧闻/核查重点/V2推荐首发类型/V2备选渠道/V3推荐媒体/
V3推荐揭弊人物/V3推荐放大者/V3正式渠道/风险提示/附件数量/附件名称/原始邮件路径/
Message-ID/Content Hash/处理状态/责任编辑/人工标签/记者备注
```

数组用 `；` 连接，核查重点为 `1. …\n2. …` 换行；类别/Pattern/渠道/检举类型
一律只显示自然语言名称（如`正式检举优先型`、`收贿、索贿及职务对价`、
`厂商金钱职务对价`、`检察机关`，摘要正文内嵌代码亦同），不带 A03/P04/R5 之类代码，
V3 显示 `镜周刊(97)；TVBS(91)`（Top1-3，括号内为适配分），不为 Excel 再调 LLM，不嵌入附件。

### 样式与异常

- `openpyxl`：冻结首行 `A2`、整表 AutoFilter、表头加粗、重点列加宽、长文本换行、垂直顶部对齐；
- S/A/B 允许条件格式着色，但文字始终明确显示 `S/A/B`；
- 被记者打开占用（`PermissionError`）只记 warning，保留筛选结果，CLI 正常完成，待写入进
  `data/reports/pending_excel_register.jsonl` 下次自动重试；
- 损坏无法读取记 ERROR 并重命名为 `important_email_register.corrupt.<timestamp>.xlsx` 再重建，不静默覆盖；
- 批处理前可选一次备份到 `data/reports/backups/`。

## V3.2 重要邮件待审查文件夹（Review Queue）

复制层：`V1 → V2 → V3 → SQLite → JSONL → CSV → Excel Register → Review Queue`，
只复制结果，不改 V1/V2/V3 任何评分/规则语义；方便记者人工打开审查。

```text
data/review_queue/S| A/ B/
YYYYMMDD_HHMMSS_<PRIORITY>_<SCORE>_<hash8>_<subject>.eml + .review.json
```

配置 `config/review_queue.yaml`（`.env` 以 `REVIEW_QUEUE_*` 覆盖，CLI `--enable-review-queue /
--disable-review-queue` 覆盖配置文件，`--disable` 优先）：

```yaml
review_queue:
  enabled: true
  path: data/review_queue
  priorities: [S, A, B]
  min_score: 60
  split_by_priority: true
  copy_original_eml: true
  move_original: false
  deduplicate: true
  sync_priority_changes: true
  write_sidecar_json: true
  extract_attachments: false
```

```bash
python -m app.main --input data/inbox/ --enable-release-advisor --enable-named-advisor --enable-excel-register --enable-review-queue
```

- 进入条件与台账一致：`priority in [S,A,B] OR final_score >= 60`（均可配置）；
- **复制不移动**原邮件；`source_path` 缺失只记 warning 并跳过该封；
- 去重键 `MID:<message_id>` 优先，否则正文内容哈希 `HASH:<hash>`（不用 subject/sender/date），重跑不重复；
- 优先级变化自动同步目录（旧目录副本删除，只保留一份）；降级到 C/D 且开启同步时移出副本与 sidecar（不删原邮件）；
- 文件名清洗 Windows 非法字符（`\ / : * ? " < > |`）、限长，唯一性依赖 review_key/hash；
- sidecar `xxx.review.json` 含 review_key/优先级/评分/类别/人物/Pattern/摘要/原因/核查重点/source_path/queued_at，不写正文与附件；
- 单封失败（缺源/复制/权限/非法文件名/建目录/sidecar）只 warning，不中断 Pipeline；
- Excel 台账自动回填 `待审查文件路径` 列（动态追加，不破坏原有 38 列表头；`处理状态` 仍是唯一人工状态字段）。

## 已知新闻接口

`KnownNewsMatcher` 读 `data/known_cases.jsonl`（人物/公司/项目/结果/日期），
返回 `known/matched_events/possible_new_information`；V1 本地模拟，后续可接新闻库。

## 人物库扩展

`config/news_signal/person_aliases.yaml` 预留人物别名/现职/党籍/地区结构，V1 未强制建库。
V3 实体库（政治人物/媒体人）通过 `config/channel_entities.yaml` 的 current_role 字段维护身份变化。

## 限制（真实）

- LLM 默认离线模板后端为确定性兜底，语义判断能力有限；接真实 API 后质量会显著提升。
- OCR 依赖本机 tesseract，未安装环境图片类附件只标记 skipped。
- 人名识别为启发式(规则)，不支持大规模政要库；需靠 person_aliases.yaml 逐步扩充。
- 已知新闻匹配为本地 JSONL 模拟，未接实时新闻库。
- 评分校准基于 60 条样本集，实际分布可能偏移，需记者反馈迭代。
- V2 渠道推荐为「建议」而非「发布指令」；R1 实名发声等判定依赖爆料邮件内的
  意愿表述（愿意实名/要求匿名），未明说时按保守处理（不首推 R1）。
- V2 确定性规则在 42 条渠道样本上校准（Top-1 100%），真实分布可能偏移；
  渠道边界案例（如性骚第三人检举 vs 当事人实名的措辞差异）仍需记者复核。
- 附件级证据形态依赖解析文本内容，未解析（OCR 缺失/加密）附件无法参与形态判定。
- LLM 渠道顾问未接真实 API 实测，仅离线模板验证链路；api 模式输出需真实模型回归。
- V3 具名实体库（媒体画像/人物擅长领域/评分）为规则依据的静态快照，人物职位与
  媒体路线会变化：last_verified_date 超 180 天仅告警不自动禁用，需编辑部定期维护；
  具名推荐必须由编辑部人工确认后再联系任何媒体/人物/机关。
- V3 的历史案例库按公开报道整理，存在首发媒体归属争议的案例以编辑部复核为准；
  location 识别为粗抽（县市词表），跨辖区案件（如北高两地）不自动判断管辖。
- V3 模板模式（未接 LLM API）下排序完全由 fit_score 决定，理由为模板化文本；
  真实 LLM 可提供更细致的语义解释与剔除判断。


---

# V4.1.1 Final Hardening（Stability, Privacy & Dual-Track Unification）

> 本版本不新增业务类别，重点是修复数据一致性、附件串件、外部 LLM 隐私、V1/V4 语义断层、
> 数据库持久化、配置失效、Pattern 全文拼接误报、表格公式注入，并建立可持续演进基础。

## 1. 新主链

```text
EML
 ↓
Secure Parsing
 ↓
附件原始 SHA256 / Parse Cache（source_sha256 + parser_version + ocr_version）
 ↓
文本标准化
 ↓
 ┌────────────────────────┐
 ▼                        ▼
V1 Political Track     V4 Governance Track
A01-A18                G01-G12
P01-P20                GP01-GP10
 │                        │
 └───────────┬────────────┘
             ▼
      UnifiedSignalSet
             ↓
       Local Features
             ↓
  External Privacy Gateway
             ↓
        LLM（可选）
             ↓
      Unified Final Score
             ↓
   Summary / Verification
             ↓
   V2 Release Advisor
             ↓
   V3 Named Advisor
             ↓
SQLite / JSONL / CSV / Excel / Review Queue
```

V4 不再只在最后修改 `final_score`。Governance 类别、分数、Pattern、风险、核验建议会进入
`UnifiedSignalSet`、摘要、V2 渠道语义、V3 具名候选和 SQLite `governance_results`。

## 2. UnifiedSignalSet 与 primary_track

统一信号模型位于 `app/signals/`，字段包括：

```text
political_categories / governance_categories
political_patterns / governance_patterns
political_score / governance_score
primary_track / secondary_track
entities / money / evidence_shapes / evidence_stage
risk_flags / old_news / new_information
public_interest / track_confidence / final_score / priority
```

`primary_track` 支持：

```text
POLITICAL   仅 V1 达到有效阈值
GOVERNANCE  仅 V4 达到有效阈值
MIXED       双方均达到有效阈值（两条轨道都保留）
NONE        双方均弱
```

阈值集中在 `config/news_signal/unified_signals.yaml`，不散落 hardcode。

## 3. External LLM Privacy Gateway

生产默认：

```text
privacy_level = STRUCTURED_ONLY
```

所有 External LLM 请求必须经过：

```text
SafePayloadBuilder -> JSON serialize -> OutboundGuard final scan -> requests.post
```

External 模型当前**看不到**：

```text
原始 EML
完整正文 / body_text / combined_text / original_text / normalized_text / html_body
完整附件 / OCR 全文 / 原始 PDF / DOCX / XLSX / 图片
sender 真实姓名 / sender email / recipients
Message-ID 原文
本机路径 / 缓存路径
API Key / Token / Password / Secret
银行账号 / 身份证 / 电话 / 精确住址 / 爆料源身份
```

External 可见数据只包括结构化类别、分数、Pattern、证据阶段/形态、匿名化实体、金额、
关系链、行为/程序信号、旧闻/新增信息、风险 flags、公开候选实体和公开历史案例摘要。

LOCAL 目的地（`localhost` / `127.0.0.1` / `::1`）才允许较完整文本模式；目的地只由 URL
hostname 判断，不根据模型名、供应商名或 API Key 判断。未知公网 host、HTTP 公网地址一律 BLOCK。
`chat_json(prompt, raw_string)` 在 EXTERNAL 目的地会 fail closed：`requests.post = 0`。
本版本不提供 `ALLOW_RAW_EXTERNAL_LLM=1` 之类一键绕过。

配置见 `config/security.yaml`；安全审计日志只写 `logs/security_audit.jsonl` 的最小字段，
不写 payload/正文/sender 邮箱/电话/银行账号/API key/附件文本。

## 4. 附件身份、Parse Cache 与去重

附件身份拆分：

```text
source_sha256 = 原始附件 bytes SHA256
text_sha256   = 抽取文本标准化后的 SHA256
```

附件缓存路径为：

```text
.attachments_cache/<email_raw_sha256>/<attachment_source_sha256>_<safe_filename>
```

同一目录下 `mail_A.eml/evidence.pdf=AAA` 与 `mail_B.eml/evidence.pdf=BBB` 解析后不会串件。
重复附件 bytes 通过 `attachment_parse_cache(source_sha256, parser_version, ocr_version)`
复用解析/OCR 结果，第二次不得重复 parse/OCR。

## 5. Email Identity

```text
有 Message-ID:
  email_id = MID:<sha256(normalized_message_id)>

无 Message-ID:
  email_id = RAW:<sha256(raw_eml_bytes)>
```

不再使用 `body_hash[:16]` 作为无 Message-ID 主键。同正文、不同发件人、无 Message-ID 的
两封邮件必须生成不同 `email_id`，数据库同时保留两条。

## 6. Database / Analysis Versioning / Reprocess

数据库使用 `app/storage/migrations/` 自动迁移，`schema_version` 当前为 `3`；旧库启动时保留旧数据、
清理历史重复子行、补齐新表/列/唯一索引。新增：

```text
governance_results
analysis_runs
attachment_parse_cache
```

一封邮件持久化使用 `with db.transaction():`，失败 rollback，禁止 emails 有、scores/governance 无
的半成品。子表具备幂等唯一约束（attachments/entities/pattern_matches/named_channel_recommendations）。

`analysis_runs` 记录 pipeline/rule pack/scoring/prompt hash、llm mode/provider/model 和结果状态。
重复导入与 stale analysis 分开处理：已导入但规则包/prompt hash 变化时会自动重新分析。
CLI 新增：

```bash
python -m app.main --file sample.eml --reprocess
python -m app.main --input data/inbox/ --rescore
python -m app.main --input data/inbox/ --reanalyze
python -m app.main --file sample.eml --llm-trigger-score 60
```

## 7. Governance Pattern 与数值泛化

GP01-GP10 的 `window` 配置为 `sentence/paragraph/context3`，默认 `allow_full_document: false`。
只有显式 `allow_full_document: true` 才允许全文共现；不同段落分别出现 required group 不得拼接命中。

`app/governance/numeric_features.py` 统一抽取 duration / frequency / affected_population /
money_loss / complaint_count / waiting_time / deadline_pressure，支持：

```text
500户 / 约五百户 / 数百户 / 超过四百个家庭
8天 / 八天 / 一周 / 超过一星期 / 半年 / 六个月 / 连续数月 / 多年 / 三年
5次 / 五次 / 多次 / 反复 / 连续投诉 / 打了七次电话
```

核验建议集中在 `app/governance/verification.py`，不在 scorer 中散落。

## 8. Spreadsheet Security

CSV/Excel 所有邮件可控字段写文件前必须调用 `app/security/spreadsheet.py:spreadsheet_safe()`。
字符串 trim-left 后以 `= + - @` 开头时强制作为文本，避免 Excel/CSV 公式注入。

## 9. Security Notes

- `tests/fixtures/` 放 synthetic 测试样本；`data/inbox/**` 默认全部忽略，公开仓库不得提交真实 `.eml`。
- `scripts/security_scan.py` 检查 tracked files 的 API key、私密邮箱、本机路径、银行账号 canary 和 `.eml`：`data/` 下直接 FAIL；`tests/fixtures/` 必须有 `X-Paodan-Synthetic-Fixture: true` 或命中 `tests/fixtures/manifest.json` SHA256。
- 自动 PII 识别不可能 100%；KnownNews 仍为 `local_stub`，未接台湾新闻数据库；匿名实体分类仍可能有误差。
- 外部 LLM 生产调用前仍应由编辑部确认隐私策略、allowlist 与审计日志留存。


---

# V4.1.1 Final Hardening

本轮只做可靠性、安全性、一致性收口，不扩展新业务类别、媒体、渠道、关键词库或产品功能。

## 1. External LLM 安全边界

- 生产默认仍为 `STRUCTURED_ONLY`。
- External raw 永远禁止；不存在 `ALLOW_RAW_EXTERNAL_LLM` 一类绕过。
- External 请求必须经过 `SafePayloadBuilder -> OutboundGuard final scan -> requests.post`。
- Privacy Block 属于安全层 fail closed：`requests.post = 0`，但业务层必须降级继续本地规则、Final Score、Summary、Verification、SQLite、Excel、Review Queue。
- `llm_status` 可能为 `blocked`；不得把被阻断的敏感内容写入 reason。
- `REDACTED_SNIPPETS` 仅为受限模式：必须经过实体假名化 + `PrivacyRedactor` + `OutboundGuard`；生产推荐只使用 `STRUCTURED_ONLY`。

## 2. V3 Candidate Pool

- External LLM 的 `pool_ids` 必须是候选实体 ID，例如 `media_mirror`、`media_udn`、`actor_huang_kuochang`、`authority_prosecutor_generic`。
- 禁止使用 NC rule ID、RR route ID、category ID 作为候选池。
- LLM 只能调整排序、`fit_score`、`reason`、`avoid`；候选池外实体必须被 schema 拒绝并回退 rule。

## 3. Analysis Versioning 重新分析条件

重复导入时，以下任一变化都会触发 `analysis stale` 并允许重新分析：

```text
pipeline_version
political_rule_pack_hash
governance_rule_pack_hash
scoring_rule_hash
prompt_hash
llm_mode
llm_provider
llm_model
release_rule_hash
named_rule_hash
channel_entity_hash
```

`analysis_runs` 记录上述版本/哈希，避免 V2/V3 规则更新后高价值邮件被 duplicate 逻辑挡住。

## 4. 数据一致性

- 旧库 migration 会检测 `attachments`、`entities`、`pattern_matches`、`named_channel_recommendations` 的重复行。
- 按逻辑主键保留最新/最完整一条，再建立严格 UNIQUE INDEX。
- 若旧库 `source_sha256` 为空，附件使用兼容键 `(email_id, filename, sha256, content_hash)` 清理历史重复。
- 一封邮件持久化仍使用一个业务事务；任何异常 rollback。

## 5. Windows 正式支持

- GitHub Actions 新增 `windows-latest + Python 3.12`。
- Windows 必须执行 `pytest -q`、`python -m app.main --selfcheck`、`python scripts/security_scan.py`。
- 重点覆盖 drive path、UNC path、Path resolve、SQLite、Excel、attachments_cache、temp file、Unicode/中文路径。
- OCR 不可用时允许 graceful degraded / skip，不得因路径问题静默忽略。

## 6. Review Queue 与 KnownNews

- Review Queue 属本地敏感数据区，只做本地复制/去重/sidecar；不得同步公共云盘、不得 external upload、不得自动发送。
- KnownNews 仍为 `local_stub`，`possible_new_information=[]` 只表示 `novelty_status=unknown`，不代表确认没有新增信息。

## 7. 定位声明

本系统只做新闻线索筛选和核验辅助，不对指控作事实认定；所有发布、检举、联系媒体/人物/机关的动作均需编辑部人工确认。


---

# Local Dashboard（V4.2 MVP）

## 启动

```bash
python -m app.dashboard.server
```

或：

```bash
python -m app.main --dashboard
```

访问：

```text
http://127.0.0.1:8765
```

默认只监听：

```text
127.0.0.1
```

不支持公网、端口转发或反向代理暴露。本 Dashboard 不是邮件服务器、不是发布系统、不是事实认定系统、不是云端平台；只用于本地查看、筛选、人工复核与备注。

## 数据来源

- 复用现有 `data/news_screening.db`（与主程序同一个 SQLite 文件）。
- Dashboard 不建立第二套业务库。
- 只新增 `dashboard_reviews` 人工审核表。
- Dashboard 不触发规则引擎、LLM、Governance Engine 或重新评分。

## 页面

```text
/                首页：统计、Track、筛选、高价值列表、分页
/review          人工审核队列
/emails/{id}     邮件详情：双轨、证据、核查、V2、V3、附件、人工审核
```

## 人工审核

固定状态：

```text
UNREVIEWED 未审核
VERIFY 待核查
PRIORITY 重点跟进
VERIFIED 已核实
LOW_VALUE 价值有限
FALSE_POSITIVE 误报
ARCHIVED 已归档
```

人工状态与自动评分完全隔离，`FALSE_POSITIVE` 不会把 `scores.final_score` 改成 0。

## 隐私边界

- 首页与详情页默认不显示原始正文、附件全文、OCR 全文。
- 不显示 sender email、recipients、cc、Message-ID、source_path、cached_path。
- 不显示银行账户、电话、身份证、API Key。
- 不在线打开原文件。
- Dashboard 不调用外部 LLM。
- 页面资源全部本地，不使用 CDN / Google Fonts / 公网资源。
- 所有页面使用 Jinja2 自动转义，人工备注和邮件内容按不可信字符串处理。

## 统计口径

“今日/最近 N 日”按 `emails.processed_at` 日期统计；“待人工审核”定义为：

```text
自动分数 priority in (S,A,B)
且 dashboard_review_status in (UNREVIEWED, VERIFY, PRIORITY)
```

无 `dashboard_reviews` 行视为 `UNREVIEWED`。


---

# V4.2.1 Dashboard Privacy Hotfix

## 展示层隐私过滤

Dashboard 不只不读取 raw body，还会在构造 Safe ViewModel 前对以下派生文本做统一隐私过滤：

```text
subject
summary_zh
reason_for_attention
verification_targets
attachment filename
V2 reason / verification / risk / sequence
V3 reason
```

邮箱、手机、台湾身份证、银行卡、路径、Message-ID、API token、LINE ID、精确地址、普通私人姓名等都会在进入 HTML/API 前脱敏。

## Entity 规则

```text
普通 PERSON 默认不显示。

可显示：
  ORGANIZATION / COMPANY / GOVERNMENT_AGENCY / PROJECT / LOCATION / ROLE
  以及明确 TARGET / PUBLIC_PERSON / PUBLIC_ORGANIZATION

V3 公开候选库实体名称继续显示。
```

## CSRF

人工 Review POST 使用严格 CSRF Token：

```text
missing token -> 403
wrong token   -> 403
valid token   -> 200
evil Origin   -> 403
```

## 时区

Dashboard 默认时区：

```text
Asia/Shanghai
```

可通过 `DASHBOARD_TIMEZONE` 或 `config/dashboard.yaml` 修改；非法时区启动 FAIL。

## 分数显示

Political / Governance 分数不再从 `final_score` 复制：

```text
Political = unified.political_score（或 POLITICAL-only 回退）
Governance = unified.governance_score / scores.governance_score / governance_results.score
```
