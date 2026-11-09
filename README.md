# 海外创业志 · 独立人物故事生产器（M7）

此仓库负责将 `overseas_stories` 中的**中文结构化案例与证据**批量生成中文人物故事。正式生产模式通过自动质量门槛后，直接在 Notion `07 · 海外创业志` 建立并签署 READY 文章；人工审核只用于此前的样稿测试，不再是常规上线前置步骤。

**边界：** `qjai-overseas-stories` 继续独立负责采集/翻译/结构化入库；本项目不写 Supabase、不创建微信公众号/CMS 草稿、不调用微信公众号/CMS API。只在通过自动审稿后写入 Notion 正文及 READY 签名；`mp-publisher`、`cms-publisher` 仍是彼此独立的草稿投递器。

## M8 常规自动生产（周批量生成、每日按序投递）

GitHub Actions `production.yml` 每周一 **北京时间 06:45** 运行一次（晚于采集/索引 03:30），定时批量生产。手动 dispatch 的 `mode=plan` 只读，`mode=submit` 由操作者明确授权后批量生产。业主通过 `[backfill]` 提交标记可一次性触发历史库存回填；普通 push 不发起模型生成。

1. `overseas-story-producer plan` 读取评分不低于 70、至少有 5 条中文本地化事实、状态不属于 `rejected/handed_off` 的案例；核对正式 Notion 07 一级页所有期号与来源 UUID。排除已生成案例及高置信度同源重复，按优先 UUID、评分和 UUID 稳定排序。一个创业者讲述不同产品/事件不会仅因名字相同被排除。
2. `overseas-story-producer batch --commit` 将**全部符合条件且未写入的历史/新增案例**依序生成；不限制一周只生产一篇、不依赖过去 7 天的日期窗口、没有两篇待审积压限制。上次失败或部分完成的案例下一次仍可继续处理，缺号、重复期号、无来源 UUID 或存在未签署的正式期号则安全停止。
3. 每篇经过作者生成与独立自动审阅（双模型调用、结构及证据检查）后写入 Notion，并以 **Notion 实际回读的 Markdown** 计算 SHA-256、追加唯一 `QJAI_PUBLISH_READY` 行，再次回读校验。**不需要人工审核，也不直接调用 CMS/微信 API，更不会自动公开发布。**自动质量未通过则不写稿；限流/超时或 Notion 写入结果不确定时停止整批，不盲目重试。
4. `cms-publisher` 与 `mp-publisher` 分别每天北京时间 **07:30** 从 READY 库存读取最早一篇未投递文章，各自只写**一个草稿**；是否正式公开发表仍由渠道运营决定。库存不足时安全跳过，不复用旧文章。GitHub Actions 一次最长执行 350 分钟，批处理最多使用 320 分钟；超时预算未处理完可再次 dispatch `submit` 从 Notion 库存安全续跑。
5. 继续复用 `m7-integration` 环境中已有的 `M7_DATABASE_URL`、`M7_SUPABASE_CA_PEM`、`M7_NOTION_API_KEY`、`M7_NOTION_PARENT_PAGE_ID`、`M7_OMNIROUTE_API_KEY`。数据库仍必须是只读角色、`sslmode=verify-full`；运行日志不输出正文或密钥。`M7_E2E_ACTIVE` 保持关闭，不恢复旧的临时测试页。

实施后先核对 Actions 的只读选题数量，再启动一次受控历史回填；以实际 Actions 日志及 Notion 页面为验收依据，不能以 PR 测试代替现网验收。

## 前置条件

- Python 3.11+，`pip install -e '.[test]'`。
- 单独创建 SELECT-only Postgres 角色，限定 `overseas_stories.stories`、`facts`、`story_localizations`、`localized_facts` 视图；通过 `DATABASE_URL` 连接 Supabase，推荐 TLS 验证。程序仍主动启动只读事务。
- `OPENAI_COMPAT_BASE_URL`、`OPENAI_COMPAT_API_KEY`、`OPENAI_COMPAT_MODEL` 指向**已批准**的 OpenAI-compatible 模型服务（如现有 OmniRoute），不会自己创建新 Agent。URL 可写 `/v1` 基础地址或完整 `/v1/chat/completions`，程序不会重复追加路径。默认仅允许 HTTPS；若需要复用受控 Docker 私网中的 HTTP OmniRoute，须显式设 `OPENAI_COMPAT_ALLOW_PRIVATE_HTTP=true`，且目标主机必须是受允许的本地别名或私网 IP。公网 HTTP 始终被拒绝。
- `NOTION_API_KEY` 只赋予选定内容父页面的插入权限，`NOTION_PARENT_PAGE_ID` 必须通过私有环境配置传入，仓库不包含真实页面 ID。
- 不要把 `.env`、本地成稿 JSON 或数据库凭据提交到 GitHub。

## 历史手工测试与紧急修复工作流（不是正常生产）

```bash
overseas-story-producer generate \
  --story-id '00000000-0000-4000-8000-000000000000' \
  --issue-number 01 --output outputs/01.json

overseas-story-producer inspect --input outputs/01.json

# 默认仅模拟：查询 07 的唯一性、不创建任何页面
overseas-story-producer submit --input outputs/01.json

# 允许后，才创建“待人工验收”页面，绝不添加 READY
overseas-story-producer submit --input outputs/01.json --commit
```

工具会拒绝覆盖本地成稿、拒绝重复期号/来源键、阻止无证据事实 ID、阻止缺失原始来源、阻止包含 READY 的成稿；Notion POST 超时或结果不确定时 **不自动重试**。

## 编辑与发布分工

1. 正式生成自动执行结构、来源归属、重大编造和编辑质量的检查，但不会把创业者自述当成独立核验事实；低于自动门槛的内容不能进入 READY 库存。
2. 正式批量生成器自动追加 `QJAI_PUBLISH_READY v=1 source_key=overseas-stories:NN content_sha256=<64位SHA256>`，摘要以 Notion 实际 Markdown 的 `rstrip()+"\n"` 计算。任何后续正文修改都会令两个发布器拒绝该稿，不能继续使用旧摘要。
3. `cms-publisher` 与 `mp-publisher` 使用各自独立的已授权日常调度、投递账本、封面、CMS 分类和微信 IP 白名单；每天各读取最早一篇未交付的 READY 文章，不需要人工逐期指定。
4. 本生产流程只交付两端草稿，不自动将内容对公众正式发表。

## 数据质量与安全限制

- 每篇案例至少含 5 条有效、已翻译、带证据的结构化事实，AI 返回至少 5 个证据 UUID。
- AI 必须叙述 **人物、困难、选择与转折**，不得将作者自述变成独立核验事实，不得虚构对话与具体数字。
- 每篇调用模型两次（成稿 + 独立审阅），第二次按 M7 六维 100 分标准提供逐项分数、来源偏差警告及严重编造判断。**这不是独立事实核验**：创业者公开自述可忠实转述，即使无法外部证实也不因此阻断。评分低于 85、结构检查不通过、审稿格式无效或发现严重编造（`hard_fail=true`）才阻止 Notion 待审交接。普通 `unsupported_claims` 保留为编辑警告，不再一票否决。
- 严重编造包括虚构人物、重大事件、直接引语或收入数字，以及足以改变故事结论的虚构因果/时间线；普通描述偏差（例如太阳能板位置、菜单层级）保留为模型警告。旧手工 `generate/submit` 仍只创建 `PENDING_HUMAN_REVIEW` 的测试产物并可写内部编辑备注；新的正式 `batch --commit` 通过自动审稿后直接签署 READY，不附加内部备注到文章正文。公开 GitHub Actions 日志只暴露数量，不公开全文。
- 复用现有 OmniRoute 时，为避免首轮生成消耗模型速率预算，首次成功返回后默认等待 **90 秒**再发起独立质检；可通过 `OPENAI_COMPAT_REVIEW_COOLDOWN_SECONDS` 配置 0–600 秒（测试可置 0）。这不是重试：任何一次 HTTP 429 / 超时均立即停止，不会自动再次发送请求；需先检查网关限流规则再人工决定是否重新启动。
- `OPENAI_COMPAT_TIMEOUT_SECONDS` 默认 `120` 秒，可配置 `10`–`300` 秒；分别输出 connect/read/write/pool 超时错误，并标记 author/reviewer 阶段，绝不记录完整请求、URL 或密钥。**此开关不执行任何重试，修改超时前须先核实 OmniRoute 上游超时层级及主机资源；MVP 暂不自行调整现网默认值。**

- **模型传输诊断（2026-11-09）**：Author / Reviewer 每次模型 HTTP 调用在 stderr 输出一条固定结构的 JSON 诊断摘要，字段仅包含阶段（author/reviewer）、结果、HTTP 状态码、Content-Type 分类（sse/json/other/missing）、模型传输模式、总耗时、首个应用层数据块耗时、最后一个数据块耗时、接收的解码后字节数、数据块数、SSE 帧数、是否收到显式 [DONE]、允许列表内的异常类别（例如 RemoteProtocolError、ReadError、ConnectError、ReadTimeout）。**不输出模型请求/回复正文、Prompt、推理内容、URL、Headers、密钥、用户/故事 UUID，也不输出异常消息原文**。首个数据块时间不是底层 TCP 首包时间，也不能独立证明上游完成；HTTP 200 不代表 SSE 完整，只有 [DONE] 可作为当前流完整性标记。
- **诊断不改变失败处理策略**：HTTP 429、网络错误、超时仍按原有规则失败终止，不自动重试生成或修改 Notion；保留现有 JSON 格式异常的有限重试。运行中的结构化日志可以辅助判别客户端读中断、上游提前结束和 SSE 缺失，但无法凭日志单独确定 Cloudflare 或 Runner 哪一端发起了断开。
- 自动检查标题、章节、来源、字数、证据 ID、READY 泄漏；模型二次审阅不能替代人工查阅来源或确认版权。
- 一个不依赖日期的序号（`01`、`02`、...）只对应一个 Notion 子页面；标题为 `海外创业志 01|自然中文人物故事标题`，唯一来源键为 `overseas-stories:01`。故事中包含主来源 URL 与案例/事实 UUID 供溯源。
- 读取时严格复算 M4 `story_source_hash`、使用只返回最新翻译事实的 `localized_facts` 视图，源数据或翻译过期则拒绝生成。
- **正式上线核验**：检查每周 GitHub Actions 批次、模型限流与成本、Notion READY 回读及两个独立的每日草稿投递记录；人工抽检属于可选运营质量监控，不是逐篇人工审批。

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
  --story-id '<case-uuid>' --issue-number 01 --output /outputs/01.json
```

Dockerfile 默认使用非 root UID 10001。此项目暂不包含自动调度；首次模型、Supabase 和 Notion 端到端验收前不得启用任何定时任务。


## 自动化测试（GitHub Actions）

测试不使用手工 `workflow_dispatch`，也不把人工执行 CLI 当作 CI 验收：

| 测试层 | 触发器 | 凭据与外部影响 | 成功标准 |
| --- | --- | --- | --- |
| `ci.yml` | 每次 PR / push | 不接触密钥、无外部业务写入 | 全量离线测试通过（含网关模拟验证） |
| `public-gateway-smoke.yml` | 每次 `main` push + 北京时间每天 05:17 | 无密钥；一次 HTTPS GET，不调用模型 | 系统 TLS 验证正常、目标域名应用认证返回 HTTP 401 |
| `automatic-e2e.yml` | 涉及生产源码的 `main` push（周度测试 schedule 暂停） | 在信任的 `main` Runner 上调用 Supabase 只读、已有 OmniRoute 两次模型调用及 Notion 写入；只在独立的 `M7 自动化验收测试区` 创建待人工审核页面 | 生成成功、M7 独立审稿 ≥85、无严重编造、inspect 通过、Notion 测试页创建及回执校验通过 |

**注意：**网关 HTTP 401 成功，只证明公开路由、TLS、无凭据时的认证边界，不等于模型实际推理通过。GitHub Actions 的完整 E2E 仅在仓库变量 `M7_E2E_ENABLED=true` 且 `M7_E2E_ACTIVE=true` 后自动执行；未启用时其 job 为 **skipped，绝非集成验收通过**。这两个开关避免测试已结束或公开仓库尚未配置凭据时产生计费、泄漏、临时页面或反复失败。启用后不需要人工点击每次测试，但必须先一次性配置可信环境 `m7-integration` 与密钥。

**2026-11-09 转入正常生产流程：**隔离 M7 E2E 测试已结束，测试区已清理。为了避免遗留的 `M7_E2E_ENABLED=true` 在源码更新时重新创建临时页，E2E 现额外要求显式仓库变量 `M7_E2E_ACTIVE=true` 才运行；缺省不运行。普通离线 CI 和公网网关 smoke 保持自动运行。将来需要重启集成测试时须先准备独立测试区并显式重新启用，不能把 E2E 当作正式文章生产。正式文章标题连同栏目期号需控制在 64 UTF-8 字节内；正式批次改由自动审稿后签署 READY。

一次性配置，不属于每次测试动作：

- GitHub Secrets：`M7_DATABASE_URL`、`M7_OMNIROUTE_API_KEY`、`M7_NOTION_API_KEY`、`M7_NOTION_PARENT_PAGE_ID`、`M7_SUPABASE_CA_PEM`。
- GitHub Variables：`M7_E2E_STORY_ID`（选定来源案例 UUID）、`M7_E2E_MODEL`（可选）、`M7_E2E_ENABLED`（全部准备完成才设 `true`）。
- `M7_DATABASE_URL` 必须设置 `sslmode=verify-full`，不要携带仅在硅谷主机存在的绝对路径 `sslrootcert`；CA 通过 Runner 临时文件与 `PGSSLROOTCERT` 注入。
- 保护 `main` 的代码变更审核权限和 `m7-integration` 环境的可访问分支。自动测试不配置每次运行必须批准的 Environment reviewer，否则又会引入人工执行环节。
- 公共仓库 PR 测试不使用生产密钥；CI 不使用 `pull_request_target`。真实 E2E 一次最多触发作者及审稿各一次请求，错误即停，不自动重试或补跑，不公开原文和 JSON Artifact。
- 定时 GitHub Actions 可能延迟；公开仓库在长期无活动时可能停用定时触发。定时执行状态需要独立监控。
- 历史 E2E 仍保留未签名测试稿机制，不代表正式批处理的自动签名流程。正常生产不需要逐篇人工审阅。

既有本地 `generate / inspect / submit` 命令仅供应急排障和编辑复核；正常验收使用上述自动化工作流。**自动 E2E 会在专用测试区写入真实 Notion 待审页或 QA 失败诊断页，但不等同正式生产投递上线。**


### 完整端到端验收的 Notion 写入边界

当 `M7_E2E_ENABLED=true` 时，自动 E2E 不再只做 Notion dry-run，而是执行真实的 `submit --commit --e2e-test`。首次运行会在 `NOTION_PARENT_PAGE_ID` 下创建唯一的 `M7 自动化验收测试区` 子页面；随后只在这个测试区内创建 `PENDING_HUMAN_REVIEW` 文章页，必要时创建并列的 `M7 编辑备注` 页面供人工审阅。为避免与正式期号 `01` 冲突，每次自动测试使用独立的 `90000 + GitHub run_number` 测试期号，仅写入测试区，不代表正式专栏期次。编辑备注包含 QA 意见，不包含完整正文，且不匹配发布器的文章标题前缀。它不会在正式 `07 · 海外创业志` 根目录直接创建测试文章，不写 READY，不触碰 CMS/公众号，也不自动归档/删除测试页。测试页留存以便检查，后续同一期号重跑会被唯一性保护拒绝。若 Notion POST 返回结果不确定，工作流失败且不自动重试；必须先核对 Notion 测试区再处理。


**E2E 调度保护（2026-11-08）：**首次 GitHub-hosted E2E 已实际触发，但作者请求在 120 秒 read timeout 后失败。为避免在已知上游无首字节问题尚未定位时每周重复消耗模型请求，暂时移除 weekly schedule；保持对 `src/**` 和 `pyproject.toml` 的 main push 自动触发。修复代码合并后仍会自动执行一次完整 E2E，不需要手工点击。公网网关 smoke 和无密钥离线 CI 继续照常运行。首次真实 E2E 的日志和边界见 M7 项目 Notion 文档。


### OmniRoute / MiniMax 长响应处理

生产器通过现有 OmniRoute 的 OpenAI-compatible Chat Completions 接口请求模型时，使用 `stream: true` 与 SSE。客户端逐帧收集 `choices[].delta.content`，忽略 reasoning 字段，仅在收到终止事件 `data: [DONE]` 后才解析 JSON；连接中断、缺失终止事件、格式错误或超过 800 KB 均失败关闭，不把部分内容当成完整稿件，不自动重试。保持 120 秒 read timeout，不通过延长客户端 timeout 绕过 Cloudflare Free 的 125 秒 Proxy Read Timeout。此修改需要在现网 `minimax-cn/MiniMax-M3` 路由上由自动 E2E 实测首字节和完整输出；如上游仍不流式或没有首字节，E2E 会失败并保留错误阶段。


### Model transport: SSE streaming

The producer requests OpenAI-compatible Chat Completions with `stream=true` and `Accept: text/event-stream`. For SSE responses it concatenates `choices[].delta.content` chunks, ignores reasoning-only fields, and accepts the result only after the terminal `[DONE]` event. An interrupted or malformed stream fails closed and is never submitted to Notion. For compatible gateways that ignore the stream preference and return a regular JSON completion, the producer retains the non-streaming JSON response fallback. The same bounded response size and per-read HTTP timeout apply.

Streaming is intended to let the client receive response bytes as they are generated, reducing idle time on the Cloudflare-proxied connection. It does **not** guarantee that a provider will emit its first token before Cloudflare's 125-second Free-plan proxy read timeout. The first post-merge E2E remains the required live verification; no model request is made by unit tests or CI.


### E2E QA failure diagnostics

When the full E2E fails structural validation, the 85-point editorial threshold, or the serious-fabrication gate, it writes a **diagnostic-only** Notion page inside the dedicated `M7 自动化验收测试区`. That page contains scores, issue labels, and the reviewer's unsupported-claim excerpts so the failure can be diagnosed without publishing or exposing the full manuscript in public GitHub logs. It is explicitly marked `QA_FAILED_DIAGNOSTIC_ONLY`; it is not a pending article, contains no full article body, has no READY marker, and is never sent to CMS or WeChat. A passing artifact may contain minor source-fidelity warnings; the pending-review article's body is kept free of internal QA notes, while an adjacent `M7 编辑备注` page holds the reviewer's feedback for a human editor. Only an artifact that passes the structural and independent editorial gates can be submitted as a `CREATED_PENDING_REVIEW` article page.
