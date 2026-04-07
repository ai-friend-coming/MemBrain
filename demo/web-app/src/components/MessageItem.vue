<script setup lang="ts">
import type { Message } from '@/types'
import type { Persona } from '@/utils/character'
import { Copy } from 'lucide-vue-next'
import { ref, toRef } from 'vue'
import momoAvatar from '@/assets/avatar/momo.jpeg'
import userAvatar from '@/assets/avatar/user.jpg'
import IconButton from '@/components/ui/IconButton.vue'
import { useCharacterAvatar } from '@/composables/useCharacterAvatar'
import { useToast } from '@/composables/useToast'
import { useTypewriter } from '@/composables/useTypewriter'
import { COPY_FEEDBACK_DURATION_MS } from '@/constants'

const props = defineProps<{
  message: Message
  isLastAssistant: boolean
  isStreaming: boolean
  persona?: Persona
}>()

const emit = defineEmits<{
  copy: [message: Message]
}>()

const { displayedText, showCursor } = useTypewriter(
  toRef(props, 'message'),
  toRef(props, 'isStreaming'),
)

const { info } = useToast()

const showActions = ref(false)
const copySuccess = ref(false)

function handleCopy() {
  emit('copy', props.message)
  copySuccess.value = true
  setTimeout(() => {
    copySuccess.value = false
  }, COPY_FEEDBACK_DURATION_MS)
}

function renderText(text: string, role: string): string {
  let escaped = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')

  if (role === 'user') {
    return escaped
  }

  // Thoughts: inline text with specific aesthetic color, no background
  escaped = escaped.replace(/（([^）]+)）/g, '<span class="thought-text">（$1）</span>')

  // Actions: convert to block elements and strip surrounding whitespace/newlines
  // Replace both **...** and *...*
  escaped = escaped.replace(/\*\*([^*]+)\*\*/g, '<div class="action-text">$1</div>')
  escaped = escaped.replace(/\*([^*\n]+)\*/g, '<div class="action-text">$1</div>')

  // Strip all whitespace and newlines immediately surrounding the action divs
  // so that CSS margins can cleanly control the spacing without stray \n causing gaps.
  escaped = escaped.replace(/\s*(<div class="action-text">.*?<\/div>)\s*/g, '$1')

  return escaped
}

function formatTime(timestamp: string) {
  if (!timestamp)
    return ''
  const date = new Date(timestamp.includes('Z') || timestamp.includes('+') ? timestamp : `${timestamp}Z`)
  return date.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
  })
}

const currentAvatarUrl = useCharacterAvatar(
  () => props.message.role === 'assistant' ? props.persona?.avatarImg : undefined,
)
</script>

<template>
  <div
    class="message-item" :class="[message.role]"
    @mouseenter="showActions = true"
    @mouseleave="showActions = false"
  >
    <div class="message-wrapper">
      <div class="message-avatar" :class="[message.role]">
        <img
          v-if="message.role === 'assistant'"
          :src="currentAvatarUrl || momoAvatar"
          alt="Assistant"
          class="avatar-image"
        >
        <img v-else :src="userAvatar" alt="User" class="avatar-image">
      </div>

      <div class="message-content-wrapper">
        <div class="message-bubble">
          <div class="message-text">
            <span v-html="renderText(displayedText, message.role)" /><span v-if="showCursor" class="typewriter-cursor" />
          </div>
          <div v-if="message.timestamp" class="message-time">
            {{ formatTime(message.timestamp) }}
          </div>
        </div>

        <div class="message-actions" :class="{ visible: showActions }">
          <!-- Copy button -->
          <IconButton
            :variant="copySuccess ? 'soft' : 'ghost'"
            size="sm"
            :title="copySuccess ? 'Copied!' : 'Copy message'"
            :class="copySuccess ? 'text-green-500' : ''"
            @click="handleCopy"
          >
            <Copy class="w-4 h-4" />
          </IconButton>

        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.message-item {
  display: flex;
  width: 100%;
}

.message-item.user {
  justify-content: flex-end;
}

.message-item.assistant {
  justify-content: flex-start;
}

.message-wrapper {
  display: flex;
  gap: 0.75rem;
  max-width: 80%;
  align-items: flex-start;
}

.message-item.user .message-wrapper {
  flex-direction: row-reverse;
}

.message-avatar {
  width: 2.5rem;
  height: 2.5rem;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  overflow: hidden;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}

.avatar-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center;
}

.message-content-wrapper {
  flex: 1;
  min-width: 0;
}

.message-bubble {
  padding: 0.875rem 1.125rem;
  border-radius: 1.25rem;
  word-wrap: break-word;
  position: relative;
  min-height: 2.5rem; /* Prevent height collapse during streaming */
}

.message-item.user .message-bubble {
  background: linear-gradient(135deg, #3b82f6, #60a5fa);
  color: white;
  border-bottom-right-radius: 0.25rem;
  box-shadow: 0 4px 12px rgba(59, 130, 246, 0.15);
}

.message-item.assistant .message-bubble {
  background-color: #ffffff;
  color: #1f2937;
  border: 1px solid #f3f4f6;
  border-bottom-left-radius: 0.25rem;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.03);
}

.message-text {
  font-size: 0.95rem;
  line-height: 1.6;
  white-space: pre-wrap;
}

.message-text :deep(.action-text) {
  display: block;
  margin: 0.75rem 0;
  padding: 0.5rem 0.875rem;
  color: #0d9488;
  background-color: rgba(13, 148, 136, 0.06);
  border-left: 2px solid rgba(13, 148, 136, 0.5);
  border-radius: 0 0.375rem 0.375rem 0;
  font-size: 0.95em;
  font-style: italic;
  line-height: 1.5;
}

.message-item.user .message-text :deep(.action-text) {
  color: #e0e7ff;
  background-color: rgba(255, 255, 255, 0.1);
  border-left-color: rgba(255, 255, 255, 0.4);
}

.message-text :deep(.thought-text) {
  color: #64748b; /* Muted slate */
  font-size: 0.95em;
  font-style: normal;
}

.message-item.user .message-text :deep(.thought-text) {
  color: #e2e8f0;
}

.message-time {
  font-size: 0.75rem;
  margin-top: 0.25rem;
  opacity: 0.7;
}

.message-item.user .message-time {
  text-align: right;
}

.message-actions {
  display: flex;
  gap: 0.25rem;
  margin-top: 0.5rem;
  min-height: 32px;
  opacity: 0;
  transition: opacity 0.2s ease-in-out;
  pointer-events: none;
}

.message-actions.visible {
  opacity: 1;
  pointer-events: auto;
}

.message-item.user .message-actions {
  justify-content: flex-end;
}

.action-btn {
  padding: 0.375rem !important;
  min-height: auto !important;
}

.typewriter-cursor {
  display: inline-block;
  width: 2px;
  height: 1em;
  background: linear-gradient(180deg, #3b82f6 0%, #60a5fa 100%);
  margin-left: 0.25rem;
  vertical-align: text-bottom;
  animation: cursor-pulse 1.2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
  border-radius: 1px;
  box-shadow: 0 0 8px rgba(59, 130, 246, 0.4);
}

@keyframes cursor-pulse {
  0%,
  100% {
    opacity: 1;
    transform: scaleY(1);
  }
  50% {
    opacity: 0.3;
    transform: scaleY(0.8);
  }
}
</style>
