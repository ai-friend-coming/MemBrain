import type { ClassValue } from 'clsx'
import { clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Render time annotations: [raw::resolved] → raw (resolved). */
export function formatFactText(text: string): string {
  return text.replace(/\[([^[\]:]+)::([^[\]]+)\]/g, '$1 ($2)')
}

/** Returns true if `pattern` is not a valid regex (or if regex/query are empty). */
export function isRegexInvalid(pattern: string, enabled: boolean): boolean {
  if (!enabled || !pattern)
    return false
  try {
    return !(new RegExp(pattern, 'i'))
  }
  catch {
    return true
  }
}
