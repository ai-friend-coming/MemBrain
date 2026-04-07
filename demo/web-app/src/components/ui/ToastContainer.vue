<script setup lang="ts">
import { AlertCircle, AlertTriangle, CheckCircle, Info, X } from 'lucide-vue-next'
import { useToast } from '@/composables/useToast'

const { toasts, removeToast } = useToast()

function getIcon(type: string) {
  switch (type) {
    case 'success': return CheckCircle
    case 'warning': return AlertTriangle
    case 'error': return AlertCircle
    default: return Info
  }
}

function getStyles(type: string) {
  switch (type) {
    case 'success': return 'bg-white text-green-700 border-green-200'
    case 'warning': return 'bg-white text-yellow-700 border-yellow-200'
    case 'error': return 'bg-white text-red-700 border-red-200'
    default: return 'bg-white text-blue-700 border-blue-200'
  }
}

function getIconColor(type: string) {
  switch (type) {
    case 'success': return 'text-green-500'
    case 'warning': return 'text-yellow-500'
    case 'error': return 'text-red-500'
    default: return 'text-blue-500'
  }
}
</script>

<template>
  <div class="fixed top-4 left-1/2 transform -translate-x-1/2 z-[9999] flex flex-col gap-2 pointer-events-none w-full max-w-sm px-4">
    <TransitionGroup name="toast">
      <div
        v-for="toast in toasts"
        :key="toast.id"
        class="toast-item pointer-events-auto flex items-start gap-3 px-4 py-3 rounded-xl shadow-lg border backdrop-blur-md bg-opacity-95"
        :class="getStyles(toast.type || 'info')"
      >
        <component :is="getIcon(toast.type || 'info')" class="w-5 h-5 flex-shrink-0 mt-0.5" :class="getIconColor(toast.type || 'info')" />
        <p class="text-sm font-medium flex-1 text-gray-800">
          {{ toast.message }}
        </p>
        <button
          class="flex-shrink-0 text-gray-400 hover:text-gray-600 transition-colors focus:outline-none"
          @click="removeToast(toast.id)"
        >
          <X class="w-4 h-4" />
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
/* Ensure leave active is absolute but maintains its layout width. */
.toast-move,
.toast-enter-active,
.toast-leave-active {
  transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateY(-8px) scale(0.98);
}

.toast-leave-active {
  position: absolute;
  /* Since its container is max-w-sm px-4, and we used w-full, giving it explicit alignment: */
  left: 1rem;
  right: 1rem;
}
</style>
