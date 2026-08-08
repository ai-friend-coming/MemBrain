# MemBrain

## 1. 库简单介绍

MemBrain 是一个面向 Agent / Chatbot 的长期记忆后端：写入多轮对话，后台抽取事实、实体和会话摘要；查询时检索相关记忆，并返回可直接注入 prompt 的 `packed_context`。

核心依赖：

- `FastAPI`：HTTP API。
- `ParadeDB`：存储消息、事实、实体树、摘要和向量索引。
- LLM API：事实抽取、query 改写、多 query 扩展。
- Embedding API：向量检索。
- Rerank API：仅 `strategy="rerank"` 时需要。

## 2. API 协议相关

Docker 部署默认地址：

```text
http://localhost:8094
```

### 写入记忆

```http
POST /api/memory
```

最小请求：

```json
{
  "dataset": "user_123",
  "task": "persona_alice",
  "chat_id": "chat_987",
  "messages": [
    {
      "speaker": "User",
      "content": "I usually drink Dr. Pepper while debugging.",
      "message_time": "2026-04-26T10:00:00+08:00"
    }
  ],
  "store": true,
  "digest": true,
  "wait_for_digest": true
}
```

关键字段：

- `dataset` / `task`：记忆隔离键，建议按用户和角色空间稳定生成。
- `chat_id`：外部应用的聊天 ID，用于标记本次写入产生的消息、事实和会话摘要。
- `messages`：待写入消息。
- `store`：保存原始消息。
- `digest`：触发后台记忆构建。
- `wait_for_digest`：是否等待本次 digest 完成；`true` 时 response trace 包含 digest 的全部上游调用，默认 `false` 只入队。
- `agent_profile`：可选，任务级 Agent 画像。

典型响应：

```json
{
  "dataset_id": 1,
  "task_pk": 1,
  "session_id": 1,
  "session_number": 1,
  "digested_sessions": 1,
  "status": "stored_and_digested",
  "trace": {
    "duration_ms": 1234.5,
    "calls": [],
    "total_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    "estimated_cost_usd": null
  }
}
```

响应字段：

- `dataset_id`：内部数据集 ID。
- `task_pk`：内部任务主键 ID。
- `session_id`：本次新写入的会话 ID；当 `store=false` 时为 `null`。
- `session_number`：当前 task 下递增的会话序号；当 `store=false` 时为 `null`。
- `digested_sessions`：`wait_for_digest=true` 时本次同步完成的会话数；异步入队时为 `0`。
- `status`：处理状态。
- `trace`：本次 API 请求内所有上游 API 调用的耗时、HTTP 状态、真实 token usage、错误和估算成本；只在 response 中返回，不持久化。

`status` 可能值：

- `"stored"`：只保存原始消息，未触发 digest。
- `"stored_and_digest_queued"`：已保存原始消息，并已把 digest 放入后台队列。
- `"digest_queued"`：未写入新消息，只把已有未处理会话放入 digest 队列。
- `"stored_and_digested"`：已保存消息并同步完成 digest（`wait_for_digest=true`）。
- `"digested"`：未写入新消息并同步完成 digest（`wait_for_digest=true`）。
- `"stored_and_digest_failed"` / `"digest_failed"`：同步 digest 发生上游或处理异常；`trace.calls` 保留失败调用，已完成的部分可能已经落库。

注意：默认 `digest=true` 仍会立即入队返回；需要在同一 response 中拿到 digest usage 时传入 `wait_for_digest=true`。同步模式完成后新记忆即可被 `/api/memory/search` 检索到。

### 检索记忆

```http
POST /api/memory/search
```

最小请求：

```json
{
  "dataset": "user_123",
  "task": "persona_alice",
  "question": "What does the user usually drink while debugging?",
  "mode": "expand",
  "strategy": "rrf"
}
```

检索参数：

- `mode="direct"`：不做 LLM query 改写。
- `mode="expand"`：默认模式，LLM 改写 + 多路检索。
- `mode="reflect"`：在 `expand` 基础上增加一轮反思补检索。
- `strategy="rrf"`：无需 rerank 服务。
- `strategy="rerank"`：调用 rerank 服务重排候选事实。

检索接口不接收 `chat_id`，始终在当前 `dataset + task` 的全部记忆中检索。
当前会话或跨会话的取舍由下游根据 `facts[*].source_chat_ids` 判断。
`packed_context` 仍包含全量召回内容；下游筛选后应使用结构化 `facts` 和
`sessions` 重建需要注入模型的上下文。

核心响应字段：

- `packed_context`：给 Chatbot 注入 prompt 的最终记忆上下文。
- `packed_token_count`：上下文估算 token 数。
- `fact_ids`：进入 `packed_context` 的事实 ID 列表。
- `facts`：检索、融合、重排后的事实明细，按相关性排序。
- `sessions`：对当前问题有贡献的相关会话摘要。
- `raw_messages`：当前版本固定为空数组。

典型响应：

```json
{
  "packed_context": "## Relevant Episodes\n\n**Debugging and beverages**: Alice talked about drinking Dr. Pepper while debugging.\n---\n\n## Additional Facts\n- User usually drinks Dr. Pepper while debugging [2026-04-26]",
  "packed_token_count": 43,
  "fact_ids": [1],
  "facts": [
    {
      "fact_id": 1,
      "text": "User usually drinks Dr. Pepper while debugging",
      "source_chat_ids": ["chat_987"],
      "source": "bm25",
      "rerank_score": 0.91,
      "time_info": "2026-04-26",
      "entity_ref": "User",
      "aspect_path": "Habits > Beverages"
    }
  ],
  "sessions": [
    {
      "session_summary_id": 1,
      "session_id": 1,
      "chat_id": "chat_987",
      "subject": "Debugging and beverages",
      "content": "Alice talked about drinking Dr. Pepper while debugging.",
      "score": 0.82,
      "source": "fact_agg",
      "contributing_facts": 1
    }
  ],
  "raw_messages": [],
  "trace": {
    "duration_ms": 98.1,
    "calls": [],
    "total_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    "estimated_cost_usd": null
  }
}
```

`trace.calls[*]` 的 `kind` 包括 `llm`、`embedding`、`rerank`；上游没有返回 token usage 时对应值为 0。成本按公开的美元/百万 token 单价估算：`gpt-4.1-mini` 为输入 0.40 / 输出 1.60，`gpt-4.1` 为输入 2.00 / 输出 8.00，`text-embedding-3-large` 为输入 0.13。未知模型（例如当前 rerank 模型）的单次成本为 `null`，且不计入 trace 成本小计；代理平台实际账单可能不同。

`facts[*]` 字段：

- `fact_id`：事实 ID。
- `text`：事实文本，实体引用已尽量解析为可读文本。
- `source_chat_ids`：支持该事实的全部外部聊天 ID；下游可据此实现当前会话或跨会话业务规则。
- `source`：命中的检索路径，常见值包括 `"bm25"`、`"embed"`、`"tree"`、`"bm25_parsed"`。
- `rerank_score`：融合或 rerank 后的相关性分数。
- `time_info`：事实关联时间。
- `entity_ref`：事实归属的规范实体。
- `aspect_path`：事实在实体树中的路径。

`sessions[*]` 字段：

- `session_summary_id`：会话摘要 ID。
- `session_id`：原始会话 ID。
- `chat_id`：该内部会话所属的外部聊天 ID。
- `subject`：会话主题。
- `content`：会话摘要内容。
- `score`：会话相关性分数。
- `source`：会话命中来源，常见值包括 `"bm25"`、`"fact_agg"`。
- `contributing_facts`：贡献到该会话摘要的事实数量。

### File RAG

File RAG 是与长期记忆 add/recall 分离的文件知识库，只使用 `chat_id` 隔离文件，
不会创建 memory fact、session、entity 或 summary。V0 支持 UTF-8 TXT、Markdown
和文本型 PDF，索引流程同步完成解析、固定 token 切块、Embedding 和 pgvector
持久化。

索引文件：

```http
PUT /api/file-libraries/{chat_id}/documents/{document_id}
Content-Type: multipart/form-data
```

表单包含原始 `file` 和 64 位十六进制 `content_sha256`。同一
`chat_id + document_id + content_sha256` 重复上传会返回 `already_indexed`；同一文档
ID 上传不同内容会返回 `409`。

检索当前 Chat 文件库：

```http
POST /api/file-libraries/{chat_id}/search
Content-Type: application/json
```

```json
{
  "query": "项目的发布条件是什么？",
  "top_k": 5,
  "max_tokens": 4000
}
```

响应包含按相关性排序的 `chunks`，以及 token 预算内可临时注入主模型的
`packed_context`。删除接口为：

```http
DELETE /api/file-libraries/{chat_id}/documents/{document_id}
DELETE /api/file-libraries/{chat_id}
```

详细契约和配置见 [File RAG API](docs/file-rag-api.md)。`chat_id` 是隔离键而非访问
凭证，调用方必须先验证用户对 Chat 和附件的所有权，并把 MemBrain 部署在可信
服务网络中。

### Chatbot 接入

推荐映射：

```text
dataset = user_<owner_id>
task    = persona_<persona_id>
```

典型流程：

1. 用户和助手消息落库后调用 `POST /api/memory`，传入当前 `chat_id` 并使用 `store=true, digest=true`。
2. 回复前由上层 router 判断是否需要回忆。
3. 需要回忆时调用 `POST /api/memory/search`，下游按 `source_chat_ids` 应用会话范围规则，再把所需记忆注入回复 agent。

## 3. Docker 部署

镜像目标：干净的 `membrain-api` 微服务，只包含核心 API，不包含 demo、viewer、vLLM、benchmark 数据集和实验产物。

### 首次启动

```bash
cp .env.example .env
```

编辑 `.env`，至少确认：

```dotenv
LLM_API_URL=http://host.docker.internal:4000/v1
LLM_API_KEY=sk-1234
EMBED_SERVICE_URL=http://host.docker.internal:9113/v1/embeddings
EMBED_MODEL=qwen3-embed
EMBED_DIM=2560
```

Rerank 配置: 只有使用 `strategy="rerank"` 时需要：

```dotenv
RERANK_SERVICE_URL=http://host.docker.internal:9114/v1/rerank
RERANK_MODEL=qwen3-rerank
```

### 更新或重启

```bash
./update.sh
```

`update.sh` 会拉取远端镜像：

- 没有运行：直接启动。
- 镜像变更：替换 API 容器。
- 镜像相同：重启 API 容器。

### 一键启动模式

统一入口：

```bash
MEMBRAIN_PROFILE=thirdparty ENV_FILE=.env ./scripts/membrain-up.sh
```

可选模式：

- `thirdparty`：纯 Docker 部署，MemBrain API 调第三方 LLM / embedding / rerank API。
- `linux-local`：Linux 本地模型部署，MemBrain API 仍在 Docker 中运行，embedding / rerank 通过本机 HTTP 服务暴露。
- `mac-mlx`：Apple Silicon Mac 本地 MLX 部署，数据库跑 Docker，MemBrain API 在宿主机进程内通过 MLX/Metal 加载 0.6B embedding 与 rerank 模型。

Mac MLX 示例：

```bash
MEMBRAIN_PROFILE=mac-mlx ENV_FILE=.env_1 ./scripts/membrain-up.sh
```

停止：

```bash
MEMBRAIN_PROFILE=mac-mlx ENV_FILE=.env_1 ./scripts/membrain-down.sh
```

注意：MLX 依赖 macOS Metal，不能封装进 Linux Docker 容器内运行。Mac MLX 模式会自动启动 Docker 数据库、停止 Docker API 容器，并用 `screen` 在宿主机后台启动 MemBrain API。
