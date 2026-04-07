<script setup lang="ts">
import { X } from 'lucide-vue-next'
import { onMounted, onUnmounted, watch } from 'vue'
import IconButton from './IconButton.vue'

const props = withDefaults(defineProps<{
  visible?: boolean
  title?: string
  width?: string
  closeOnClickModal?: boolean
  showClose?: boolean
}>(), {
  visible: false,
  title: '',
  width: '500px',
  closeOnClickModal: true,
  showClose: true,
})

const emit = defineEmits<{
  'update:visible': [value: boolean]
  'close': []
  'open': []
}>()

function handleClose() {
  emit('update:visible', false)
  emit('close')
}

function handleBackdropClick(e: MouseEvent) {
  if (props.closeOnClickModal && e.target === e.currentTarget) {
    handleClose()
  }
}

// Handle body scroll locking
watch(() => props.visible, (newVal) => {
  if (newVal) {
    document.body.style.overflow = 'hidden'
    emit('open')
  }
  else {
    document.body.style.overflow = ''
  }
})

// Handle Escape key
function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && props.visible) {
    handleClose()
  }
}

onMounted(() => {
  document.addEventListener('keydown', handleKeydown)
  if (props.visible) {
    document.body.style.overflow = 'hidden'
  }
})

onUnmounted(() => {
  document.removeEventListener('keydown', handleKeydown)
  document.body.style.overflow = ''
})
</script>

<template>
  <Teleport to="body">
    <Transition
      enter-active-class="transition-opacity duration-300 ease-out"
      enter-from-class="opacity-0"
      enter-to-class="opacity-100"
      leave-active-class="transition-opacity duration-200 ease-in"
      leave-from-class="opacity-100"
      leave-to-class="opacity-0"
    >
      <div
        v-if="visible"
        class="fixed inset-0 z-[1000] flex items-center justify-center p-4 bg-gray-900/40 backdrop-blur-sm"
        @mousedown="handleBackdropClick"
      >
        <Transition
          enter-active-class="transition-all duration-300 ease-out"
          enter-from-class="opacity-0 translate-y-4 sm:translate-y-8 sm:scale-95"
          enter-to-class="opacity-100 translate-y-0 sm:scale-100"
          leave-active-class="transition-all duration-200 ease-in"
          leave-from-class="opacity-100 translate-y-0 sm:scale-100"
          leave-to-class="opacity-0 translate-y-4 sm:translate-y-8 sm:scale-95"
        >
          <div
            v-if="visible"
            class="relative bg-white rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh] w-full"
            :style="{ maxWidth: width }"
            @mousedown.stop
          >
            <!-- Header -->
            <div
              v-if="title || showClose || $slots.header"
              class="flex items-center justify-between px-6 py-4 border-b border-gray-100"
            >
              <slot name="header">
                <h3 v-if="title" class="text-lg font-semibold text-gray-900 m-0">
                  {{ title }}
                </h3>
                <div v-else />
              </slot>

              <IconButton
                v-if="showClose"
                variant="ghost"
                size="sm"
                title="Close"
                class="ml-auto -mr-2"
                @click="handleClose"
              >
                <X class="w-5 h-5" />
              </IconButton>
            </div>

            <!-- Body -->
            <div class="px-6 py-5 overflow-y-auto flex-1 min-h-[50px]">
              <slot />
            </div>

            <!-- Footer -->
            <div
              v-if="$slots.footer"
              class="px-6 py-4 border-t border-gray-100 bg-gray-50 flex items-center justify-end gap-3"
            >
              <slot name="footer" />
            </div>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>
