import type { Ref } from 'vue'
import type { Message } from '@/types'
import { nextTick, ref, watch } from 'vue'
import { SCROLL_NEAR_BOTTOM_PX, SCROLL_SHOW_BUTTON_PX, SCROLL_UP_TOLERANCE_PX, SMOOTH_SCROLL_DURATION_MS } from '@/constants'

interface UseAutoScrollOptions {
  onScrollButtonUpdate?: (show: boolean) => void
  onScrollComplete?: () => void
}

export function useAutoScroll(
  messages: Ref<Message[]>,
  isLoading: Ref<boolean>,
  userJustSentMessage: Ref<boolean>,
  options: UseAutoScrollOptions = {},
) {
  const messagesContainer = ref<HTMLElement | null>(null)
  const autoScrollEnabled = ref(false)
  const lastScrollTop = ref(0)

  function isNearBottom(): boolean {
    if (!messagesContainer.value)
      return false
    const { scrollTop, scrollHeight, clientHeight } = messagesContainer.value
    return scrollHeight - scrollTop - clientHeight < SCROLL_NEAR_BOTTOM_PX
  }

  function checkScrollPosition() {
    if (!messagesContainer.value)
      return
    const { scrollTop, scrollHeight, clientHeight } = messagesContainer.value
    const shouldShow = scrollHeight - scrollTop - clientHeight > SCROLL_SHOW_BUTTON_PX
    options.onScrollButtonUpdate?.(shouldShow)

    if (autoScrollEnabled.value && scrollTop < lastScrollTop.value - SCROLL_UP_TOLERANCE_PX) {
      autoScrollEnabled.value = false
    }

    lastScrollTop.value = scrollTop
  }

  async function scrollToBottom(instant = false) {
    if (!messagesContainer.value)
      return
    messagesContainer.value.scrollTo({
      top: messagesContainer.value.scrollHeight,
      behavior: instant ? 'auto' : 'smooth',
    })
    await nextTick()
    if (!instant) {
      setTimeout(() => {
        options.onScrollComplete?.()
      }, SMOOTH_SCROLL_DURATION_MS)
    }
    else {
      options.onScrollComplete?.()
    }
  }

  // Auto-scroll on message changes
  watch(
    messages,
    async () => {
      await nextTick()

      if (userJustSentMessage.value) {
        if (isNearBottom()) {
          autoScrollEnabled.value = true
          scrollToBottom()
        }
      }
      else if (autoScrollEnabled.value) {
        scrollToBottom(true)
      }
      else if (isNearBottom()) {
        scrollToBottom()
      }

      checkScrollPosition()
    },
    { deep: true },
  )

  // Disable auto-scroll when streaming stops
  watch(isLoading, (newValue) => {
    if (!newValue && autoScrollEnabled.value) {
      autoScrollEnabled.value = false
    }
  })

  return {
    messagesContainer,
    checkScrollPosition,
    scrollToBottom,
  }
}
