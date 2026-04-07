export interface User {
  id: string
  loginTime: number
}

export interface SessionMetadata {
  id: string
  title: string
  createdAt: string
  updatedAt: string
}

export interface SessionDetail extends SessionMetadata {
  messages: Message[]
}

export interface MessagePart {
  type: string
  text: string
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content?: string
  parts?: MessagePart[]
  timestamp?: string
}
