<script setup lang="ts">
import type { User } from '@/types'
import type { Persona } from '@/utils/character'
import { Loader2 } from 'lucide-vue-next'
import { onMounted, ref } from 'vue'
import ToastContainer from '@/components/ui/ToastContainer.vue'
import { listPersonas } from '@/services/api'
import { getCurrentUser, login, logout } from '@/utils/auth'
import { clearCurrentPersonaId, getCurrentPersonaId } from '@/utils/character'
import CharacterSelectView from '@/views/CharacterSelectView.vue'
import ChatInterface from '@/views/ChatInterface.vue'
import LoginPage from '@/views/LoginPage.vue'

const currentUser = ref<User | null>(null)
const currentPersona = ref<Persona | null>(null)
const personaLoading = ref(false)

onMounted(async () => {
  currentUser.value = getCurrentUser()
  if (currentUser.value) {
    const personaId = getCurrentPersonaId(currentUser.value.id)
    if (personaId) {
      personaLoading.value = true
      try {
        const personas = await listPersonas(currentUser.value.id)
        currentPersona.value = personas.find(p => p.id === personaId) ?? null
        if (!currentPersona.value)
          clearCurrentPersonaId(currentUser.value.id)
      }
      finally {
        personaLoading.value = false
      }
    }
  }
})

function handleLogin(userId: string) {
  try {
    currentUser.value = login(userId)
  }
  catch (error) {
    console.error('Login failed:', error)
  }
}

function handlePersonaSelect(persona: Persona) {
  currentPersona.value = persona
}

function handleSwitchUser() {
  logout()
  currentPersona.value = null
  currentUser.value = null
}

function handleSwitchCharacter() {
  if (currentUser.value)
    clearCurrentPersonaId(currentUser.value.id)
  currentPersona.value = null
}
</script>

<template>
  <LoginPage v-if="!currentUser" @login="handleLogin" />
  <div v-else-if="personaLoading" class="loading-screen bg-gray-50">
    <Loader2 class="w-10 h-10 text-blue-500 animate-spin" />
  </div>
  <CharacterSelectView
    v-else-if="!currentPersona"
    :user-id="currentUser.id"
    @select="handlePersonaSelect"
    @switch-user="handleSwitchUser"
  />
  <div v-else class="app-container">
    <ChatInterface
      :current-user="currentUser"
      :current-persona="currentPersona"
      @switch-character="handleSwitchCharacter"
    />
  </div>
  <ToastContainer />
</template>

<style scoped>
.app-container {
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  display: flex;
}

.loading-screen {
  width: 100vw;
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
