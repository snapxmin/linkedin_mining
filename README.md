# LinkedIn Talent Research

合规优先的人才画像研究工具，以 **OKX** 为示例。系统聚合公开搜索元数据与用户提供的 CSV，支持复核、去重、统计和导出。

## 重要边界

- 仅处理公开索引信息与用户合法提供的 CSV。
- 不登录 LinkedIn，不绕过验证码，不批量抓取受限页面。
- 搜索结果默认进入 `pending` 队列，建议人工复核后再纳入统计。
- 使用者需自行遵守数据来源条款、当地隐私法律与数据保留要求。

## 功能

- 创建公司研究项目（例如 `OKX`）
- 生成 `site:linkedin.com/in "OKX"` 及职位关键词查询
- 通过可替换搜索提供商采集公开元数据（未配置密钥时使用 demo 数据）
- 原子 CSV 导入与字段标准化
- Web 仪表板：搜索、导入、复核、分布统计、CSV 导出
- 默认统计仅包含 `verified` 记录，可切换 `all`（pending + verified）

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
cp .env.example .env
uvicorn app.main:app --reload
```

打开 `http://127.0.0.1:8000/`。

## GitHub Pages 演示

静态演示站点展示 OKX 大学/职位/地点分布样例（1247 人虚构样本）：

- 站点地址：https://snapxmin.github.io/linkedin_mining/
- 若首次访问 404，请在仓库 **Settings → Pages** 中将 Source 设为 **GitHub Actions** 或 **Deploy from a branch → gh-pages / root**，然后等待 1–2 分钟。

完整搜索、导入、复核与导出功能仍需本地运行上述命令。

## 演示流程（无需 API 密钥）

1. 创建 `OKX` 项目。
2. 点击“运行搜索”使用 demo 提供商写入示例候选人。
3. 导入 `sample_data/okx_profiles.csv`。
4. 在复核表格中补充/确认大学、职位、地点，并标记 `verified`。
5. 查看大学分布统计并导出 CSV。

## CSV 字段

支持以下列名（大小写不敏感，可带空格）：

| 字段 | 别名 |
| --- | --- |
| Name | name |
| Current Company | current company |
| University | university |
| Degree | degree |
| Location | location |
| Role | role |
| Years | years, years experience |
| URL | url, profile url |
| Review Status | review status |

## 可选：Serper 公开搜索

在 `.env` 中设置：

```bash
SERPER_API_KEY=your_key_here
```

应用会使用 Serper 的公开索引接口返回标题、摘要和 URL，不会打开 LinkedIn 个人页。

## API 概览

- `GET /health`
- `POST /api/projects`
- `GET /api/projects/{id}`
- `POST /api/projects/{id}/search`
- `POST /api/projects/{id}/imports/csv`
- `GET /api/projects/{id}/profiles`
- `PATCH /api/profiles/{id}`
- `GET /api/projects/{id}/analytics?scope=verified|all`
- `GET /api/projects/{id}/export.csv?scope=verified|all`

## 测试

```bash
python -m pytest -q
python -m compileall -q app
```

## 样本规模建议

当 `verified` 样本达到 **1000+** 人时，大学分布通常已经比较稳定。系统分页支持大规模数据集浏览与导出。

## 设计文档

- 设计说明：`docs/superpowers/specs/2026-07-14-linkedin-talent-mining-design.md`
- 实施计划：`docs/superpowers/plans/2026-07-14-linkedin-talent-mining.md`
