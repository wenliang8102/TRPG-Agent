// frontend/src/composables/useTypewriter.ts
import { ref, watch, type Ref } from 'vue'

export function useTypewriter(
  content: Ref<string>,        // 后端推送的原始内容（会不断增长）
  speed: number = 10,          // 毫秒/字符，例如 35ms/字 → 约28字/秒
  onComplete?: () => void,
  skipAnimation: Ref<boolean> = ref(false),
  onChar?: () => void          // 🔥 新增：每输出一个字符时触发
) {
  const displayText = ref('')
  let timer: ReturnType<typeof setTimeout> | null = null
  let cacheQueue: string[] = []      // 字符缓存池（待输出的字符队列）
  let lastContent = ''               // 上一次接收到的完整内容（用于计算增量）
  let isOutputting = false           // 是否正在输出中

  // 计算每秒可输出的字符数，作为启动阈值（至少为1）
  const charsPerSecond = Math.max(1, Math.floor(1000 / speed))
  const threshold = 10 // 缓存池积累到该数量时开始输出

  const stopTimer = () => {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  // 从缓存池取出一个字符并显示
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
      // 🔥 每输出一个字符，调用外部传入的回调（用于滚动）
      if (onChar) onChar()
    }
    timer = setTimeout(outputNextChar, speed)
  }

  // 尝试启动输出（只有当缓存池达到阈值且尚未输出时才启动）
  const tryStartOutput = () => {
    console.log('[tryStartOutput] isOutputting:', isOutputting, 'queue length:', cacheQueue.length, 'threshold:', threshold)
    if (!isOutputting && cacheQueue.length >= threshold) {
      isOutputting = true
      outputNextChar()
    }
  }

  // 将后端新增的内容加入缓存池
  const enqueueNewContent = (newContent: string) => {
    if (skipAnimation.value) {
      // 历史消息或用户消息：直接显示全部，不清除缓存池
      displayText.value = newContent
      cacheQueue = []
      stopTimer()
      isOutputting = false
      return
    }

    // 计算增量部分（假设后端每次推送的内容是累积的）
    let newPart = ''
    if (newContent.startsWith(lastContent)) {
      newPart = newContent.slice(lastContent.length)
    } else {
      // 新消息开始（内容不连续），清空旧缓存，重新开始
      cacheQueue = []
      displayText.value = ''
      stopTimer()
      isOutputting = false
      newPart = newContent
    }

    if (newPart.length === 0) return

    // 将新增部分拆成单个字符放入缓存池
    const newChars = newPart.split('')
    cacheQueue.push(...newChars)
    lastContent = newContent

    // 尝试启动输出
    tryStartOutput()
  }

  // 强制刷新：立即输出剩余缓存（用于后端 done 事件）
  const flush = () => {
    if (cacheQueue.length > 0 && !isOutputting) {
      isOutputting = true
      outputNextChar()
    }
  }

  // 重置所有状态（新消息开始时调用）
  const reset = () => {
    stopTimer()
    displayText.value = ''
    cacheQueue = []
    lastContent = ''
    isOutputting = false
  }

  // 跳过动画，直接显示全部（用户点击跳过）
  const skip = () => {
    stopTimer()
    displayText.value = lastContent
    cacheQueue = []
    isOutputting = false
    if (onComplete) onComplete()
  }

  const cleanup = () => stopTimer()

  // 监听后端推送的内容变化
  watch(content, (newContent) => {
    enqueueNewContent(newContent)
  }, { immediate: true })

  return { displayText, skip, cleanup, reset, flush }
}