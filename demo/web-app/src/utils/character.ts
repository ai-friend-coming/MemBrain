import { CHARACTERS_STORAGE_KEY, CURRENT_PERSONA_KEY } from '@/constants'

export interface CharacterDefaults {
  userAlias: string
}

export interface Persona {
  id: string
  userAlias: string
  characterName: string
  characterBiography: string
  netaUuid?: string
  avatarImg?: string
  headerImg?: string
  characterReflection?: string
  userReflection?: string
  llmApiUrl?: string
  llmApiKey?: string
  createdAt: string
}

type DefaultsDict = Record<string, CharacterDefaults>
type PersonaIdDict = Record<string, string>

export function getCharacterDefaults(userId: string): CharacterDefaults {
  try {
    const raw = localStorage.getItem(CHARACTERS_STORAGE_KEY)
    if (raw) {
      const parsed: DefaultsDict = JSON.parse(raw)
      if (parsed[userId])
        return parsed[userId]
    }
  }
  catch { }
  return { userAlias: userId }
}

export function saveCharacterDefaults(userId: string, defaults: CharacterDefaults): void {
  let dict: DefaultsDict = {}
  try {
    const raw = localStorage.getItem(CHARACTERS_STORAGE_KEY)
    if (raw)
      dict = JSON.parse(raw)
  }
  catch { }
  dict[userId] = defaults
  localStorage.setItem(CHARACTERS_STORAGE_KEY, JSON.stringify(dict))
}

export function getCurrentPersonaId(userId: string): string | null {
  try {
    const raw = localStorage.getItem(CURRENT_PERSONA_KEY)
    if (raw) {
      const parsed: PersonaIdDict = JSON.parse(raw)
      return parsed[userId] ?? null
    }
  }
  catch { }
  return null
}

export function setCurrentPersonaId(userId: string, personaId: string): void {
  let dict: PersonaIdDict = {}
  try {
    const raw = localStorage.getItem(CURRENT_PERSONA_KEY)
    if (raw)
      dict = JSON.parse(raw)
  }
  catch { }
  dict[userId] = personaId
  localStorage.setItem(CURRENT_PERSONA_KEY, JSON.stringify(dict))
}

export function clearCurrentPersonaId(userId: string): void {
  let dict: PersonaIdDict = {}
  try {
    const raw = localStorage.getItem(CURRENT_PERSONA_KEY)
    if (raw)
      dict = JSON.parse(raw)
  }
  catch { }
  delete dict[userId]
  localStorage.setItem(CURRENT_PERSONA_KEY, JSON.stringify(dict))
}
