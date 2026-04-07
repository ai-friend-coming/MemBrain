<script setup lang="ts">
import { User } from 'lucide-vue-next'
import { ref } from 'vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { LOGIN_DELAY_MS } from '@/constants'

const emit = defineEmits<{
  login: [userId: string]
}>()

const userId = ref('')
const loading = ref(false)

function handleLogin() {
  if (!userId.value.trim())
    return

  loading.value = true
  setTimeout(() => {
    emit('login', userId.value.trim())
    loading.value = false
  }, LOGIN_DELAY_MS)
}

function handleKeyPress(event: KeyboardEvent) {
  if (event.key === 'Enter')
    handleLogin()
}
</script>

<template>
  <div class="flex items-center justify-center w-screen h-screen bg-gray-50/50 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-blue-50/80 via-white to-white font-sans text-gray-900 border-none">
    <div class="bg-white/80 backdrop-blur-xl border border-gray-100 rounded-3xl shadow-xl p-10 w-full max-w-sm animate-[slideIn_0.3s_ease-out]">
      <div class="text-center mb-8 flex flex-col items-center">
        <div class="w-16 h-16 bg-blue-50 text-blue-600 rounded-2xl flex items-center justify-center mb-4 rotate-3 shadow-sm border border-blue-100">
          <User class="w-8 h-8" />
        </div>
        <h1 class="text-2xl font-bold tracking-tight text-gray-900 mb-2">
          Nieta.art Agent
        </h1>
        <p class="text-sm text-gray-500">
          Please enter your user ID to continue
        </p>
      </div>

      <div class="flex flex-col gap-4">
        <div class="relative">
          <div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
            <User class="h-5 w-5 text-gray-400" />
          </div>
          <input
            v-model="userId"
            type="text"
            class="block w-full pl-10 pr-3 py-3 border border-gray-200 rounded-xl leading-5 bg-white placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 sm:text-sm transition-shadow"
            placeholder="Enter your user ID"
            autofocus
            @keypress="handleKeyPress"
          >
        </div>

        <BaseButton
          variant="primary"
          class="w-full h-11 text-base font-semibold"
          :loading="loading"
          :disabled="!userId.trim()"
          @click="handleLogin"
        >
          {{ loading ? "Logging in..." : "Login" }}
        </BaseButton>
      </div>
    </div>
  </div>
</template>

<style scoped>
@keyframes slideIn {
  from {
    opacity: 0;
    transform: translateY(-20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
