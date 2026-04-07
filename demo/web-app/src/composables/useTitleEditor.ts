import type { Ref } from 'vue'
import { nextTick, ref } from 'vue'

export function useTitleEditor(
  currentSessionId: Ref<string | null>,
  currentTitle: Ref<string>,
  onSave: (sessionId: string, title: string) => Promise<void>,
) {
  const isEditingTitle = ref(false)
  const editingTitle = ref('')
  const titleInput = ref<HTMLInputElement | null>(null)

  async function startEditingTitle() {
    if (!currentSessionId.value)
      return
    editingTitle.value = currentTitle.value
    isEditingTitle.value = true
    await nextTick()
    titleInput.value?.focus()
  }

  function cancelEditingTitle() {
    isEditingTitle.value = false
    editingTitle.value = ''
  }

  async function saveTitle() {
    const newTitle = editingTitle.value.trim()
    if (newTitle && currentSessionId.value) {
      await onSave(currentSessionId.value, newTitle)
    }
    cancelEditingTitle()
  }

  function handleTitleKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter') {
      event.preventDefault()
      saveTitle()
    }
    else if (event.key === 'Escape') {
      cancelEditingTitle()
    }
  }

  return {
    isEditingTitle,
    editingTitle,
    titleInput,
    startEditingTitle,
    cancelEditingTitle,
    saveTitle,
    handleTitleKeydown,
  }
}
