<script setup lang="ts">
import type { SessionMetadata, User as UserData } from '@/types'
import type { Persona } from '@/utils/character'
import { ArrowLeft, Plus, Settings, Trash2 } from 'lucide-vue-next'
import { ref } from 'vue'
import momoAvatar from '@/assets/avatar/momo.jpeg'
import ConfigModal from '@/components/ConfigModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import IconButton from '@/components/ui/IconButton.vue'
import { useCharacterAvatar } from '@/composables/useCharacterAvatar'

const props = defineProps<{
  sessions: SessionMetadata[]
  currentSessionId: string | null
  currentUser: UserData
  currentPersona: Persona
}>()

const emit = defineEmits<{
  createSession: []
  deleteSession: [sessionId: string]
  switchSession: [sessionId: string]
  switchCharacter: []
}>()

const configVisible = ref(false)

function handleDeleteSession(sessionId: string, event: Event) {
  event.stopPropagation()
  // eslint-disable-next-line no-alert
  if (window.confirm('Are you sure you want to delete this session?')) {
    emit('deleteSession', sessionId)
  }
}

function handleSwitchSession(sessionId: string) {
  emit('switchSession', sessionId)
}

function formatDate(date: string): string {
  if (!date)
    return 'Unknown'
  const sessionDate = new Date(date.includes('Z') || date.includes('+') ? date : `${date}Z`)
  if (Number.isNaN(sessionDate.getTime()))
    return 'Invalid date'

  const diff = Date.now() - sessionDate.getTime()
  if (diff < 0 || diff < 60_000)
    return 'Just now'

  const minutes = Math.floor(diff / 60_000)
  if (minutes < 60)
    return `${minutes} minute${minutes > 1 ? 's' : ''} ago`

  const hours = Math.floor(diff / 3_600_000)
  if (hours < 24)
    return `${hours} hour${hours > 1 ? 's' : ''} ago`

  const days = Math.floor(diff / 86_400_000)
  if (days === 1)
    return 'Yesterday'
  if (days < 7)
    return `${days} days ago`

  return sessionDate.toLocaleDateString()
}

const currentAvatarUrl = useCharacterAvatar(() => props.currentPersona?.avatarImg)
</script>

<template>
  <div class="session-sidebar">
    <div class="sidebar-header">
      <BaseButton variant="ghost" block class="justify-start !px-3" @click="emit('createSession')">
        <Plus class="w-5 h-5 mr-1" />
        <span>New Chat</span>
      </BaseButton>
      <BaseButton variant="ghost" block class="justify-start !px-3" @click="configVisible = true">
        <Settings class="w-5 h-5 mr-1" />
        <span>Settings</span>
      </BaseButton>
    </div>

    <div class="sessions-list">
      <div
        v-for="session in sessions"
        :key="session.id"
        class="session-item" :class="[{ active: session.id === currentSessionId }]"
        @click="handleSwitchSession(session.id)"
      >
        <div class="session-content">
          <div class="session-info">
            <div class="session-title">
              {{ session.title || "New Chat" }}
            </div>
            <div class="session-date">
              {{ formatDate(session.updatedAt || session.createdAt) }}
            </div>
          </div>
        </div>
        <IconButton
          class="delete-btn"
          variant="ghost"
          size="sm"
          title="Delete session"
          @click.stop="handleDeleteSession(session.id, $event)"
        >
          <Trash2 class="w-4 h-4" />
        </IconButton>
      </div>
    </div>

    <div class="user-info-section">
      <div class="user-info">
        <div
          v-if="currentAvatarUrl"
          class="user-avatar overflow-hidden border border-gray-200 shadow-sm flex-shrink-0"
        >
          <img :src="currentAvatarUrl" class="w-full h-full object-cover" alt="User Avatar">
        </div>
        <div v-else class="user-avatar overflow-hidden flex-shrink-0">
          <img :src="momoAvatar" class="w-full h-full object-cover" alt="Avatar">
        </div>
        <div class="user-details flex flex-col justify-center">
          <div class="user-name">
            {{ currentPersona.characterName || 'Unnamed' }}
          </div>
          <div class="user-alias flex items-center gap-1">
            <span class="text-gray-400">@</span>{{ currentPersona.userAlias }}
          </div>
        </div>
        <IconButton
          title="Go Back"
          class="switch-user-btn"
          variant="ghost"
          @click="emit('switchCharacter')"
        >
          <ArrowLeft class="w-5 h-5" />
        </IconButton>
      </div>
    </div>

    <ConfigModal v-model:visible="configVisible" :user-id="currentUser.id" :persona-id="currentPersona.id" :current-persona="currentPersona" />
  </div>
</template>

<style scoped>
.session-sidebar {
  width: 260px;
  background-color: #f7f7f8;
  border-right: 1px solid #e5e7eb;
  display: flex;
  flex-direction: column;
  height: 100%;
}

.sidebar-header {
  padding: 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.sessions-list {
  flex: 1;
  overflow-y: auto;
  padding: 0.5rem;
}

.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem;
  margin-bottom: 0.25rem;
  border-radius: 0.5rem;
  cursor: pointer;
  transition: background-color 0.2s;
  position: relative;
}

.session-item:hover {
  background-color: #e5e7eb;
}

.session-item.active {
  background-color: #dbeafe;
}

.session-content {
  display: flex;
  align-items: center;
  flex: 1;
  min-width: 0;
}

.session-info {
  flex: 1;
  min-width: 0;
}

.session-title {
  font-size: 0.875rem;
  font-weight: 500;
  color: #111827;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 1.25rem;
  height: 1.25rem;
}

.session-date {
  font-size: 0.75rem;
  color: #6b7280;
  margin-top: 0.125rem;
}

.delete-btn {
  opacity: 0;
  flex-shrink: 0;
  transition: all 0.2s;
  color: #6b7280;
}

.session-item:hover .delete-btn {
  opacity: 1;
}

.delete-btn:hover {
  color: #ef4444 !important;
  background-color: #fee2e2 !important;
}

/* Scrollbar styling */
.sessions-list::-webkit-scrollbar {
  width: 6px;
}

.sessions-list::-webkit-scrollbar-track {
  background: transparent;
}

.sessions-list::-webkit-scrollbar-thumb {
  background: #d1d5db;
  border-radius: 3px;
}

.sessions-list::-webkit-scrollbar-thumb:hover {
  background: #9ca3af;
}

/* User info section */
.user-info-section {
  background-color: #f7f7f8;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem;
  width: 100%;
}

.user-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  flex-shrink: 0;
}

.user-details {
  flex: 1;
  min-width: 0;
}

.user-name {
  font-size: 0.875rem;
  font-weight: 600;
  color: #111827;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.user-alias {
  font-size: 0.75rem;
  color: #6b7280;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.switch-user-btn {
  width: 36px;
  height: 36px;
}
</style>
