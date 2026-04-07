<script setup lang="ts">
import { ArrowUp } from 'lucide-vue-next'
import IconButton from '@/components/ui/IconButton.vue'
import { ref, watch } from 'vue'

const props = defineProps<{ disabled: boolean }>()
const emit = defineEmits<{
  submit: [event: Event]
}>()

const model = defineModel<string>()
const textareaRef = ref<HTMLTextAreaElement | null>(null)

watch(model, (newVal) => {
  if (!newVal && textareaRef.value) {
    textareaRef.value.style.height = 'auto'
  }
})

function handleSend(event: Event) {
  if (props.disabled || !model.value?.trim())
    return
  emit('submit', event)
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    handleSend(event)
  }
}

function resizeTextarea(e: Event) {
  const target = e.target as HTMLTextAreaElement
  target.style.height = 'auto'
  target.style.height = `${target.scrollHeight}px`
}
</script>

<template>
  <div class="bg-white px-4 pt-4 pb-8">
    <div class="max-w-4xl mx-auto w-full">
      <form
        class="relative flex items-end gap-2 border border-gray-200 bg-white shadow-sm transition-all focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-100 rounded-[24px] px-4 py-1.5"
        @submit.prevent="handleSend"
      >
        <div class="flex-1 min-w-0 flex flex-col justify-center">
          <textarea
            ref="textareaRef"
            v-model="model"
            rows="1"
            class="w-full resize-none border-0 bg-transparent py-2.5 px-2 focus:ring-0 text-gray-900 leading-relaxed max-h-[240px] overflow-y-auto outline-none min-h-[44px] block"
            placeholder="Type a message..."
            @keydown="handleKeydown"
            @input="resizeTextarea"
          />
        </div>

        <div class="flex-shrink-0 self-end mb-0.5 mr-0.5">
          <IconButton
            variant="primary"
            type="submit"
            class="rounded-full w-[38px] h-[38px] flex items-center justify-center transition-all disabled:opacity-50 disabled:bg-blue-300 shadow-sm !p-0"
            :disabled="!model?.trim() || disabled"
          >
            <ArrowUp class="w-5 h-5 text-white" stroke-width="2.5" />
          </IconButton>
        </div>
      </form>
      <div class="mt-2 text-[11px] text-center text-gray-400 font-medium">
        Press Enter to send, Shift+Enter for new line
      </div>
    </div>
  </div>
</template>

<style scoped>
textarea::-webkit-scrollbar {
  display: none;
}
textarea {
  scrollbar-width: none;
  -ms-overflow-style: none; /* IE and Edge */
}
</style>
