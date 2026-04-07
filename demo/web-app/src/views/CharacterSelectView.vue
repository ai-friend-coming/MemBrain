<script setup lang="ts">
import type { CharacterPreset } from '@/services/api'
import type { Persona } from '@/utils/character'
import { Loader2, LogOut, Plus, Sparkles, Trash2, User as UserIcon } from 'lucide-vue-next'
import { onMounted, ref } from 'vue'
import momoAvatar from '@/assets/avatar/momo.jpeg'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import IconButton from '@/components/ui/IconButton.vue'
import { useToast } from '@/composables/useToast'
import { createPersona, deletePersona, importCharacter, listCharacters, listPersonas } from '@/services/api'
import { clearCurrentPersonaId, getCharacterDefaults, saveCharacterDefaults, setCurrentPersonaId } from '@/utils/character'
import { getCachedImageUrl } from '@/utils/imageCache'
import { getEnvLLMConfig, getLLMConfig, hasLLMConfig, saveLLMConfig } from '@/utils/llmConfig'

const props = defineProps<{
  userId: string
}>()

const emit = defineEmits<{
  select: [persona: Persona]
  switchUser: []
}>()

const { success, error: showError, info, warning } = useToast()

const personas = ref<Persona[]>([])
const loading = ref(true)
const avatarCache = ref<Record<string, string>>({})
const headerCache = ref<Record<string, string>>({})

const showCreateDialog = ref(false)
const creating = ref(false)

const userAlias = ref('')
const characterName = ref('')
const characterBiography = ref('')

const presets = ref<CharacterPreset[]>([])
const importUrl = ref('')
const importMeta = ref({ netaUuid: '', avatarImg: '', headerImg: '' })
const importLoading = ref(false)
const importExpanded = ref(false)
let importController: AbortController | null = null

// LLM config
const llmApiUrl = ref('')
const llmApiKey = ref('')
const useSeparateLLM = ref(false)

onMounted(async () => {
  await loadPersonas()
  await loadPresets()
})

function handleSwitchUser() {
  // eslint-disable-next-line no-alert
  if (window.confirm('Are you sure you want to switch user? This will log you out.')) {
    emit('switchUser')
  }
}

async function loadPersonas() {
  loading.value = true
  try {
    personas.value = await listPersonas(props.userId)
    // Preload images into cache refs
    await Promise.allSettled(personas.value.flatMap((p) => {
      const tasks: Promise<void>[] = []
      if (p.avatarImg) {
        tasks.push(getCachedImageUrl(p.avatarImg).then((url) => {
          if (url)
            avatarCache.value[p.id] = url
        }))
      }
      if (p.headerImg) {
        tasks.push(getCachedImageUrl(p.headerImg).then((url) => {
          if (url)
            headerCache.value[p.id] = url
        }))
      }
      return tasks
    }))
  }
  catch (err: any) {
    showError(`Failed to load characters: ${err.message}`)
  }
  finally {
    loading.value = false
  }
}

async function loadPresets() {
  try {
    presets.value = await listCharacters(props.userId)
  }
  catch (e) {
    console.warn('Failed to load character presets', e)
  }
}

function openCreateDialog() {
  const defaults = getCharacterDefaults(props.userId)
  userAlias.value = defaults.userAlias || props.userId
  characterName.value = ''
  characterBiography.value = ''
  importUrl.value = ''
  importMeta.value = { netaUuid: '', avatarImg: '', headerImg: '' }
  importExpanded.value = false
  // LLM: prefill with global config if exists, reset separate toggle
  const globalConfig = getLLMConfig(props.userId) ?? getEnvLLMConfig()
  llmApiUrl.value = globalConfig?.apiUrl || ''
  llmApiKey.value = globalConfig?.apiKey || ''
  useSeparateLLM.value = false
  showCreateDialog.value = true
}

function tryLoadImage(url: string): Promise<boolean> {
  return new Promise((resolve) => {
    const img = new Image()
    img.onload = () => resolve(true)
    img.onerror = () => resolve(false)
    img.src = url
  })
}

async function probeImage(basePath: string): Promise<string> {
  for (const ext of ['jpg', 'png', 'webp']) {
    const url = `${basePath}.${ext}`
    if (await tryLoadImage(url))
      return url
  }
  return ''
}

async function applyPreset(preset: CharacterPreset) {
  characterName.value = preset.alias
  characterBiography.value = preset.biography
  const [avatarImg, headerImg] = await Promise.all([
    probeImage(`/presets/${preset.id}-avatar`),
    probeImage(`/presets/${preset.id}-header`),
  ])
  importMeta.value = {
    netaUuid: '',
    avatarImg,
    headerImg,
  }
  info(`Loaded "${preset.label}" preset`)
}

async function handleImport() {
  if (!importUrl.value.trim())
    return
  importController?.abort()
  importController = new AbortController()
  importLoading.value = true
  try {
    const result = await importCharacter(props.userId, importUrl.value.trim(), importController.signal)
    characterName.value = result.character_name
    characterBiography.value = result.character_bio
    importMeta.value = {
      netaUuid: result.neta_uuid || '',
      avatarImg: result.avatar_img || '',
      headerImg: result.header_img || '',
    }
    importUrl.value = ''
    success('Character imported')
  }
  catch (err: any) {
    if (err.name === 'AbortError')
      return
    showError(err.message || 'Import failed')
  }
  finally {
    importLoading.value = false
  }
}

async function handleCreate() {
  if (!userAlias.value.trim()) {
    warning('User alias is required')
    return
  }
  // First character: require API key
  const isFirst = personas.value.length === 0
  if (isFirst && !llmApiKey.value.trim() && !hasLLMConfig(props.userId)) {
    warning('Please enter an API Key to continue')
    return
  }
  creating.value = true
  try {
    // Save global key on first character (or when not using separate key)
    if (llmApiKey.value.trim() && !useSeparateLLM.value) {
      saveLLMConfig(props.userId, { apiUrl: llmApiUrl.value.trim(), apiKey: llmApiKey.value.trim() })
    }

    const persona = await createPersona(props.userId, {
      user_alias: userAlias.value.trim(),
      character_name: characterName.value.trim(),
      character_biography: characterBiography.value.trim(),
      neta_uuid: importMeta.value.netaUuid || undefined,
      avatar_img: importMeta.value.avatarImg || undefined,
      header_img: importMeta.value.headerImg || undefined,
      // Only save per-persona key if user explicitly chose separate
      llm_api_url: useSeparateLLM.value ? llmApiUrl.value.trim() || undefined : undefined,
      llm_api_key: useSeparateLLM.value ? llmApiKey.value.trim() || undefined : undefined,
    })
    saveCharacterDefaults(props.userId, {
      userAlias: userAlias.value.trim(),
    })
    setCurrentPersonaId(props.userId, persona.id)
    showCreateDialog.value = false
    emit('select', persona)
  }
  catch (err: any) {
    showError(`Failed to create character: ${err.message}`)
  }
  finally {
    creating.value = false
  }
}

function selectPersona(persona: Persona) {
  setCurrentPersonaId(props.userId, persona.id)
  emit('select', persona)
}

async function handleDelete(persona: Persona, event: Event) {
  event.stopPropagation()
  // eslint-disable-next-line no-alert
  if (window.confirm(`Delete character "${persona.characterName || persona.userAlias}"? This will permanently delete all chat sessions and memories.`)) {
    try {
      await deletePersona(props.userId, persona.id)
      clearCurrentPersonaId(props.userId)
      personas.value = personas.value.filter(p => p.id !== persona.id)
      success('Character deleted')
    }
    catch (err: any) {
      showError(`Failed to delete: ${err.message}`)
    }
  }
}
</script>

<template>
  <div class="flex items-center justify-center w-screen h-screen bg-gray-50/50 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-blue-50/80 via-white to-white font-sans text-gray-900 border-none">
    <div class="bg-white/80 backdrop-blur-xl border border-gray-100 rounded-3xl shadow-xl px-10 pt-10 pb-6 w-full max-w-4xl min-h-[500px] max-h-[90vh] flex flex-col relative z-10 transition-all duration-300">
      <div class="text-center mb-8 flex flex-col items-center flex-shrink-0">
        <div class="w-16 h-16 bg-blue-50 text-blue-600 rounded-2xl flex items-center justify-center mb-4 rotate-3 shadow-sm border border-blue-100">
          <Sparkles class="w-8 h-8" />
        </div>
        <h1 class="text-3xl font-bold tracking-tight text-gray-900 mb-2">
          Start a chat, spark a life
        </h1>
        <p class="text-gray-500 max-w-sm mx-auto">
          Deeply attuned, infinitely evolving.
        </p>
      </div>

      <div class="flex-1 overflow-y-auto overflow-x-hidden min-h-0 pr-2 -mr-2 custom-scrollbar">
        <div v-if="loading" class="flex flex-col items-center justify-center py-20 text-blue-500">
          <Loader2 class="w-10 h-10 animate-spin mb-4" />
          <p class="text-sm text-gray-500">
            Loading your characters...
          </p>
        </div>

        <div v-else class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-5 pb-4">
          <div
          v-for="persona in personas"
          :key="persona.id"
          class="group relative border border-gray-200 hover:border-blue-400 rounded-2xl cursor-pointer transition-all duration-300 hover:shadow-[0_8px_30px_rgb(59,130,246,0.12)] hover:-translate-y-1 overflow-hidden h-80 sm:h-[340px] flex flex-col"
          :class="headerCache[persona.id] ? 'bg-white' : 'bg-slate-700'"
          @click="selectPersona(persona)"
        >
          <!-- Full Card Image Background -->
          <div
            v-if="headerCache[persona.id]"
            class="absolute inset-0 bg-cover bg-center transition-transform duration-700 group-hover:scale-105"
            :style="{ backgroundImage: `url(${headerCache[persona.id]})` }"
          >
            <!-- Gradient Overlay for text readability at bottom -->
            <div class="absolute inset-0 bg-gradient-to-t from-gray-900/90 via-gray-900/40 to-black/10" />
          </div>

          <IconButton
            class="absolute top-3 right-3 opacity-0 group-hover:opacity-100 transition-opacity bg-white/80 hover:bg-red-50 hover:text-red-600 z-20 backdrop-blur shadow-sm"
            variant="ghost"
            size="sm"
            title="Delete character"
            @click.stop="handleDelete(persona, $event)"
          >
            <Trash2 class="w-4 h-4" />
          </IconButton>

          <div class="p-5 flex flex-col h-full flex-1 relative z-10" :class="[headerCache[persona.id] ? 'justify-end' : '']">
            <div class="flex items-start gap-3 mb-3">
              <div
                v-if="avatarCache[persona.id]"
                class="w-12 h-12 rounded-full shadow-md flex-shrink-0 border-2 overflow-hidden bg-gray-50"
                :class="headerCache[persona.id] ? 'border-white/80' : 'border-white'"
              >
                <img :src="avatarCache[persona.id]" class="w-full h-full object-cover" alt="Avatar">
              </div>
              <div
                v-else
                class="w-12 h-12 rounded-full shadow-md flex-shrink-0 border-2 overflow-hidden"
                :class="headerCache[persona.id] ? 'border-white/80' : 'border-white'"
              >
                <img :src="momoAvatar" class="w-full h-full object-cover" alt="Avatar">
              </div>

              <div class="min-w-0 flex-1 pt-0.5">
                <h3 class="font-semibold truncate text-lg mb-1 drop-shadow-sm leading-tight text-white [text-shadow:_0_2px_8px_rgba(0,0,0,0.9)]">
                  {{ persona.characterName || '(no name)' }}
                </h3>
                <div
                  class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium border shadow-sm backdrop-blur-sm bg-black/30 text-gray-200 border-white/10"
                >
                  <UserIcon class="w-3 h-3 opacity-70" />
                  <span class="truncate max-w-[120px]">@{{ persona.userAlias }}</span>
                </div>
              </div>
            </div>

            <div v-if="persona.characterBiography" class="text-xs mt-auto line-clamp-3 leading-relaxed relative z-10 text-white/90 [text-shadow:_0_2px_8px_rgba(0,0,0,0.9)]">
              {{ persona.characterBiography }}
            </div>
          </div>
        </div>

        <div
          class="border-2 border-dashed border-gray-300 hover:border-blue-500 hover:bg-blue-50/50 rounded-2xl p-6 cursor-pointer transition-all duration-300 flex flex-col items-center justify-center text-gray-500 hover:text-blue-600 h-80 sm:h-[340px] group"
          @click="openCreateDialog"
        >
          <div class="w-12 h-12 rounded-full bg-gray-50 group-hover:bg-blue-100 flex items-center justify-center mb-3 transition-colors duration-300 border border-transparent group-hover:border-blue-200">
            <Plus class="w-6 h-6" />
          </div>
            <span class="font-medium">New Character</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Switch User floating button -->
    <div class="fixed bottom-6 right-6 z-20">
      <BaseButton
        variant="secondary"
        class="shadow-md bg-white/90 backdrop-blur text-gray-700 hover:bg-white border-gray-200"
        @click="handleSwitchUser"
      >
        <LogOut class="w-4 h-4 mr-2" />
        Switch Account
      </BaseButton>
    </div>

    <!-- Create Character Modal -->
    <BaseModal
      :visible="showCreateDialog"
      title="Create Character"
      width="520px"
      :close-on-click-modal="!creating"
      @update:visible="val => showCreateDialog = val"
    >
      <div class="flex flex-col gap-6 pt-2">
        <div class="flex flex-col gap-1">
          <label class="text-sm font-semibold text-gray-800">Your Alias</label>
          <p class="text-[11px] text-gray-500 mb-1">
            How the AI character should address you in conversation
          </p>
          <input
            v-model="userAlias"
            type="text"
            class="w-full rounded-xl border border-gray-200 px-4 py-2.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100 transition-all bg-gray-50 hover:bg-white focus:bg-white"
            placeholder="e.g. Master, Traveler, John"
          >
        </div>

        <div v-if="presets.length" class="flex flex-wrap gap-2">
          <button
            v-for="preset in presets"
            :key="preset.id"
            class="px-3 py-1.5 rounded-full text-xs font-medium border border-blue-100 text-blue-600 bg-blue-50 hover:bg-blue-100 transition-colors focus:outline-none focus:ring-2 focus:ring-offset-1 focus:ring-blue-300"
            @click="applyPreset(preset)"
          >
            {{ preset.label }}
          </button>
        </div>

        <div class="flex flex-col gap-3 p-4 bg-gray-50 border border-gray-100 rounded-xl">
          <button
            class="text-sm font-medium text-blue-600 hover:text-blue-700 flex items-center gap-2 focus:outline-none self-start"
            @click="importExpanded = !importExpanded"
          >
            <span class="transform transition-transform text-xs" :class="importExpanded ? 'rotate-90' : 'rotate-0'">▶</span>
            Import from nieta.art
          </button>

          <div v-if="importExpanded" class="flex flex-col gap-3">
            <p class="text-xs text-gray-600 leading-relaxed">
              Import a character from
              <a href="https://app.nieta.art/character/discover" target="_blank" rel="noopener" class="text-blue-600 hover:underline">nieta.art</a>.
              Open the character's page, copy the URL from your browser address bar, and paste it below.
            </p>
            <div class="flex gap-2">
              <input
                v-model="importUrl"
                type="text"
                class="flex-1 rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none bg-white"
                placeholder="Paste character page URL"
                :disabled="importLoading"
                @keyup.enter="handleImport"
              >
              <BaseButton
                variant="primary"
                :loading="importLoading"
                :disabled="!importUrl.trim()"
                @click="handleImport"
              >
                Import
              </BaseButton>
            </div>
            <p class="text-[11px] text-gray-400">
              e.g. https://app.nieta.art/oc?uuid=32417563-d287-4bda-b321-df04a6acffb5
            </p>
          </div>
        </div>

        <div class="flex flex-col gap-1 mt-2">
          <label class="text-sm font-semibold text-gray-800">Character Description</label>
          <p class="text-[11px] text-gray-500 mb-1">
            Define the AI persona you want to talk to
          </p>
          <input
            v-model="characterName"
            type="text"
            class="w-full rounded-xl border border-gray-200 px-4 py-2.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100 transition-all bg-gray-50 hover:bg-white focus:bg-white mb-3"
            placeholder="Character Name (e.g. Alice)"
          >
          <textarea
            v-model="characterBiography"
            rows="4"
            class="w-full rounded-xl border border-gray-200 px-4 py-3 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100 transition-all bg-gray-50 hover:bg-white focus:bg-white resize-none"
            placeholder="Character Biography (Personality, speech style, background, etc.)"
          />
        </div>

        <!-- LLM API Key section -->
        <div class="flex flex-col gap-1">
          <div class="flex items-center justify-between">
            <label class="text-sm font-semibold text-gray-800">
              LLM API Key
              <span v-if="personas.length === 0 && !hasLLMConfig(userId)" class="ml-1 text-amber-500 text-xs font-normal">Required</span>
            </label>
            <!-- Subsequent characters: toggle -->
            <label v-if="personas.length > 0 || hasLLMConfig(userId)" class="flex items-center gap-2 cursor-pointer select-none">
              <span class="text-[11px] text-gray-500">Separate key</span>
              <div
                class="w-8 h-4 rounded-full transition-colors relative flex-shrink-0"
                :class="useSeparateLLM ? 'bg-blue-500' : 'bg-gray-300'"
                @click="useSeparateLLM = !useSeparateLLM"
              >
                <div
                  class="absolute top-0.5 w-3 h-3 bg-white rounded-full shadow transition-transform"
                  :class="useSeparateLLM ? 'translate-x-4' : 'translate-x-0.5'"
                />
              </div>
            </label>
          </div>
          <p class="text-[11px] text-gray-500 mb-1">
            <template v-if="personas.length === 0 && !hasLLMConfig(userId)">
              Enter your OpenAI-compatible API key. This will be the global default for all characters.
            </template>
            <template v-else-if="!useSeparateLLM">
              Using global API key &nbsp;·&nbsp; toggle to configure a separate key for this character
            </template>
            <template v-else>
              This key will only apply to this character.
            </template>
          </p>
          <template v-if="personas.length === 0 || useSeparateLLM">
            <input
              v-model="llmApiUrl"
              type="text"
              class="w-full rounded-xl border border-gray-200 px-4 py-2.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100 transition-all bg-gray-50 hover:bg-white focus:bg-white mb-2"
              placeholder="API Base URL (e.g. https://api.openai.com/v1)"
            >
            <input
              v-model="llmApiKey"
              type="password"
              class="w-full rounded-xl border border-gray-200 px-4 py-2.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100 transition-all bg-gray-50 hover:bg-white focus:bg-white"
              :class="personas.length === 0 && !hasLLMConfig(userId) && !llmApiKey ? 'border-amber-300 focus:border-amber-400 focus:ring-amber-100' : ''"
              placeholder="API Key (sk-...)"
            >
          </template>
        </div>
      </div>

      <template #footer>
        <BaseButton variant="ghost" @click="showCreateDialog = false">
          Cancel
        </BaseButton>
        <BaseButton variant="primary" :loading="creating" :disabled="!userAlias.trim() || (personas.length === 0 && !hasLLMConfig(userId) && !llmApiKey.trim())" @click="handleCreate">
          Create Character
        </BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<style scoped>
/* Slim, beautiful scrollbar for the character grid */
.custom-scrollbar::-webkit-scrollbar {
  width: 6px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background-color: rgba(156, 163, 175, 0.4);
  border-radius: 10px;
}
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background-color: rgba(156, 163, 175, 0.7);
}
</style>
