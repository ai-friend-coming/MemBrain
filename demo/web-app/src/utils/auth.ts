import type { User } from '@/types'
import { AUTH_EXPIRY_MS, AUTH_STORAGE_KEY } from '@/constants'

export function getCurrentUser(): User | null {
  try {
    const authData = localStorage.getItem(AUTH_STORAGE_KEY)
    if (!authData)
      return null

    const user: User = JSON.parse(authData)
    const now = Date.now()
    const loginTime = user.loginTime || 0
    if (now - loginTime > AUTH_EXPIRY_MS) {
      logout()
      return null
    }

    return user
  }
  catch (error) {
    console.error('Error reading auth data:', error)
    return null
  }
}

export function login(userId: string): User {
  const user: User = {
    id: userId.trim(),
    loginTime: Date.now(),
  }

  try {
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(user))
    return user
  }
  catch (error) {
    console.error('Error saving auth data:', error)
    throw error
  }
}

export function logout(): void {
  try {
    localStorage.removeItem(AUTH_STORAGE_KEY)
  }
  catch (error) {
    console.error('Error removing auth data:', error)
  }
}

