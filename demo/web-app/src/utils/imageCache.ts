import localforage from 'localforage'

const imageStore = localforage.createInstance({
  name: 'MemBrainMemory',
  storeName: 'character_images',
})

// In-memory cache of object URLs so createObjectURL is called at most once per source URL
const urlCache = new Map<string, string>()

export async function getCachedImageUrl(url: string | undefined): Promise<string | undefined> {
  if (!url)
    return undefined

  if (urlCache.has(url))
    return urlCache.get(url)!

  try {
    let blob: Blob | null = await imageStore.getItem(url)
    if (!blob) {
      const response = await fetch(url)
      if (!response.ok)
        throw new Error(`Failed to fetch image: ${response.statusText}`)
      blob = await response.blob()
      await imageStore.setItem(url, blob)
    }
    const objectUrl = URL.createObjectURL(blob)
    urlCache.set(url, objectUrl)
    return objectUrl
  }
  catch (err) {
    console.warn('Failed to load or cache image:', url, err)
    return url
  }
}
