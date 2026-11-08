# 海外创业志 · 独立人物故事生产器（M7）

此仓库只负责将 `overseas_stories` 中的**中文结构化案例与证据**整理成中文人物故事，并送到 Notion `07 · 海外创业志` 下供人工审核。

**边界：** `qjai-overseas-stories` 继续独立负责采集/翻译/结构化入库；本项目不写数据库、不创建微信公众号/CMS 草稿、不调用微信公众号/CMS API、不写 READY 标记、不定义生产定时任务。`mp-publisher`、`cms-publisher` 仍是彼此独立的投递器。

## 前置条件

- Python 3.11+，`pip install -e '.[test]'`。
- 单独创建 SELECT-only Postgres 角色，限定 `overseas_stories.stories`、`facts`、`story_localizations`、`localized_facts` 视图；通过 `DATABASE_URL` 连接 Supabase，推荐 TLS 验证。程序仍主动启动只读事务。
- `OPENAI_COMPAT_BASE_URL`、`OPENAI_COMPAT_API_KEY`、`OPENAI_COMPAT_MODEL` 指向**已批准**的 OpenAI-compatible 模型服务（如现有 OmniRoute），不会自己创建新 Agent。URL 可写 `/v1` 基础地址或完整 `/v1/chat/completions`，程序不会重复追加路径。默认仅允许 HTTPS；若需要复用受控 Docker 私网中的 HTTP OmniRoute，须显式设 `OPENAI_COMPAT_ALLOW_PRIVATE_HTTP=true`，且目标主机必须是受允许的本地别名或私网 IP。公网 HTTP 始终被拒绝。
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
- 复用现有 OmniRoute 时，为避免首轮生成消耗模型速率预算，首次成功返回后默认等待 **90 秒**再发起独立质检；可通过 `OPENAI_COMPAT_REVIEW_COOLDOWN_SECONDS` 配置 0–600 秒（测试可置 0）。这不是重试：任何一次 HTTP 429 / 超时均立即停止，不会自动再次发送请求；需先检查网关限流规则再人工决定是否重新启动。
- `OPENAI_COMPAT_TIMEOUT_SECONDS` 默认 `120` 秒，可配置 `10`–`300` 秒；分别输出 connect/read/write/pool 超时错误，并标记 author/reviewer 阶段，绝不记录完整请求、URL 或密钥。**此开关不执行任何重试，修改超时前须先核实 OmniRoute 上游超时层级及主机资源；MVP 暂不自行调整现网默认值。**
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


## 自动化测试（GitHub Actions）

测试不使用手工 `workflow_dispatch`，也不把人工执行 CLI 当作 CI 验收：

| 测试层 | 触发器 | 凭据与外部影响 | 成功标准 |
| --- | --- | --- | --- |
| `ci.yml` | 每次 PR / push | 不接触密钥、无外部业务写入 | 全量离线测试通过（含网关模拟验证） |
| `public-gateway-smoke.yml` | 每次 `main` push + 北京时间每天 05:17 | 无密钥；一次 HTTPS GET，不调用模型 | 系统 TLS 验证正常、目标域名应用认证返回 HTTP 401 |
| `automatic-e2e.yml` | 涉及生产源码的 `main` push（当前暂停周度 schedule，待上游首字节延迟修复后恢复） | 在信任的 `main` Runner 上调用 Supabase 只读、已有 OmniRoute 两次模型调用及 Notion 写入；只在独立的 `M7 自动化验收测试区` 创建待人工审核页面 | 生成成功、M7 独立审稿 ≥85、inspect 通过、Notion 测试页创建及回执校验通过 |

**注意：**网关 HTTP 401 成功，只证明公开路由、TLS、无凭据时的认证边界，不等于模型实际推理通过。GitHub Actions 的完整 E2E 仅在仓库变量 `M7_E2E_ENABLED=true` 后自动执行；未启用时其 job 为 **skipped，绝非集成验收通过**。此开关避免公开仓库尚未配置凭据时产生计费、泄漏或反复失败。启用后不需要人工点击每次测试，但必须先一次性配置可信环境 `m7-integration` 与密钥。

一次性配置，不属于每次测试动作：

- GitHub Secrets：`M7_DATABASE_URL`、`M7_OMNIROUTE_API_KEY`、`M7_NOTION_API_KEY`、`M7_NOTION_PARENT_PAGE_ID`、`M7_SUPABASE_CA_PEM`。
- GitHub Variables：`M7_E2E_STORY_ID`（选定来源案例 UUID）、`M7_E2E_MODEL`（可选）、`M7_E2E_ENABLED`（全部准备完成才设 `true`）。
- `M7_DATABASE_URL` 必须设置 `sslmode=verify-full`，不要携带仅在硅谷主机存在的绝对路径 `sslrootcert`；CA 通过 Runner 临时文件与 `PGSSLROOTCERT` 注入。
- 保护 `main` 的代码变更审核权限和 `m7-integration` 环境的可访问分支。自动测试不配置每次运行必须批准的 Environment reviewer，否则又会引入人工执行环节。
- 公共仓库 PR 测试不使用生产密钥；CI 不使用 `pull_request_target`。真实 E2E 一次最多触发作者及审稿各一次请求，错误即停，不自动重试或补跑，不公开原文和 JSON Artifact。
- 定时 GitHub Actions 可能延迟；公开仓库在长期无活动时可能停用定时触发。定时执行状态需要独立监控。
- 所有生产稿仍需人工审阅才能写 `QJAI_PUBLISH_READY`；此要求是出版审批，不是测试执行前置条件。

既有本地 `generate / inspect / submit` 命令仅供应急排障和编辑复核；正常验收使用上述自动化工作流。**本版自动 E2E 是只读/无 Notion 写入的联通与质量测试，不等同正式生产投递已经上线。**


### 完整端到端验收的 Notion 写入边界

当 `M7_E2E_ENABLED=true` 时，自动 E2E 不再只做 Notion dry-run，而是执行真实的 `submit --commit --e2e-test`。首次运行会在 `NOTION_PARENT_PAGE_ID` 下创建唯一的 `M7 自动化验收测试区` 子页面；随后只在这个测试区内创建当日期号的 `PENDING_HUMAN_REVIEW` 文章页。它不会在正式 `07 · 海外创业志` 根目录直接创建文章，不写 READY，不触碰 CMS/公众号，也不自动归档/删除测试页。测试页留存以便检查，后续同一期号重跑会被唯一性保护拒绝。若 Notion POST 返回结果不确定，工作流失败且不自动重试；必须先核对 Notion 测试区再处理。


**E2E 调度保护（2026-11-08）：**首次 GitHub-hosted E2E 已实际触发，但作者请求在 120 秒 read timeout 后失败。为避免在已知上游无首字节问题尚未定位时每周重复消耗模型请求，暂时移除 weekly schedule；保持对 `src/**` 和 `pyproject.toml` 的 main push 自动触发。修复代码合并后仍会自动执行一次完整 E2E，不需要手工点击。公网网关 smoke 和无密钥离线 CI 继续照常运行。首次真实 E2E 的日志和边界见 M7 项目 Notion 文档。


### OmniRoute / MiniMax 长响应处理

生产器通过现有 OmniRoute 的 OpenAI-compatible Chat Completions 接口请求模型时，使用 `stream: true` 与 SSE。客户端逐帧收集 `choices[].delta.content`，忽略 reasoning 字段，仅在收到终止事件 `data: [DONE]` 后才解析 JSON；连接中断、缺失终止事件、格式错误或超过 800 KB 均失败关闭，不把部分内容当成完整稿件，不自动重试。保持 120 秒 read timeout，不通过延长客户端 timeout 绕过 Cloudflare Free 的 125 秒 Proxy Read Timeout。此修改需要在现网 `minimax-cn/MiniMax-M3` 路由上由自动 E2E 实测首字节和完整输出；如上游仍不流式或没有首字节，E2E 会失败并保留错误阶段。


### Model transport: SSE streaming

The producer requests OpenAI-compatible Chat Completions with `stream=true` and `Accept: text/event-stream`. For SSE responses it concatenates `choices[].delta.content` chunks, ignores reasoning-only fields, and accepts the result only after the terminal `[DONE]` event. An interrupted or malformed stream fails closed and is never submitted to Notion. For compatible gateways that ignore the stream preference and return a regular JSON completion, the producer retains the non-streaming JSON response fallback. The same bounded response size and per-read HTTP timeout apply.

Streaming is intended to let the client receive response bytes as they are generated, reducing idle time on the Cloudflare-proxied connection. It does **not** guarantee that a provider will emit its first token before Cloudflare's 125-second Free-plan proxy read timeout. The first post-merge E2E remains the required live verification; no model request is made by unit tests or CI.
