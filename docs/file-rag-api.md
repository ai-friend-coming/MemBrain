# File RAG API

## 功能边界

File RAG 为外部 Chat 提供独立的文件解析、文本切块、Embedding 生成、pgvector 持久化和相关片段召回能力。它与现有长期记忆链路保持责任分离：

- `/api/memory` 继续负责对话消息写入、事实抽取、实体树和会话摘要。
- `/api/memory/search` 继续负责 `dataset + task` 范围内的长期记忆召回。
- `/api/file-libraries/*` 只处理用户上传文件，不创建 memory fact、session、entity 或 summary。

文件库只使用外部 `chat_id` 作为召回隔离键。一个 Chat 内可以存在多个文档；`document_id` 使用上游稳定附件 ID 标识单个文档，`content_sha256` 用于验证内容和实现重复请求幂等。

## V0 范围

- 支持 UTF-8 TXT、Markdown 和具有可提取文本层的 PDF。
- 同步完成解析、切块、Embedding 和持久化；文件体积及提取 token 总量受服务配置限制。
- 按固定 token 窗口切块，并保留相邻窗口 overlap。
- 复用 MemBrain 当前 Embedding 服务和 `EMBED_DIM`。
- 在当前 Chat 的全部文档 chunk 中执行精确 pgvector 余弦距离检索。
- 按请求 token 预算拼装 `<file_context>`，同时返回结构化 chunk 结果。

使用 `EMBED_BACKEND=mlx` 时，本地模型当前默认最大输入为 512 tokens，必须把
`FILE_RAG_CHUNK_TOKENS` 配置为不大于 `MLX_EMBED_MAX_LENGTH`，例如 500；HTTP
Embedding 服务应按实际模型输入上限调整 chunk 大小。

V0 不包含 OCR、Office/表格解析、后台任务、BM25、RRF、rerank、HNSW、查询改写或全文件分层摘要。

## 数据关系

`file_documents` 保存 Chat 下的文档索引事实，`chat_id + document_id` 唯一。`file_chunks` 保存文档切块、页码、token 数和 Embedding；删除文档时级联删除全部 chunk。

MemBrain 不保存原始文件。索引失败时上游可以重新提交同一份附件；相同 `chat_id + document_id + content_sha256` 的已完成索引直接返回幂等结果。

## HTTP API

### 索引文档

```http
PUT /api/file-libraries/{chat_id}/documents/{document_id}
Content-Type: multipart/form-data
```

表单字段：

- `file`：原始文件。
- `content_sha256`：上游计算的十六进制 SHA-256；MemBrain 必须重新计算并校验。

成功响应包含 `status`、文件元数据、chunk 数、提取 token 数和本次 Embedding trace。相同内容重复提交返回 `already_indexed`；同一文档 ID 对应不同内容时返回冲突。

示例：

```bash
curl -X PUT \
  -F "file=@./notes.md;type=text/markdown" \
  -F "content_sha256=$(shasum -a 256 ./notes.md | cut -d ' ' -f 1)" \
  http://localhost:8094/api/file-libraries/chat_123/documents/attachment_456
```

### 检索文件库

```http
POST /api/file-libraries/{chat_id}/search
Content-Type: application/json
```

```json
{
  "query": "用户最近几轮形成的检索问题",
  "top_k": 5,
  "max_tokens": 4000
}
```

响应返回：

- `packed_context`：可直接作为本轮临时模型上下文使用的文件片段。
- `packed_token_count`：实际拼装 token 数。
- `chunks`：按相关性排序的文档 ID、文件名、chunk 序号、页码、分数和正文。
- `trace`：本次 query Embedding 调用明细。

### 删除文件索引

```http
DELETE /api/file-libraries/{chat_id}/documents/{document_id}
DELETE /api/file-libraries/{chat_id}
```

单文档删除用于附件删除；Chat 级删除用于 Chat 生命周期结束时清理全部派生索引。

## 配置

```dotenv
FILE_RAG_MAX_FILE_BYTES=20971520
FILE_RAG_MAX_EXTRACTED_TOKENS=200000
FILE_RAG_CHUNK_TOKENS=800
FILE_RAG_CHUNK_OVERLAP_TOKENS=100
FILE_RAG_EMBED_BATCH_SIZE=10
FILE_RAG_TOP_K=5
FILE_RAG_MAX_TOP_K=20
FILE_RAG_MAX_CONTEXT_TOKENS=4000
```

## 上游接入契约

Companion 在调用 File RAG 前负责验证登录用户对 Chat 和附件的所有权。`chat_id` 只是检索隔离键，不是访问凭证；MemBrain API 必须部署在受信任的服务网络内。

索引与搜索结果都只影响文件知识库。上游应把 `packed_context` 作为本轮 `TemporaryMessages` 注入主模型，不得写入用户消息正文、`llm_context_suffix` 或 MemBrain 长期记忆。
