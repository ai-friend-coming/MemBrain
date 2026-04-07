<script setup lang="ts">
import type { Message } from '@/types'
import { computed } from 'vue'

const props = defineProps<{
  message: Message
  highlighted: boolean
  colorIndex: number
}>()

const formattedTime = computed(() => {
  const msg = props.message
  if (msg.message_time)
    return msg.message_time.replace('T', ' ').replace(/\.\d+$/, '')
  return msg.message_time_raw ?? ''
})
</script>

<template>
  <div
    class="message-item"
    :class="[`sc-${colorIndex}`, { 'highlight-evidence': highlighted }]"
  >
    <div class="msg-header">
      <div class="speaker-wrapper">
        <span class="speaker-dot"></span>
        <span class="msg-speaker">{{ message.speaker }}</span>
      </div>
      <span v-if="formattedTime" class="msg-time">{{ formattedTime }}</span>
    </div>
    <div class="msg-content">{{ message.content }}</div>
  </div>
</template>

<style scoped>
.message-item {
  max-width: 100%;
  border-radius: var(--radius, 0.5rem);
  background-color: var(--card-bg, #ffffff);
  border: 1px solid var(--c-border);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.02);
  transition: border-color 0.15s ease-out, box-shadow 0.15s ease-out, background-color 0.15s ease-out;
  overflow: hidden;
}

.message-item:hover {
  border-color: var(--c-border-strong);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.04);
}

.message-item.highlight-evidence {
  border-color: var(--c-accent);
  box-shadow: 0 0 0 1px var(--c-accent), 0 4px 12px rgba(99, 102, 241, 0.12);
}

/* Elegant palettes for speakers */
.sc-0, .sc-2, .sc-4 {
  --speaker-color: var(--c-text-2);
  --speaker-badge: var(--c-accent); /* indigo indicator */
  --card-bg: var(--c-bg); /* white background */
}

.sc-1, .sc-3 {
  --speaker-color: var(--c-text-3);
  --speaker-badge: var(--c-text-5); /* slate indicator */
  --card-bg: var(--c-bg-subtle, #f8fafc); /* soft subtle background */
}

.msg-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.25rem 0.5rem;
}

.speaker-wrapper {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.speaker-dot {
  width: 0.375rem;
  height: 0.375rem;
  border-radius: 50%;
  background-color: var(--speaker-badge);
  opacity: 0.9;
}

.msg-speaker {
  font-size: var(--text-base);
  font-weight: var(--fw-semibold);
  color: var(--speaker-color);
  letter-spacing: var(--ls-wide);
}

.msg-time {
  font-size: var(--text-sm);
  color: var(--c-text-muted);
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}

.msg-content {
  padding: 0 1.25rem 1.25rem;
  font-size: 1.0625rem;
  line-height: var(--lh-relaxed);
  color: var(--c-text-2);
  white-space: pre-wrap;
  word-wrap: break-word;
}
</style>
