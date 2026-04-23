// frontend/src/composables/useTypewriter.ts
import { ref, watch, type Ref } from 'vue'

export function useTypewriter(
  content: Ref<string>,
  speed: number = 10,
  onComplete?: () => void,
  skipAnimation: Ref<boolean> = ref(false),
  onChar?: () => void
) {
  const displayText = ref('')
  let timer: ReturnType<typeof setTimeout> | null = null
  let cacheQueue: string[] = []
  let lastContent = ''
  let isOutputting = false
  let skipped = false

  const threshold = 10

  const stopTimer = () => {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  const outputNextChar = () => {
    if (cacheQueue.length === 0) {
      stopTimer()
      isOutputting = false
      if (onComplete) onComplete()
      return
    }
    const char = cacheQueue.shift()
    if (char) {
      displayText.value += char
      if (onChar) onChar()
    }
    timer = setTimeout(outputNextChar, speed)
  }

  const tryStartOutput = () => {
    if (!isOutputting && cacheQueue.length >= threshold) {
      isOutputting = true
      outputNextChar()
    }
  }

  const enqueueNewContent = (newContent: string) => {
    if (skipAnimation.value) {
      displayText.value = newContent
      cacheQueue = []
      stopTimer()
      isOutputting = false
      return
    }

    // 1. 先计算增量
    let newPart = ''
    if (newContent.startsWith(lastContent)) {
      newPart = newContent.slice(lastContent.length)
    } else {
      cacheQueue = []
      displayText.value = ''
      stopTimer()
      isOutputting = false
      newPart = newContent
    }

    if (newPart.length === 0) return

    // 2. 如果已跳过动画，直接同步追加
    if (skipped) {
      displayText.value += newPart
      lastContent = newContent
      return
    }

    // 3. 正常入队
    const newChars = newPart.split('')
    cacheQueue.push(...newChars)
    lastContent = newContent
    tryStartOutput()
  }

  const flush = () => {
    if (cacheQueue.length > 0 && !isOutputting) {
      isOutputting = true
      outputNextChar()
    }
  }

  const reset = () => {
    stopTimer()
    displayText.value = ''
    cacheQueue = []
    lastContent = ''
    isOutputting = false
    skipped = false
  }

  const skip = () => {
    if (skipped) return
    skipped = true
    stopTimer()
    displayText.value = lastContent
    cacheQueue = []
    isOutputting = false
    if (onComplete) onComplete()
  }

  const cleanup = () => stopTimer()

  watch(content, (newContent) => {
    enqueueNewContent(newContent)
  }, { immediate: true })

  return { displayText, skip, cleanup, reset, flush }
}