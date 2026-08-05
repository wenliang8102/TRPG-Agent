<template>
  <Transition name="reply-suggestions">
    <div v-if="loading || suggestions.length" class="reply-suggestions-shell">
      <div class="reply-suggestions-clip">
        <div class="reply-suggestions" aria-live="polite">
          <div class="suggestions-toolbar">
            <Sparkles class="suggestions-mark" :size="15" aria-hidden="true" />
            <span class="suggestions-divider" aria-hidden="true" />
            <button
              v-if="!loading"
              type="button"
              class="refresh-button"
              :disabled="disabled"
              title="重新生成候选回复"
              aria-label="重新生成候选回复"
              @click="$emit('refresh')"
            >
              <RefreshCw :size="14" />
            </button>
          </div>

          <div v-if="loading" class="suggestion-list" aria-label="正在生成候选回复">
            <span v-for="index in 3" :key="index" class="suggestion-skeleton">
              <span class="suggestion-number" aria-hidden="true">{{ String(index).padStart(2, '0') }}</span>
              <span class="skeleton-line" />
            </span>
          </div>
          <div v-else class="suggestion-list">
            <button
              v-for="(suggestion, index) in suggestions"
              :key="suggestion"
              type="button"
              class="suggestion-button"
              :disabled="disabled"
              @click="$emit('select', suggestion)"
            >
              <span class="suggestion-number" aria-hidden="true">{{ String(index + 1).padStart(2, '0') }}</span>
              <span class="suggestion-copy">{{ suggestion }}</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { RefreshCw, Sparkles } from 'lucide-vue-next'

defineProps<{
  suggestions: string[]
  loading: boolean
  disabled: boolean
}>()

defineEmits<{
  select: [suggestion: string]
  refresh: []
}>()
</script>

<style scoped>
.reply-suggestions-shell {
  display: grid;
  grid-template-rows: 1fr;
  margin-top: 12px;
}

.reply-suggestions-clip {
  min-height: 0;
  overflow: hidden;
}

.reply-suggestions-enter-active,
.reply-suggestions-leave-active {
  transition:
    grid-template-rows 260ms ease,
    margin-top 260ms ease,
    opacity 200ms ease,
    transform 260ms ease;
}

.reply-suggestions-enter-from,
.reply-suggestions-leave-to {
  grid-template-rows: 0fr;
  margin-top: 0;
  opacity: 0;
  transform: translateY(6px);
}

.reply-suggestions {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 0 4px;
}

.suggestions-toolbar {
  display: flex;
  align-items: center;
  height: 24px;
  gap: 9px;
  padding: 0 2px 0 7px;
}

.suggestions-mark {
  flex: none;
  color: rgba(215, 183, 109, 0.74);
}

.suggestions-divider {
  height: 1px;
  flex: 1;
  background: rgba(215, 183, 109, 0.13);
}

.suggestion-list {
  display: flex;
  flex-direction: column;
  flex: 1;
  width: 100%;
  min-width: 0;
}

.suggestion-button {
  position: relative;
  display: grid;
  grid-template-columns: 29px minmax(0, 1fr);
  align-items: start;
  gap: 7px;
  width: 100%;
  min-height: 38px;
  padding: 9px 10px 10px 5px;
  border: 0;
  border-radius: 2px;
  background: transparent;
  color: rgba(236, 232, 222, 0.9);
  font: inherit;
  font-size: calc(13px * var(--chat-font-scale, 100) / 100);
  line-height: 1.45;
  text-align: left;
  overflow-wrap: anywhere;
  cursor: pointer;
  animation: suggestion-item-enter 220ms ease both;
  transition: background-color 0.16s ease, color 0.16s ease;
}

.suggestion-button:nth-child(2) {
  animation-delay: 35ms;
}

.suggestion-button:nth-child(3) {
  animation-delay: 70ms;
}

.suggestion-button::after,
.suggestion-skeleton::after {
  position: absolute;
  right: 10px;
  bottom: 0;
  left: 41px;
  height: 1px;
  background: rgba(255, 255, 255, 0.055);
  content: '';
}

.suggestion-button:last-child::after,
.suggestion-skeleton:last-child::after {
  display: none;
}

.suggestion-number {
  padding-top: 2px;
  color: rgba(215, 183, 109, 0.48);
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 10px;
  line-height: 1.5;
  text-align: right;
}

.suggestion-copy {
  min-width: 0;
}

.suggestion-button:hover:not(:disabled),
.suggestion-button:focus-visible {
  background: rgba(215, 183, 109, 0.06);
  color: #fffaf0;
  outline: none;
}

.suggestion-button:hover:not(:disabled) .suggestion-number,
.suggestion-button:focus-visible .suggestion-number {
  color: rgba(240, 215, 147, 0.9);
}

.suggestion-button:disabled,
.refresh-button:disabled {
  opacity: 0.5;
  cursor: default;
}

.refresh-button {
  display: grid;
  width: 24px;
  height: 24px;
  place-items: center;
  padding: 0;
  border: 0;
  background: transparent;
  color: #aaa69c;
  cursor: pointer;
}

.refresh-button:hover:not(:disabled),
.refresh-button:focus-visible {
  color: #f0d793;
  outline: none;
}

.suggestion-skeleton {
  position: relative;
  display: grid;
  grid-template-columns: 29px minmax(0, 1fr);
  align-items: center;
  gap: 7px;
  width: 100%;
  height: 38px;
  padding: 0 10px 0 5px;
}

.skeleton-line {
  height: 6px;
  border-radius: 2px;
  background: linear-gradient(90deg, rgba(255, 255, 255, 0.035), rgba(255, 255, 255, 0.105), rgba(255, 255, 255, 0.035));
  background-size: 200% 100%;
  animation: suggestion-loading 1.2s ease-in-out infinite;
}

.suggestion-skeleton:nth-child(1) .skeleton-line { width: 76%; }
.suggestion-skeleton:nth-child(2) .skeleton-line { width: 91%; }
.suggestion-skeleton:nth-child(3) .skeleton-line { width: 64%; }

@keyframes suggestion-item-enter {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes suggestion-loading {
  from { background-position: 200% 0; }
  to { background-position: -200% 0; }
}

@media (max-width: 700px) {
  .suggestion-button {
    grid-template-columns: 25px minmax(0, 1fr);
    gap: 6px;
    padding-right: 7px;
  }

  .suggestion-button::after,
  .suggestion-skeleton::after {
    left: 36px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .reply-suggestions-enter-active,
  .reply-suggestions-leave-active {
    transition-duration: 1ms;
  }

  .suggestion-button,
  .skeleton-line {
    animation-duration: 1ms;
    animation-delay: 0ms;
  }
}
</style>
