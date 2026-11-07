# 海外创业志 · 独立人物故事生产器（M7）

此仓库只负责将 `overseas_stories` 中的**中文结构化案例与证据**整理成中文人物故事，并送到 Notion `07 · 海外创业志` 下供人工审核。

**边界：** `qjai-overseas-stories` 继续独立负责采集/翻译/结构化入库；本项目不写数据库、不创建微信公众号/CMS 草稿、不调用微信公众号/CMS API、不写 READY 标记、不定义生产定时任务。`mp-publisher`、`cms-publisher` 仍是彼此独立的投递器。

## 前置条件

- Python 3.11+，`pip install -e '.[test]'`。
- 单独创建 SELECT-only Postgres 角色，限定 `overseas_stories.stories`、`facts`、`story_localizations`、`localized_facts` 视图；通过 `DATABASE_URL` 连接 Supabase，推荐 TLS 验证。程序仍主动启动只读事务。
- `OPENAI_COMPAT_BASE_URL`、`OPENAI_COMPAT_API_KEY`、`OPENAI_COMPAT_MODEL` 指向**已批准**的 OpenAI-compatible 模型服务（如现有 OmniRoute），不会自己创建新 Agent。
- `NOTION_API_KEY` 只赋予选定内容父页面的插入权限，`NOTION_PARENT_PAGE_ID` 必须通过私有环境配置传入，仓库不包含真实页面 ID。
- 不要把 `.env`、本地成稿 JSON 或数据库凭据提交到 GitHub。

## 人工安全工作流

```bash
overseas-story-producer generate \
  --story-id '00000000-0000-4000-8000-000000000000' \
  --period 2026-11-09 --output outputs/2026-11-09.json

overseas-story-producer inspect --input outputs/2026-11-09.json

# 默认仅模拟：查询 07 的唯一性、不创建任何页面
overseas-story-producer submit --input outputs/2026-11-09.json

# 允许后，才创建“待人工验收”页面，绝不添加 READY
overseas-story-producer submit --input outputs/2026-11-09.json --commit
```

工具会拒绝覆盖本地成稿、拒绝重复期号/来源键、阻止无证据事实 ID、阻止缺失原始来源、阻止包含 READY 的成稿；Notion POST 超时或结果不确定时 **不自动重试**。

## 编辑与发布分工

1. 编辑人工检查人物情节/真实性归属/数据引用/著作权，使用组织内部 M7 编辑质量规范。本地 QA 只是第一道启发式筛查，不是事实核验或最终评分。
2. 人工确认后，按照两个发布器的 README，在页面末尾添加与 Notion 实际 Markdown 相符的 `QJAI_PUBLISH_READY v=1 source_key=overseas-stories:YYYY-MM-DD content_sha256=<64位SHA256>`。MD 标准化为 `rstrip()+"\n"` 后计算摘要。标记必须是正文最后一行；任何后续修改都要求重新签署。
3. 手工触发 `cms-publisher --profile overseas --period YYYY-MM-DD` 和 `mp-publisher` 对应的单期 `overseas` 命令；两者默认均关闭，必须分别检查 CMS 分类与微信封面、状态、密钥及 IP 白名单。
4. 编辑人员确认 CMS 草稿与微信公众号草稿内容无误后，再由人操作发布。

## 数据质量与安全限制

- 每篇案例至少含 5 条有效、已翻译、带证据的结构化事实，AI 返回至少 5 个证据 UUID。
- AI 必须叙述 **人物、困难、选择与转折**，不得将作者自述变成独立核验事实，不得虚构对话与具体数字。
- 每篇调用模型两次（成稿 + 独立审阅），第二次按 M7 六维 100 分标准提供逐项分数、错误点及硬性问题；低于 85 分或有不可追溯断言不可提交 Notion。两个模型调用的费用与延迟需在现网模型上先验收。
- 自动检查标题、章节、来源、字数、证据 ID、READY 泄漏；模型二次审阅不能替代人工查阅来源或确认版权。
- 一个 `YYYY-MM-DD` 期号只对应一个 Notion 子页面；故事中包含主来源 URL 与案例/事实 UUID 供溯源。
- 读取时严格复算 M4 `story_source_hash`、使用只返回最新翻译事实的 `localized_facts` 视图，源数据或翻译过期则拒绝生成。
- **未完成的上线前验收项**：复核现有模型成本/合规、人工抽检样稿、将代码入新独立 GitHub 仓库并运行 CI、服务器运行账号与调度错峰。

## 本地回归

```bash
python -m pytest -q
```

## 容器（手动运行，不开启定时任务）

```bash
docker build -t overseas-story-producer:local .
# 由运维在硅谷服务器私有路径准备 .env 和 UID 10001 可写的输出目录。
docker run --rm --env-file /opt/overseas-story-producer/.env \
  --mount type=bind,src=/opt/overseas-story-producer/outputs,dst=/outputs \
  overseas-story-producer:local generate \
  --story-id '<case-uuid>' --period YYYY-MM-DD --output /outputs/YYYY-MM-DD.json
```

Dockerfile 默认使用非 root UID 10001。此项目暂不包含自动调度；首次模型、Supabase 和 Notion 端到端验收前不得启用任何定时任务。

