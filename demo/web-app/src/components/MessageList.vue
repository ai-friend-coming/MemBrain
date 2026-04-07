<script setup lang="ts">
import type { Message } from '@/types'
import type { Persona } from '@/utils/character'
import { MessageSquareText } from 'lucide-vue-next'
import { computed, toRef } from 'vue'
import { useAutoScroll } from '@/composables/useAutoScroll'
import MessageItem from './MessageItem.vue'

const props = defineProps<{
  messages: Message[]
  isLoading: boolean
  userJustSentMessage: boolean
  currentPersona: Persona
}>()

const emit = defineEmits<{
  copy: [message: Message]
  updateScrollButton: [show: boolean]
  scrollComplete: []
}>()

const lastAssistantMessageId = computed(() => {
  const assistantMessages = props.messages.filter(m => m.role === 'assistant')
  return assistantMessages.length > 0
    ? assistantMessages[assistantMessages.length - 1].id
    : null
})

const streamingMessageId = computed(() => {
  if (!props.isLoading)
    return null
  const assistantMessages = props.messages.filter(m => m.role === 'assistant')
  return assistantMessages.length > 0
    ? assistantMessages[assistantMessages.length - 1].id
    : null
})

const { messagesContainer, checkScrollPosition, scrollToBottom } = useAutoScroll(
  toRef(props, 'messages'),
  toRef(props, 'isLoading'),
  toRef(props, 'userJustSentMessage'),
  {
    onScrollButtonUpdate: show => emit('updateScrollButton', show),
    onScrollComplete: () => emit('scrollComplete'),
  },
)

defineExpose({ scrollToBottom })
</script>

<template>
  <div
    ref="messagesContainer"
    class="messages-container"
    @scroll="checkScrollPosition"
  >
    <div v-if="messages.length === 0" class="empty-state h-full flex flex-col items-center justify-center p-8 text-center animate-in fade-in duration-500">
      <div class="w-16 h-16 bg-blue-50/50 rounded-full flex items-center justify-center text-blue-400 mb-5 shadow-sm border border-blue-100/30">
        <MessageSquareText class="w-8 h-8 opacity-80" />
      </div>
      <h3 class="text-xl font-medium text-gray-800 mb-3 tracking-tight">
        Start a Conversation
      </h3>
      <p class="text-sm text-gray-500 max-w-sm mx-auto leading-relaxed flex flex-col gap-1 items-center">
        <span>Say hello to</span>
        <span class="font-semibold text-gray-700 bg-gray-50 px-3 py-1 rounded-md border border-gray-100 shadow-sm max-w-full truncate">
          {{ currentPersona?.characterName || 'the character' }}
        </span>
      </p>
    </div>

    <div v-else class="messages-list">
      <MessageItem
        v-for="message in messages"
        :key="message.id"
        :message="message"
        :is-last-assistant="message.id === lastAssistantMessageId"
        :is-streaming="message.id === streamingMessageId"
        :persona="currentPersona"
        @copy="emit('copy', $event)"
      />
    </div>
  </div>
</template>

<style scoped>
.messages-container {
  flex: 1;
  overflow-y: auto;
  background-color: #fff;
  padding: 1rem;
  position: relative;
}

.messages-list {
  max-width: 48rem;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

/* Invisible scrollbar - only shows on scroll */
.messages-container::-webkit-scrollbar {
  width: 0px;
  background: transparent;
}

.messages-container::-webkit-scrollbar-track {
  background: transparent;
}

.messages-container::-webkit-scrollbar-thumb {
  background: transparent;
}

.messages-container::-webkit-scrollbar-thumb:hover {
  background: transparent;
}
</style>
