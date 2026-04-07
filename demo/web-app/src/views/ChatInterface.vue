<script setup lang="ts">
import type { Message, MessagePart, SessionMetadata, User } from '@/types'
import type { Persona } from '@/utils/character'
import { Chat } from '@ai-sdk/vue'
import { DefaultChatTransport } from 'ai'
import { ArrowDown } from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'
import ChatInput from '@/components/ChatInput.vue'
import MessageList from '@/components/MessageList.vue'
import SessionSidebar from '@/components/SessionSidebar.vue'
import IconButton from '@/components/ui/IconButton.vue'
import { useTitleEditor } from '@/composables/useTitleEditor'
import { useToast } from '@/composables/useToast'
import * as api from '@/services/api'
import { getLLMConfig } from '@/utils/llmConfig'

const props = defineProps<{
  currentUser: User
  currentPersona: Persona
}>()

const emit = defineEmits<{
  switchCharacter: []
}>()

const { success, error: showError } = useToast()

const sessions = ref<SessionMetadata[]>([])
const currentSessionId = ref<string | null>(null)
const input = ref('')

// Scroll button state
const showScrollButton = ref(false)
const messageListRef = ref<InstanceType<typeof MessageList> | null>(null)
const userJustSentMessage = ref(false)

// Get current session
const currentSession = computed(() => {
  return sessions.value.find(s => s.id === currentSessionId.value)
})

const currentTitle = computed(() => currentSession.value?.title || '')
const {
  isEditingTitle,
  editingTitle,
  titleInput,
  startEditingTitle,
  saveTitle,
  handleTitleKeydown,
} = useTitleEditor(
  currentSessionId,
  currentTitle,
  updateSessionTitle,
)

const chat = new Chat({
  transport: new DefaultChatTransport({
    api: '/api/chat',
    headers: () => {
      const headers: Record<string, string> = { 'X-Persona-ID': props.currentPersona.id }
      // Pass global key only if persona has no custom key configured
      if (!props.currentPersona.llmApiKey) {
        const globalConfig = getLLMConfig(props.currentUser.id)
        if (globalConfig?.apiKey) {
          headers['X-LLM-API-Key'] = globalConfig.apiKey
          if (globalConfig.apiUrl)
            headers['X-LLM-API-URL'] = globalConfig.apiUrl
        }
      }
      return headers
    },
    prepareSendMessagesRequest: ({ id, messages, body, headers, credentials, api, trigger }) => ({
      body: { id, messages: messages.slice(-1), trigger, ...body },
      headers,
      credentials,
      api,
    }),
  }),
  id: currentSessionId.value || crypto.randomUUID(),
  initialMessages: [],
  onFinish: async () => {
    await loadSessions()
  },
  onError: (error) => {
    console.error('Chat error:', error)
    showError(`Chat error: ${error.message}`)
  },
})

const isLoading = computed(() => {
  const status = chat.status
  return status === 'submitted' || status === 'streaming'
})

// Load sessions on mount
onMounted(async () => {
  await loadSessions()

  if (sessions.value.length > 0) {
    await switchSession(sessions.value[0].id)
  }
})

async function loadSessions() {
  try {
    const data = await api.listSessions(props.currentPersona.id)
    sessions.value = data
  }
  catch (err: any) {
    console.error('Failed to load sessions:', err)
    showError(`Failed to load sessions: ${err.message}`)
  }
}

async function switchSession(sessionId: string) {
  if (sessionId === currentSessionId.value)
    return

  currentSessionId.value = sessionId

  try {
    const data = await api.getSession(sessionId, props.currentPersona.id)
    chat.messages.splice(0, chat.messages.length, ...(data.messages || []))
    chat.id = sessionId
  }
  catch (err: any) {
    console.error('Failed to load session:', err)
    showError(`Failed to load session: ${err.message}`)
    chat.messages.splice(0, chat.messages.length)
  }
}

async function createNewSession() {
  try {
    const { id: newId } = await api.createSession(props.currentPersona.id)
    currentSessionId.value = newId
    chat.id = newId
    chat.messages.splice(0, chat.messages.length)
    input.value = ''
    await loadSessions()
  }
  catch (err: any) {
    console.error('Failed to create session:', err)
    showError(`Failed to create session: ${err.message}`)
  }
}

async function deleteSession(sessionId: string) {
  try {
    await api.deleteSession(sessionId, props.currentPersona.id)
    await loadSessions()

    if (currentSessionId.value === sessionId) {
      if (sessions.value.length > 0) {
        await switchSession(sessions.value[0].id)
      }
      else {
        currentSessionId.value = null
        chat.id = crypto.randomUUID()
        chat.messages.splice(0, chat.messages.length)
        input.value = ''
      }
    }

    success('Session deleted')
  }
  catch (err: any) {
    console.error('Failed to delete session:', err)
    showError(`Failed to delete session: ${err.message}`)
  }
}

async function updateSessionTitle(sessionId: string, newTitle: string) {
  try {
    await api.updateSessionTitle(sessionId, props.currentPersona.id, newTitle)
    await loadSessions()
  }
  catch (err: any) {
    console.error('Failed to update title:', err)
    showError(`Failed to update title: ${err.message}`)
  }
}

function handleTitleClick() {
  if (isEditingTitle.value)
    return
  startEditingTitle()
}

// Message actions
function handleCopy(message: Message) {
  const text = message.parts
    ?.filter((part: MessagePart) => part.type === 'text')
    ?.map((part: MessagePart) => part.text)
    ?.join('') || message.content || ''

  navigator.clipboard.writeText(text)
  success('Copied to clipboard')
}

function handleSubmit(event: Event) {
  event.preventDefault()
  if (isLoading.value || !input.value?.trim())
    return
  userJustSentMessage.value = true
  chat.sendMessage(
    { text: input.value },
    {
      body: {
        characterName: props.currentPersona.characterName,
        characterBiography: props.currentPersona.characterBiography,
        userAlias: props.currentPersona.userAlias,
      },
    },
  )
  input.value = ''
}

function handleScrollButtonUpdate(shouldShow: boolean) {
  showScrollButton.value = shouldShow
}

function scrollToBottom() {
  messageListRef.value?.scrollToBottom(true)
}

function handleScrollComplete() {
  userJustSentMessage.value = false
}
</script>

<template>
  <div class="chat-interface">
    <SessionSidebar
      :sessions="sessions"
      :current-session-id="currentSessionId"
      :current-user="currentUser"
      :current-persona="currentPersona"
      @create-session="createNewSession"
      @switch-session="switchSession"
      @delete-session="deleteSession"
      @switch-character="emit('switchCharacter')"
    />
    <div class="chat-main">
      <div class="chat-header">
        <div class="header-title-area">
          <!-- Editing mode -->
          <input
            v-if="isEditingTitle"
            ref="titleInput"
            v-model="editingTitle"
            class="title-input"
            @keydown="handleTitleKeydown"
            @blur="saveTitle"
          >
          <!-- Display mode -->
          <h2
            v-else
            class="header-title" :class="[{ clickable: currentSessionId }]"
            @click="handleTitleClick"
          >
            {{ currentSession?.title || "New Chat" }}
          </h2>
        </div>
      </div>
      <div class="messages-wrapper">
        <MessageList
          ref="messageListRef"
          :messages="chat.messages"
          :is-loading="isLoading"
          :user-just-sent-message="userJustSentMessage"
          :current-persona="currentPersona"
          @copy="handleCopy"
          @update-scroll-button="handleScrollButtonUpdate"
          @scroll-complete="handleScrollComplete"
        />
        <!-- Scroll to bottom button -->
        <transition name="fade">
          <IconButton
            v-show="showScrollButton"
            class="scroll-to-bottom bg-white"
            variant="secondary"
            size="md"
            title="Scroll to bottom"
            @click="scrollToBottom"
          >
            <ArrowDown class="w-5 h-5" />
          </IconButton>
        </transition>
      </div>
      <ChatInput
        v-model="input"
        :disabled="isLoading"
        @submit="handleSubmit"
      />
    </div>
  </div>
</template>

<style scoped>
.chat-interface {
  display: flex;
  width: 100%;
  height: 100%;
  background-color: #fff;
}

.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.chat-header {
  padding: 1rem 1.5rem;
  border-bottom: 1px solid #e5e7eb;
  background-color: #fff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.header-title-area {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex: 1;
  min-width: 0;
}

.header-title {
  margin: 0;
  font-size: 1.25rem;
  font-weight: 600;
  color: #111827;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 1.5rem;
  padding: 0.25rem 0;
}

.header-title.clickable {
  cursor: pointer;
}

.header-title.clickable:hover {
  color: #3b82f6;
}

.title-input {
  font-size: 1.25rem;
  font-weight: 600;
  color: #111827;
  background: white;
  border: 1px solid #3b82f6;
  border-radius: 0.25rem;
  padding: 0.25rem 0.5rem;
  outline: none;
  flex: 1;
  min-width: 0;
  line-height: 1.5rem;
  height: 2rem;
  box-sizing: border-box;
}

.messages-wrapper {
  flex: 1;
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

/* Scroll to bottom button */
.scroll-to-bottom {
  position: absolute;
  bottom: 1.5rem;
  left: 50%;
  transform: translateX(-50%);
  z-index: 10;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  border-radius: 9999px;
}

/* Fade transition */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
