import { ref, watch } from 'vue'
import { getCachedImageUrl } from '@/utils/imageCache'

export function useCharacterAvatar(getAvatarImg: () => string | undefined) {
  const avatarUrl = ref('')

  watch(getAvatarImg, async (img) => {
    avatarUrl.value = img ? (await getCachedImageUrl(img)) ?? '' : ''
  }, { immediate: true })

  return avatarUrl
}
