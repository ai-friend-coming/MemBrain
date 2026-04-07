import { ref } from 'vue'

export type ToastType = 'success' | 'error' | 'info' | 'warning'

export interface ToastOptions {
  message: string
  type?: ToastType
  duration?: number
}

export interface Toast extends ToastOptions {
  id: string
}

const toasts = ref<Toast[]>([])

export function useToast() {
  const removeToast = (id: string) => {
    const index = toasts.value.findIndex(t => t.id === id)
    if (index > -1) {
      toasts.value.splice(index, 1)
    }
  }

  const showToast = (options: ToastOptions | string) => {
    const defaultOptions: ToastOptions = {
      message: '',
      type: 'info',
      duration: 2000,
    }

    const toastOptions = typeof options === 'string'
      ? { ...defaultOptions, message: options }
      : { ...defaultOptions, ...options }

    const duration = toastOptions.duration ?? defaultOptions.duration!
    const id = crypto.randomUUID()

    toasts.value.push({ ...toastOptions, id, duration })

    if (duration > 0) {
      setTimeout(() => {
        removeToast(id)
      }, duration)
    }
  }

  const success = (message: string, duration?: number) => showToast({ message, type: 'success', duration })
  const error = (message: string, duration?: number) => showToast({ message, type: 'error', duration })
  const info = (message: string, duration?: number) => showToast({ message, type: 'info', duration })
  const warning = (message: string, duration?: number) => showToast({ message, type: 'warning', duration })

  return {
    toasts,
    showToast,
    removeToast,
    success,
    error,
    info,
    warning,
  }
}
