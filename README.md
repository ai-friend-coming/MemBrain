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

接口文档：

```text
GET /docs
GET /openapi.json
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
- `facts`：命中的事实。
- `sessions`：相关会话摘要。

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
