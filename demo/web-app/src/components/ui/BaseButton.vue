<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost' | 'soft'
  size?: 'sm' | 'md' | 'lg'
  disabled?: boolean
  loading?: boolean
  block?: boolean
}>(), {
  variant: 'secondary',
  size: 'md',
  disabled: false,
  loading: false,
  block: false,
})

const emit = defineEmits<{
  click: [event: MouseEvent]
}>()

const baseClasses = 'inline-flex items-center justify-center font-medium transition-all duration-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-offset-1 select-none whitespace-nowrap'

const variantClasses = computed(() => {
  switch (props.variant) {
    case 'primary':
      return 'bg-blue-600 text-white hover:bg-blue-700 hover:shadow-md active:bg-blue-800 focus:ring-blue-500'
    case 'danger':
      return 'bg-red-500 text-white hover:bg-red-600 hover:shadow-md active:bg-red-700 focus:ring-red-500'
    case 'ghost':
      return 'bg-transparent text-gray-600 hover:bg-gray-100 hover:text-gray-900 active:bg-gray-200 focus:ring-gray-300'
    case 'soft':
      return 'bg-blue-50 text-blue-600 hover:bg-blue-100 active:bg-blue-200 focus:ring-blue-500'
    case 'secondary':
    default:
      return 'bg-white text-gray-700 border border-gray-200 hover:bg-gray-50 hover:border-gray-300 active:bg-gray-100 shadow-sm focus:ring-gray-300'
  }
})

const sizeClasses = computed(() => {
  switch (props.size) {
    case 'sm':
      return 'px-3 py-1.5 text-xs gap-1.5'
    case 'lg':
      return 'px-6 py-3 text-base gap-2.5'
    case 'md':
    default:
      return 'px-4 py-2 text-sm gap-2'
  }
})

const disabledClasses = computed(() => {
  return props.disabled || props.loading
    ? 'opacity-60 cursor-not-allowed transform-none'
    : 'cursor-pointer hover:-translate-y-0.5 active:translate-y-0'
})

const blockClasses = computed(() => props.block ? 'w-full flex' : 'inline-flex')
</script>

<template>
  <button
    :class="[baseClasses, variantClasses, sizeClasses, disabledClasses, blockClasses]"
    :disabled="disabled || loading"
    @click="(e) => !disabled && !loading && emit('click', e)"
  >
    <svg v-if="loading" class="animate-spin -ml-1 h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
      <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
      <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
    </svg>
    <slot v-if="!loading" name="icon" />
    <slot />
  </button>
</template>
