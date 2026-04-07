import type { SessionDetail, SessionMetadata } from '@/types'
import type { Persona } from '@/utils/character'
import { API_BASE_URL } from '@/constants'

export class APIError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'APIError'
    this.status = status
  }
}

interface ApiRequestOptions {
  userId?: string
  personaId?: string
  method?: string
  headers?: Record<string, string>
  body?: string
  signal?: AbortSignal
}

async function apiRequest<T>(endpoint: string, options: ApiRequestOptions = {}): Promise<T> {
  const { userId, personaId, signal, ...fetchOptions } = options

  if (!userId && !personaId) {
    throw new APIError('User ID or Persona ID is required', 400)
  }

  const authHeaders: Record<string, string> = {}
  if (personaId) {
    authHeaders['X-Persona-ID'] = personaId
  }
  else if (userId) {
    authHeaders['X-User-ID'] = userId
  }

  const requestOptions: RequestInit = {
    ...fetchOptions,
    signal,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders,
      ...fetchOptions.headers,
    },
  }

  try {
    const response = await fetch(`${API_BASE_URL}${endpoint}`, requestOptions)

    if (response.status === 204) {
      return null as T
    }

    const result = await response.json()

    if (!response.ok) {
      console.error('API Error:', result)
      const errorMessage = typeof result.detail === 'string'
        ? result.detail
        : JSON.stringify(result.detail)
      throw new APIError(errorMessage || 'Request failed', response.status)
    }

    return result as T
  }
  catch (error) {
    if (error instanceof APIError)
      throw error
    console.error('Network error:', error)
    throw new APIError((error as Error).message || 'Network error', 0)
  }
}

export async function createSession(personaId: string): Promise<{ id: string }> {
  return apiRequest<{ id: string }>('/sessions', {
    personaId,
    method: 'POST',
  })
}

export async function listSessions(personaId: string): Promise<SessionMetadata[]> {
  return apiRequest<SessionMetadata[]>('/sessions', {
    personaId,
    method: 'GET',
  })
}

export async function getSession(sessionId: string, personaId: string): Promise<SessionDetail> {
  return apiRequest<SessionDetail>(`/sessions/${sessionId}`, {
    personaId,
    method: 'GET',
  })
}

export async function deleteSession(sessionId: string, personaId: string): Promise<null> {
  return apiRequest<null>(`/sessions/${sessionId}`, {
    personaId,
    method: 'DELETE',
  })
}

export async function updateSessionTitle(sessionId: string, personaId: string, title: string): Promise<{ title: string }> {
  return apiRequest<{ title: string }>(`/sessions/${sessionId}/title`, {
    personaId,
    method: 'PATCH',
    body: JSON.stringify({ title }),
  })
}

export async function deleteUserData(personaId: string): Promise<null> {
  return apiRequest<null>('/user/data', {
    personaId,
    method: 'DELETE',
  })
}

export interface CharacterPreset {
  id: string
  label: string
  alias: string
  biography: string
}

export async function listCharacters(userId: string): Promise<CharacterPreset[]> {
  return apiRequest<CharacterPreset[]>('/characters', { userId, method: 'GET' })
}

export interface ImportCharacterResult {
  character_name: string
  character_bio: string
  neta_uuid?: string
  avatar_img?: string
  header_img?: string
}

export async function importCharacter(userId: string, url: string, signal?: AbortSignal): Promise<ImportCharacterResult> {
  return apiRequest<ImportCharacterResult>('/characters/import', {
    userId,
    method: 'POST',
    body: JSON.stringify({ url }),
    signal,
  })
}

interface PersonaCreateData {
  user_alias: string
  character_name: string
  character_biography: string
  neta_uuid?: string
  avatar_img?: string
  header_img?: string
  llm_api_url?: string
  llm_api_key?: string
}

function toPersona(raw: any): Persona {
  return {
    id: raw.id,
    userAlias: raw.user_alias,
    characterName: raw.character_name,
    characterBiography: raw.character_biography,
    netaUuid: raw.neta_uuid,
    avatarImg: raw.avatar_img,
    headerImg: raw.header_img,
    characterReflection: raw.character_reflection,
    userReflection: raw.user_reflection,
    llmApiUrl: raw.llm_api_url,
    llmApiKey: raw.llm_api_key,
    createdAt: raw.created_at,
  }
}

export async function listPersonas(userId: string): Promise<Persona[]> {
  const raw = await apiRequest<any[]>('/personas', { userId, method: 'GET' })
  return raw.map(toPersona)
}

export async function createPersona(userId: string, data: PersonaCreateData): Promise<Persona> {
  const raw = await apiRequest<any>('/personas', {
    userId,
    method: 'POST',
    body: JSON.stringify(data),
  })
  return toPersona(raw)
}

export async function deletePersona(userId: string, personaId: string): Promise<null> {
  return apiRequest<null>(`/personas/${personaId}`, { userId, method: 'DELETE' })
}

export async function updatePersonaLLM(userId: string, personaId: string, llmApiUrl: string, llmApiKey: string): Promise<Persona> {
  const raw = await apiRequest<any>(`/personas/${personaId}/llm`, {
    userId,
    method: 'PATCH',
    body: JSON.stringify({ llm_api_url: llmApiUrl || null, llm_api_key: llmApiKey || null }),
  })
  return toPersona(raw)
}
