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
  "messages": [
    {
      "speaker": "User",
      "content": "I usually drink Dr. Pepper while debugging.",
      "message_time": "2026-04-26T10:00:00+08:00"
    }
  ],
  "store": true,
  "digest": true
}
```

关键字段：

- `dataset` / `task`：记忆隔离键，建议按用户和角色空间稳定生成。
- `messages`：待写入消息。
- `store`：保存原始消息。
- `digest`：触发后台记忆构建。
- `agent_profile`：可选，任务级 Agent 画像。

典型响应：

```json
{
  "dataset_id": 1,
  "task_pk": 1,
  "session_id": 1,
  "session_number": 1,
  "digested_sessions": 0,
  "status": "stored_and_digest_queued"
}
```

响应字段：

- `dataset_id`：内部数据集 ID。
- `task_pk`：内部任务主键 ID。
- `session_id`：本次新写入的会话 ID；当 `store=false` 时为 `null`。
- `session_number`：当前 task 下递增的会话序号；当 `store=false` 时为 `null`。
- `digested_sessions`：当前固定返回 `0`，因为 digest 是后台异步执行。
- `status`：处理状态。

`status` 可能值：

- `"stored"`：只保存原始消息，未触发 digest。
- `"stored_and_digest_queued"`：已保存原始消息，并已把 digest 放入后台队列。
- `"digest_queued"`：未写入新消息，只把已有未处理会话放入 digest 队列。

注意：`digest=true` 时接口会在后台任务入队后立即返回；新写入记忆需要等 digest 完成后才能被 `/api/memory/search` 检索到。

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
      "subject": "Debugging and beverages",
      "content": "Alice talked about drinking Dr. Pepper while debugging.",
      "score": 0.82,
      "source": "fact_agg",
      "contributing_facts": 1
    }
  ],
  "raw_messages": []
}
```

`facts[*]` 字段：

- `fact_id`：事实 ID。
- `text`：事实文本，实体引用已尽量解析为可读文本。
- `source`：命中的检索路径，常见值包括 `"bm25"`、`"embed"`、`"tree"`、`"bm25_parsed"`。
- `rerank_score`：融合或 rerank 后的相关性分数。
- `time_info`：事实关联时间。
- `entity_ref`：事实归属的规范实体。
- `aspect_path`：事实在实体树中的路径。

`sessions[*]` 字段：

- `session_summary_id`：会话摘要 ID。
- `session_id`：原始会话 ID。
- `subject`：会话主题。
- `content`：会话摘要内容。
- `score`：会话相关性分数。
- `source`：会话命中来源，常见值包括 `"bm25"`、`"fact_agg"`。
- `contributing_facts`：贡献到该会话摘要的事实数量。

### Chatbot 接入

推荐映射：

```text
dataset = user_<owner_id>
task    = persona_<persona_id>
```

典型流程：

1. 用户和助手消息落库后调用 `POST /api/memory`，使用 `store=true, digest=true`。
2. 回复前由上层 router 判断是否需要回忆。
3. 需要回忆时调用 `POST /api/memory/search`，把 `packed_context` 注入回复 agent。

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
