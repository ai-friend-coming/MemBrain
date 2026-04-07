<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost' | 'soft'
  size?: 'sm' | 'md' | 'lg'
  disabled?: boolean
  loading?: boolean
  circle?: boolean
  title?: string
}>(), {
  variant: 'ghost',
  size: 'md',
  disabled: false,
  loading: false,
  circle: true,
  title: '',
})

const emit = defineEmits<{
  click: [event: MouseEvent]
}>()

const baseClasses = 'inline-flex items-center justify-center transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-offset-1 select-none flex-shrink-0'

const shapeClasses = computed(() => props.circle ? 'rounded-full' : 'rounded-lg')

const variantClasses = computed(() => {
  switch (props.variant) {
    case 'primary':
      return 'bg-blue-600 text-white hover:bg-blue-700 active:bg-blue-800 focus:ring-blue-500 shadow-sm'
    case 'danger':
      return 'bg-red-50 text-red-600 hover:bg-red-100 active:bg-red-200 focus:ring-red-500'
    case 'secondary':
      return 'bg-white text-gray-700 border border-gray-200 hover:bg-gray-50 active:bg-gray-100 shadow-sm focus:ring-gray-300'
    case 'soft':
      return 'bg-blue-50 text-blue-600 hover:bg-blue-100 active:bg-blue-200 focus:ring-blue-500'
    case 'ghost':
    default:
      return 'bg-transparent text-gray-500 hover:text-gray-900 hover:bg-gray-100 active:bg-gray-200 focus:ring-gray-300'
  }
})

const sizeClasses = computed(() => {
  switch (props.size) {
    case 'sm':
      return 'p-1.5 w-7 h-7'
    case 'lg':
      return 'p-2.5 w-11 h-11'
    case 'md':
    default:
      return 'p-2 w-9 h-9'
  }
})

const disabledClasses = computed(() => {
  return props.disabled || props.loading
    ? 'opacity-50 cursor-not-allowed transform-none'
    : 'cursor-pointer hover:-translate-y-0.5 active:translate-y-0'
})
</script>

<template>
  <button
    :class="[baseClasses, shapeClasses, variantClasses, sizeClasses, disabledClasses]"
    :disabled="disabled || loading"
    :title="title"
    @click="(e) => !disabled && !loading && emit('click', e)"
  >
    <svg v-if="loading" class="animate-spin h-full w-full" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
      <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
      <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
    </svg>
    <slot v-else />
  </button>
</template>
