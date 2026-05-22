<template>
  <Transition name="notice-fade">
    <div v-if="isVisible && activeReasons.length" class="availability-notice" role="status" aria-live="polite">
      <div class="notice-crest" aria-hidden="true">✦</div>
      <div class="notice-copy">
        <div class="notice-title">战术提示</div>
        <div ref="viewportRef" class="notice-viewport">
          <div v-if="shouldScroll" class="notice-track scrolling">
            <span class="notice-text">{{ activeText }}</span>
            <span class="notice-divider" aria-hidden="true">✦</span>
            <span class="notice-text notice-text-ghost" aria-hidden="true">{{ activeText }}</span>
          </div>
          <div v-else class="notice-track">
            <span class="notice-text">{{ activeText }}</span>
          </div>
          <span ref="measureRef" class="notice-measure">{{ activeText }}</span>
        </div>
      </div>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { AvailabilityReason } from '../../Services_/actionAvailabilityService'

const props = defineProps<{
  reasons: AvailabilityReason[]
  text: string
}>()

const viewportRef = ref<HTMLElement | null>(null)
const measureRef = ref<HTMLElement | null>(null)
const shouldScroll = ref(false)
const isVisible = ref(false)
const activeText = ref('')
const activeReasons = ref<AvailabilityReason[]>([])

let resizeObserver: ResizeObserver | null = null
let hideTimer: ReturnType<typeof setTimeout> | null = null

const NOTICE_VISIBLE_MS = 2400

/**
 * 只有文案真实溢出时才开启滚动，避免短提示也像跑马灯一样喧宾夺主。
 */
const syncOverflowState = () => {
  const viewport = viewportRef.value
  const measure = measureRef.value
  if (!viewport || !measure) {
    shouldScroll.value = false
    return
  }
  shouldScroll.value = measure.scrollWidth > viewport.clientWidth
}

const clearHideTimer = () => {
  if (!hideTimer) return
  clearTimeout(hideTimer)
  hideTimer = null
}

/**
 * 提示条按“事件”展示，而不是常驻状态：
 * 每次文案变化就闪现一次，展示一小段时间后自动淡出。
 */
const showNotice = async (text: string, reasons: AvailabilityReason[]) => {
  clearHideTimer()
  activeText.value = text
  activeReasons.value = reasons
  isVisible.value = true

  await nextTick()
  syncOverflowState()

  hideTimer = setTimeout(() => {
    isVisible.value = false
    hideTimer = null
  }, NOTICE_VISIBLE_MS)
}

const hideNoticeImmediately = () => {
  clearHideTimer()
  isVisible.value = false
  activeText.value = ''
  activeReasons.value = []
  shouldScroll.value = false
}

// 文案变化时触发一次完整展示周期；内容清空时立即收起并清理现场。
watch(
  () => props.text,
  async (text) => {
    if (!text) {
      hideNoticeImmediately()
      return
    }

    await showNotice(text, props.reasons)
  },
  { immediate: true },
)

// 尺寸监听独立收口在组件内，外部页面不需要知道跑马灯的任何实现细节。
onMounted(() => {
  resizeObserver = new ResizeObserver(() => {
    syncOverflowState()
  })

  if (viewportRef.value) resizeObserver.observe(viewportRef.value)
  if (measureRef.value) resizeObserver.observe(measureRef.value)
  syncOverflowState()
})

onBeforeUnmount(() => {
  clearHideTimer()
  resizeObserver?.disconnect()
  resizeObserver = null
})
</script>

<style scoped>
.availability-notice {
  position: relative;
  margin: 14px 24px 6px;
  min-height: 58px;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 12px 18px;
  border-radius: 14px;
  background:
    linear-gradient(180deg, rgba(47, 38, 26, 0.94) 0%, rgba(28, 24, 20, 0.98) 100%);
  border: 1px solid rgba(201, 161, 98, 0.42);
  box-shadow:
    0 14px 34px rgba(0, 0, 0, 0.34),
    0 0 0 1px rgba(255, 244, 214, 0.04) inset,
    0 0 22px rgba(190, 144, 68, 0.08);
  overflow: hidden;
}

.availability-notice::before {
  content: '';
  position: absolute;
  inset: 0;
  background:
    linear-gradient(90deg, rgba(201, 161, 98, 0.06) 0%, transparent 22%, transparent 78%, rgba(201, 161, 98, 0.05) 100%);
  pointer-events: none;
}

.availability-notice::after {
  content: '';
  position: absolute;
  inset: 8px;
  border-radius: 10px;
  border: 1px solid rgba(255, 235, 196, 0.05);
  pointer-events: none;
}

.notice-crest {
  position: relative;
  z-index: 1;
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  color: #f1ddb1;
  background: radial-gradient(circle, rgba(207, 166, 93, 0.34) 0%, rgba(207, 166, 93, 0.08) 72%, transparent 100%);
  text-shadow: 0 0 10px rgba(237, 196, 118, 0.32);
}

.notice-copy {
  position: relative;
  z-index: 1;
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.notice-title {
  color: #d8bc8f;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  font-family: 'Cinzel', 'UnifrakturMaguntia', 'Georgia', serif;
}

.notice-viewport {
  position: relative;
  overflow: hidden;
  white-space: nowrap;
}

.notice-track {
  display: inline-flex;
  align-items: center;
  min-width: 100%;
  color: #f5ead4;
  font-size: 13px;
  line-height: 1.45;
}

.notice-track.scrolling {
  width: max-content;
  min-width: max-content;
  animation: notice-marquee 16s linear infinite;
}

.notice-text {
  display: inline-block;
}

.notice-text-ghost {
  padding-right: 8px;
}

.notice-divider {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 18px;
  color: rgba(236, 210, 156, 0.82);
}

.notice-measure {
  position: absolute;
  visibility: hidden;
  pointer-events: none;
  white-space: nowrap;
}

.notice-fade-enter-active,
.notice-fade-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.notice-fade-enter-from,
.notice-fade-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}

@keyframes notice-marquee {
  0% {
    transform: translateX(0);
  }
  100% {
    transform: translateX(calc(-50% - 12px));
  }
}

@media (max-width: 768px) {
  .availability-notice {
    margin: 12px 16px 4px;
    padding: 11px 14px;
  }

  .notice-title {
    letter-spacing: 0.14em;
  }
}
</style>
