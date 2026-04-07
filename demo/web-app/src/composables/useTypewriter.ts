import type { Ref } from 'vue'
import type { Message } from '@/types'
import { computed, onUnmounted, ref, watch } from 'vue'
import { CHAR_REVEAL_INTERVAL_MS } from '@/constants'

export function useTypewriter(
  message: Ref<Message>,
  isStreaming: Ref<boolean>,
) {
  const displayedText = ref('')
  const isTypewriting = ref(false)
  const rafId = ref<number | null>(null)
  const lastUpdateTime = ref(0)

  const fullText = computed(() => {
    if (message.value.parts) {
      return message.value.parts
        .filter(part => part.type === 'text')
        .map(part => part.text)
        .join('')
    }
    return message.value.content || ''
  })

  const showCursor = computed(() => {
    return isTypewriting.value || (isStreaming.value && fullText.value.length === 0)
  })

  function typewriterLoop(currentTime: number) {
    if (currentTime - lastUpdateTime.value < CHAR_REVEAL_INTERVAL_MS) {
      rafId.value = requestAnimationFrame(typewriterLoop)
      return
    }

    lastUpdateTime.value = currentTime

    if (displayedText.value.length < fullText.value.length) {
      displayedText.value = fullText.value.slice(0, displayedText.value.length + 1)
      rafId.value = requestAnimationFrame(typewriterLoop)
    }
    else {
      isTypewriting.value = false
      rafId.value = null
    }
  }

  function startTypewriter() {
    if (!isTypewriting.value) {
      isTypewriting.value = true
      lastUpdateTime.value = performance.now()
      rafId.value = requestAnimationFrame(typewriterLoop)
    }
  }

  function cancelTypewriter() {
    if (rafId.value) {
      cancelAnimationFrame(rafId.value)
      rafId.value = null
    }
    isTypewriting.value = false
  }

  watch(
    fullText,
    (newText) => {
      if (isStreaming.value && message.value.role === 'assistant') {
        if (!isTypewriting.value && newText.length > 0) {
          startTypewriter()
        }
      }
      else {
        displayedText.value = newText
      }
    },
    { immediate: true },
  )

  watch(
    () => message.value.id,
    (newId, oldId) => {
      if (newId !== oldId) {
        cancelTypewriter()
        displayedText.value = fullText.value
      }
    },
  )

  onUnmounted(() => {
    cancelTypewriter()
  })

  return {
    displayedText,
    fullText,
    showCursor,
  }
}
