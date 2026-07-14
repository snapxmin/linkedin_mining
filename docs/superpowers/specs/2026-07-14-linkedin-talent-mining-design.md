# LinkedIn 公开人才画像系统设计

## 目标

构建一个以 OKX 为示例的研究型人才画像系统。系统从合规搜索接口返回的公开搜索结果、用户上传的 CSV 和人工录入中建立候选人数据集，支持复核、去重、标准化、统计和导出。系统不登录 LinkedIn、不绕过验证码或访问控制，也不批量抓取受限页面。

## 范围

MVP 支持以下工作流：

1. 创建公司研究项目，例如 `OKX`。
2. 生成 `site:linkedin.com/in "OKX"` 及带职位关键词的搜索任务。
3. 通过可替换的搜索提供商采集公开索引结果；未配置提供商时可使用示例数据。
4. 导入 CSV，字段包括姓名、当前公司、大学、学位、地点、职位、工作年限和公开资料 URL。
5. 按规范化 URL 去重，保存来源、采集时间和复核状态。
6. 在 Web 界面中筛选、编辑和确认候选记录。
7. 展示大学、职位、地点和学位分布，并显示样本量。
8. 导出当前项目的规范化 CSV。

MVP 不包括代理池、账号自动化、验证码处理、私有资料采集、自动联系候选人或对个人进行敏感属性推断。

## 推荐方案与取舍

### 方案 A：直接抓取 LinkedIn 页面

字段可能较丰富，但页面经常要求登录并有自动化限制，维护成本和合规风险都高，不采用。

### 方案 B：搜索 API + CSV + 人工复核

搜索 API 提供公开索引的标题、摘要和 URL，CSV 允许研究者补充合法取得的数据，人工复核保证字段质量。覆盖率不如登录态抓取，但边界清晰、可测试、可替换，作为 MVP。

### 方案 C：仅 CSV 分析

最简单且风险最低，但缺少发现候选人的能力。保留为无 API 密钥时的降级工作流，而不是唯一方案。

## 架构

应用采用 Python 3、FastAPI、Jinja2、SQLite 和原生 JavaScript。单进程服务提供 HTML 页面和 JSON API，适合本地研究或小团队内部部署。

核心模块：

- `projects`：研究项目及目标公司。
- `search`：查询生成、搜索提供商接口和结果采集。
- `profiles`：候选记录、URL 规范化、去重、编辑和复核。
- `imports`：CSV 校验与批量导入。
- `analytics`：按大学、职位、地点和学位聚合。
- `exports`：规范化 CSV 下载。
- `web`：项目概览、候选列表、复核表单和统计图表。

搜索提供商通过接口隔离。MVP 实现示例提供商和可选的 Serper HTTP 提供商；密钥只从环境变量读取，不写入数据库或日志。

## 数据模型

### Project

- `id`
- `name`
- `company`
- `created_at`

### SearchRun

- `id`
- `project_id`
- `query`
- `provider`
- `status`
- `result_count`
- `error_message`
- `created_at`

### Profile

- `id`
- `project_id`
- `name`
- `current_company`
- `university`
- `degree`
- `location`
- `role`
- `years_experience`（可空浮点数）
- `profile_url`
- `normalized_url`
- `source`
- `source_query`
- `review_status`（`pending`、`verified`、`rejected`）
- `created_at`
- `updated_at`

同一项目内 `normalized_url` 唯一。没有 URL 的 CSV 行以规范化后的姓名、公司和职位生成稳定指纹去重。

## 数据流

1. 用户创建 OKX 项目。
2. 系统根据公司和可选职位关键词生成公开搜索查询。
3. 提供商返回标题、摘要和 URL。
4. 解析器只从结果元数据中提取确定性字段；无法确定的字段保持为空，不猜测。
5. 候选记录进入 `pending` 队列。
6. 用户复核或通过 CSV 补充字段后标记为 `verified`。
7. 统计默认仅计算 `verified`，可切换查看全部非拒绝记录。
8. 导出包含来源和复核状态，便于研究审计。

## API 与界面

主要 API：

- `POST /api/projects`
- `GET /api/projects/{project_id}`
- `POST /api/projects/{project_id}/search`
- `POST /api/projects/{project_id}/imports/csv`
- `GET /api/projects/{project_id}/profiles`
- `PATCH /api/profiles/{profile_id}`
- `GET /api/projects/{project_id}/analytics`
- `GET /api/projects/{project_id}/export.csv`

Web 界面包含项目首页、创建项目、项目仪表板、CSV 导入、搜索触发、候选复核表格和分布统计。统计以横向条形图呈现，并明确显示样本量与筛选口径。

## 错误处理与隐私

- CSV 缺少必要列时返回逐项可读错误，不写入部分批次。
- 单行字段格式错误时返回行号；合法行只有在整批校验通过后写入。
- 搜索提供商超时或限流时记录失败的 SearchRun，不重复写入候选人。
- 服务端限制上传大小和分页大小。
- URL 仅允许 `http`/`https`，公开资料 URL 经过规范化。
- 页面不显示 API 密钥，日志不记录密钥或完整上传内容。
- README 明示使用者需遵守数据来源条款、当地隐私法律和数据保留要求。

## 测试

- 单元测试覆盖 URL 规范化、查询生成、元数据解析、CSV 校验、去重和聚合。
- API 测试覆盖项目创建、导入原子性、复核状态更新、分页、统计和导出。
- 提供商测试使用本地假实现，不依赖外部网络。
- 端到端冒烟测试验证从创建项目到导出 CSV 的主流程。

## 验收标准

- 全新环境可按 README 在本地启动。
- 无外部 API 密钥时仍可通过示例搜索和 CSV 完成完整流程。
- 重复导入同一资料不会增加候选人数。
- 默认统计只包含已复核记录，计数与导出一致。
- 至少可处理 1,000 条候选记录并在分页界面中浏览。
- 自动化测试全部通过，且没有真实 LinkedIn 网络请求。
