export interface Dataset {
  id: number
  name: string
  task_count: number
}

export interface Task {
  id: number
  task_id: string
  session_count: number
  qa_count: number
}

export interface Message {
  id: number
  position: number
  dia_id: string | null
  speaker: string
  content: string
  message_time: string | null
  message_time_raw: string | null
}

export interface ChatSession {
  id: number
  session_number: number
  session_time: string | null
  session_time_raw: string | null
  messages: Message[]
}

export interface QAPair {
  id: number
  question_id: string
  question: string
  answer: string
  category: string | null
  evidence: string[]
  options: Record<string, string> | null
  reasoning: string | null
}

export interface TaskDetail {
  id: number
  task_id: string
  sessions: ChatSession[]
  qa_pairs: QAPair[]
}

export interface MemoryFactRef {
  fact_id: number
  entity_id: string
  alias_text: string
}

export interface FactPageItem {
  id: number
  text: string
  batch_index: number | null
  fact_ts: string | null
  status?: string
}

export interface MemoryEntity {
  id: number
  entity_id: string
  canonical_ref: string
  desc: string
  aliases: string[]
  orphan_facts: FactPageItem[]
}

export interface MemoryTreeNode {
  id: number
  entity_id: string
  parent_id: number | null
  node_type: string
  fact_id: number | null
  description: string | null
  fact_text: string | null
  fact_batch_index: number | null
  fact_ts: string | null
  fact_status?: string
}

export interface MemoryGraph {
  entities: MemoryEntity[]
  fact_refs: MemoryFactRef[]
  tree_nodes: MemoryTreeNode[]
}

export interface FactsPage {
  total: number
  offset: number
  limit: number
  batch_options: number[]
  facts: FactPageItem[]
}

export interface SessionSummaryItem {
  session_number: number
  subject: string
  content: string
}

export interface SessionSummariesPage {
  total: number
  offset: number
  limit: number
  summaries: SessionSummaryItem[]
}

export interface RunTagInfo {
  run_tag: string
}
