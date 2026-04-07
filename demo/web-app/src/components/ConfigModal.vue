<script setup lang="ts">
import type { Persona } from '@/utils/character'
import { AlertTriangle, BookOpen, Database, Key, User } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import { useToast } from '@/composables/useToast'
import { deleteUserData, updatePersonaLLM } from '@/services/api'
import { hasLLMConfig } from '@/utils/llmConfig'
import BaseModal from './ui/BaseModal.vue'

const props = defineProps<{
  visible: boolean
  userId: string
  personaId: string
  currentPersona: Persona
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
}>()

const { error: showError } = useToast()

// LLM Config
const llmApiUrl = ref(props.currentPersona.llmApiUrl || '')
const llmApiKey = ref(props.currentPersona.llmApiKey || '')
const hasGlobal = computed(() => hasLLMConfig(props.userId))
const useSeparateKey = ref(!!props.currentPersona.llmApiKey)

let saveTimer: ReturnType<typeof setTimeout> | null = null

function scheduleSave() {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(async () => {
    try {
      await updatePersonaLLM(props.userId, props.personaId, llmApiUrl.value, llmApiKey.value)
    }
    catch (e: any) {
      showError(e.message || 'Failed to save')
    }
  }, 600)
}

function toggleSeparateKey() {
  useSeparateKey.value = !useSeparateKey.value
  if (!useSeparateKey.value) {
    llmApiUrl.value = ''
    llmApiKey.value = ''
    scheduleSave()
  }
}

const holdProgress = ref(0)

let holdStart: number | null = null
let rafId: number | null = null
const HOLD_DURATION = 5000

function handleClose() {
  emit('update:visible', false)
}

function startHold() {
  holdStart = performance.now()
  tick()
}

function tick() {
  if (holdStart === null)
    return
  const elapsed = performance.now() - holdStart
  holdProgress.value = Math.min((elapsed / HOLD_DURATION) * 100, 100)
  if (holdProgress.value >= 100) {
    cancelHold()
    confirmClear()
    return
  }
  rafId = requestAnimationFrame(tick)
}

function cancelHold() {
  if (rafId !== null) {
    cancelAnimationFrame(rafId)
    rafId = null
  }
  holdStart = null
  holdProgress.value = 0
}

async function confirmClear() {
  await deleteUserData(props.personaId)
  window.location.reload()
}
</script>

<template>
  <BaseModal
    :visible="visible"
    title="Settings"
    width="520px"
    @update:visible="emit('update:visible', $event)"
    @close="handleClose"
  >
    <div class="flex flex-col gap-8 py-2">
      <!-- Section: About Character -->
      <div class="flex flex-col gap-4">
        <h3 class="text-xs font-bold text-gray-500 uppercase tracking-wider flex items-center gap-2 border-b border-gray-100 pb-2">
          <User class="w-4 h-4" />
          About Character
        </h3>

        <div class="bg-gray-50/50 border border-gray-100 rounded-xl p-4 flex flex-col gap-4">
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div class="flex flex-col gap-1.5">
              <label class="text-[11px] font-semibold text-gray-500 uppercase tracking-wide">Character Name</label>
              <div class="text-sm text-gray-900 font-medium bg-white px-3 py-2 rounded-lg border border-gray-200/60 shadow-sm opacity-90">
                {{ currentPersona.characterName || 'Unnamed' }}
              </div>
            </div>
            <div class="flex flex-col gap-1.5">
              <label class="text-[11px] font-semibold text-gray-500 uppercase tracking-wide">Your Alias</label>
              <div class="text-sm text-gray-900 font-medium bg-white px-3 py-2 rounded-lg border border-gray-200/60 shadow-sm opacity-90 flex items-center gap-1">
                <span class="text-gray-400">@</span>{{ currentPersona.userAlias || 'Unknown' }}
              </div>
            </div>
          </div>

          <div class="flex flex-col gap-1.5">
            <label class="text-[11px] font-semibold text-gray-500 uppercase tracking-wide flex items-center gap-1.5">
              <BookOpen class="w-3.5 h-3.5" />
              Biography
            </label>
            <div class="text-sm text-gray-700 bg-white px-3 py-2.5 rounded-lg border border-gray-200/60 shadow-sm min-h-[80px] leading-relaxed whitespace-pre-wrap opacity-90">
              {{ currentPersona.characterBiography || 'No biography provided.' }}
            </div>
          </div>
        </div>
      </div>

      <!-- Section: LLM Config -->
      <div class="flex flex-col gap-4">
        <h3 class="text-xs font-bold text-gray-500 uppercase tracking-wider flex items-center gap-2 border-b border-gray-100 pb-2">
          <Key class="w-4 h-4" />
          LLM API Key
        </h3>
        <div class="flex items-center justify-between">
          <p class="text-[11px] text-gray-500">
            <template v-if="hasGlobal && !useSeparateKey">
              Using global API key
            </template>
            <template v-else-if="hasGlobal">
              This key will only apply to this character.
            </template>
            <template v-else>
              Override the default LLM for this character.
            </template>
          </p>
          <label v-if="hasGlobal" class="flex items-center gap-2 cursor-pointer select-none">
            <span class="text-[11px] text-gray-500">Separate key</span>
            <div
              class="w-8 h-4 rounded-full transition-colors relative flex-shrink-0"
              :class="useSeparateKey ? 'bg-blue-500' : 'bg-gray-300'"
              @click="toggleSeparateKey"
            >
              <div
                class="absolute top-0.5 w-3 h-3 bg-white rounded-full shadow transition-transform"
                :class="useSeparateKey ? 'translate-x-4' : 'translate-x-0.5'"
              />
            </div>
          </label>
        </div>
        <template v-if="!hasGlobal || useSeparateKey">
          <div class="bg-gray-50/50 border border-gray-100 rounded-xl p-4 flex flex-col gap-3">
            <div class="flex flex-col gap-1.5">
              <label class="text-[11px] font-semibold text-gray-500 uppercase tracking-wide">API Base URL</label>
              <input
                v-model="llmApiUrl"
                type="text"
                class="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100 bg-gray-50 hover:bg-white focus:bg-white transition-all"
                placeholder="https://api.openai.com/v1 (optional)"
                @blur="scheduleSave"
              >
            </div>
            <div class="flex flex-col gap-1.5">
              <label class="text-[11px] font-semibold text-gray-500 uppercase tracking-wide">API Key</label>
              <input
                v-model="llmApiKey"
                type="password"
                class="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100 bg-gray-50 hover:bg-white focus:bg-white transition-all"
                placeholder="sk-..."
                @blur="scheduleSave"
              >
            </div>
          </div>
        </template>
      </div>

      <!-- Section: Data Management -->
      <div class="flex flex-col gap-4">
        <h3 class="text-xs font-bold text-gray-500 uppercase tracking-wider flex items-center gap-2 border-b border-gray-100 pb-2">
          <Database class="w-4 h-4" />
          Data Management
        </h3>

        <div class="bg-red-50/30 border border-red-100 rounded-xl p-4 flex flex-col gap-3">
          <div class="flex items-start gap-3 text-red-800">
            <AlertTriangle class="w-5 h-5 flex-shrink-0 mt-0.5 text-red-500" />
            <div class="text-sm font-medium">
              Danger Zone
              <p class="text-xs text-red-600/80 font-normal mt-1 leading-relaxed">
                This will permanently delete all chat sessions and memories for the current character. This action cannot be undone.
              </p>
            </div>
          </div>

          <div
            class="hold-btn mt-2"
            @mousedown="startHold"
            @mouseup="cancelHold"
            @mouseleave="cancelHold"
            @touchstart.prevent="startHold"
            @touchend="cancelHold"
            @touchcancel="cancelHold"
          >
            <div class="hold-btn-fill" :style="{ width: `${holdProgress}%` }" />
            <span class="hold-btn-text">
              {{ holdProgress > 0 ? `Hold to confirm... ${Math.ceil((100 - holdProgress) / 20)}s` : 'Hold to Delete All Data' }}
            </span>
          </div>
        </div>
      </div>
    </div>
  </BaseModal>
</template>

<style scoped>
.hold-btn {
  position: relative;
  width: 100%;
  height: 48px;
  border-radius: 12px;
  border: 1px solid #fee2e2;
  background: #fff;
  cursor: pointer;
  overflow: hidden;
  user-select: none;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
  box-shadow: 0 2px 8px rgba(239, 68, 68, 0.05);
}

.hold-btn:hover {
  border-color: #fca5a5;
  box-shadow: 0 4px 12px rgba(239, 68, 68, 0.1);
  background: #fef2f2;
}

.hold-btn-fill {
  position: absolute;
  inset: 0 auto 0 0;
  background: linear-gradient(90deg, #f87171, #ef4444);
  transition: width 0.1s linear;
}

.hold-btn-text {
  position: relative;
  z-index: 1;
  font-size: 0.9rem;
  font-weight: 500;
  color: #ef4444;
  transition: color 0.1s;
}

.hold-btn:active .hold-btn-text,
.hold-btn-fill + .hold-btn-text[data-progress="active"] {
  /* Simple trick for blending, but standard white usually works if we progress fast enough. */
  text-shadow: 0 1px 2px rgba(0,0,0,0.1);
}
/* When filled more than 0, let's just make the text white for contrast */
.hold-btn:active .hold-btn-text {
  mix-blend-mode: color-burn;
}
</style>
