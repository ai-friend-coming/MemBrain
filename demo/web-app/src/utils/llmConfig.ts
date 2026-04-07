const KEY = 'membrain_llm_config'

export function getEnvLLMConfig(): LLMConfig | null {
  const apiKey = import.meta.env.LLM_API_KEY as string | undefined
if (!apiKey)
    return null
  return {
    apiKey,
    apiUrl: (import.meta.env.LLM_API_URL as string | undefined) || '',
  }
}

export interface LLMConfig {
  apiUrl: string
  apiKey: string
}

type ConfigDict = Record<string, LLMConfig>

export function getLLMConfig(userId: string): LLMConfig | null {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) {
      const dict: ConfigDict = JSON.parse(raw)
      if (dict[userId]?.apiKey)
        return dict[userId]
    }
  }
  catch {}
  return null
}

export function saveLLMConfig(userId: string, config: LLMConfig): void {
  let dict: ConfigDict = {}
  try {
    const raw = localStorage.getItem(KEY)
    if (raw)
      dict = JSON.parse(raw)
  }
  catch {}
  dict[userId] = config
  localStorage.setItem(KEY, JSON.stringify(dict))
}

export function hasLLMConfig(userId: string): boolean {
  return !!getLLMConfig(userId)
}
