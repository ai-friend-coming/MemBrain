<script setup lang="ts">
import { onClickOutside } from '@vueuse/core'
import { computed, ref } from 'vue'

export interface DropdownOption {
  value: string | null
  label: string
}

const props = withDefaults(defineProps<{
  options: DropdownOption[]
  modelValue: string | null
  placeholder?: string
}>(), {
  placeholder: 'Select...',
})

const emit = defineEmits<{
  (e: 'update:modelValue', value: string | null): void
}>()

const isOpen = ref(false)
const containerRef = ref<HTMLElement | null>(null)
onClickOutside(containerRef, () => { isOpen.value = false })

const currentLabel = computed(
  () => props.options.find(o => o.value === props.modelValue)?.label ?? props.placeholder,
)

function toggle(e: Event) {
  e.stopPropagation()
  isOpen.value = !isOpen.value
}

function select(value: string | null) {
  emit('update:modelValue', value)
  isOpen.value = false
}
</script>

<template>
  <div ref="containerRef" class="ds-container">
    <button class="ds-trigger" :class="{ 'is-open': isOpen }" :title="currentLabel" @click="toggle">
      <span class="ds-label">{{ currentLabel }}</span>
      <svg class="ds-arrow" viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="6 9 12 15 18 9" />
      </svg>
    </button>
    <div v-if="isOpen" class="ds-menu" @click.stop>
      <button
        v-for="opt in options"
        :key="String(opt.value)"
        class="ds-item"
        :class="{ 'is-active': opt.value === modelValue }"
        @click="select(opt.value)"
      >
        {{ opt.label }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.ds-container { position: relative; display: flex; align-items: center; }

.ds-trigger {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  background: var(--c-bg);
  border: 1px solid var(--c-border);
  border-radius: 9999px;
  color: var(--c-text-4);
  font-size: var(--text-ui);
  font-weight: var(--fw-medium);
  padding: 0 0.75rem;
  height: 2rem;
  width: 100%;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.02);
  font-family: inherit;
}
.ds-trigger:hover, .ds-trigger.is-open { border-color: var(--c-border-strong); color: var(--c-text); }

.ds-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: left;
}

.ds-arrow {
  color: var(--c-text-muted);
  flex-shrink: 0;
  transition: transform 0.2s;
}
.ds-trigger.is-open .ds-arrow { transform: rotate(180deg); }

.ds-menu {
  position: absolute;
  right: 0;
  top: calc(100% + 0.5rem);
  background: var(--c-bg);
  border: 1px solid var(--c-border);
  border-radius: 0.75rem;
  box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.08), 0 4px 6px -2px rgba(0, 0, 0, 0.04);
  z-index: 100;
  min-width: 160px;
  max-height: 280px;
  overflow-y: auto;
  padding: 0.375rem;
  animation: ds-in 0.15s cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes ds-in {
  from { opacity: 0; transform: translateY(-4px) scale(0.97); }
  to   { opacity: 1; transform: none; }
}

.ds-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 0.5rem 0.625rem;
  border: none;
  background: transparent;
  color: var(--c-text-4);
  font-size: var(--text-ui);
  font-weight: var(--fw-medium);
  border-radius: 0.5rem;
  cursor: pointer;
  transition: background-color 0.15s, color 0.15s;
  font-family: inherit;
  word-break: break-word;
}
.ds-item:hover { background: var(--c-bg-muted); color: var(--c-text); }
.ds-item.is-active { background: var(--c-accent-bg); color: var(--c-accent-fg); font-weight: var(--fw-semibold); }
</style>
