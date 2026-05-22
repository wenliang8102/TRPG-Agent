<template>
  <div class="welcome-page" ref="rootRef">
    <!-- 极简右上角控制 -->
    <div class="page-controls">
      <button 
        v-if="!isAllShown" 
        class="skip-btn" 
        @click="showAllContent"
        title="跳过动画"
      >
        <span class="skip-text">跳过</span>
        <ChevronRightIcon :size="16" />
      </button>
    </div>

    <!-- 标题：直接显示，大气 -->
    <h1 class="welcome-title">龙与地下城</h1>

    <!-- 正文内容将动态插入此处，无容器包裹 -->
    <div class="welcome-body" ref="bodyRef"></div>

    <!-- 游戏开始按钮（最后淡入） -->
    <transition name="fade-scale">
      <div v-if="showStartButton" class="game-start-wrapper">
        <div class="game-start" @click="handleStartGame">游戏开始</div>
      </div>
    </transition>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { ChevronRight } from 'lucide-vue-next'

const emit = defineEmits(['navigate'])
const rootRef = ref(null)
const bodyRef = ref(null)
const isAllShown = ref(false)
const showStartButton = ref(false)
let timeouts = []
let animationFrame = null

// 更具故事感的正文文案
const bodyLines = [
  "你翻开了一页泛黄的羊皮纸，尘埃在微光中浮沉",
  "它记录了一段古老的传说",
  "你即将踏入的大陆，没有统一的名字。精灵称它“歌唱之地”，矮人唤它“磐石之骨”，而人类，只是简单地叫它：世界。",
  "这里有喧嚣的港口、寂静的森林，以及被藤蔓与遗忘吞没的废墟。每一座废墟下都埋着故事，每一个故事里都藏着一件宝物——而每一件宝物的背后，通常都守着一头饥饿的怪物。",
  "你会遇见人类、精灵、矮人，还有龙裔与提夫林……每个种族都带着骄傲与伤痕，正如你一样。",
  "神明并非传说。圣武士的剑因誓言燃烧，牧师的祈祷能撕裂天空。但神明从不亲自下场——他们看着，然后让你自己选择。",
  "魔法源自一张看不见的网。你拨动它，现实便随之改变。但你的精神有限，魔网的琴弦每日只能弹响那么几次。",
  "黑暗也从未远去。幽暗地域的凝视，巨龙金币堆上的梦，邪教徒低语的禁忌之名……文明的火光，其实一直摇摇欲坠。",
  "而你，年轻的勇者",
  "命运的齿轮已经转动",
  "你的故事即将开始……"
]

const LINE_DURATION = 1200        // 每行动画时长增加至1.2秒
const CHAR_DELAY_FACTOR = 0.8     // 字符间延迟系数增大，使动画更慢

// 清理所有定时器
const clearAllTimeouts = () => {
  timeouts.forEach(t => clearTimeout(t))
  timeouts = []
  if (animationFrame) {
    cancelAnimationFrame(animationFrame)
    animationFrame = null
  }
}

// 直接显示全部内容（跳过动画）
const showAllContent = () => {
  if (isAllShown.value) return
  
  clearAllTimeouts()
  const container = bodyRef.value
  if (!container) return
  
  container.innerHTML = ''
  
  bodyLines.forEach(text => {
    const lineDiv = document.createElement('div')
    lineDiv.className = 'story-line'
    const span = document.createElement('span')
    span.className = 'story-char'
    span.textContent = text
    span.style.opacity = '1'
    span.style.transform = 'translateY(0)'
    span.style.filter = 'blur(0)'
    lineDiv.appendChild(span)
    container.appendChild(lineDiv)
  })
  
  isAllShown.value = true
  showStartButton.value = true
}

// 处理游戏开始点击
const handleStartGame = () => {
  emit('navigate', 'chat')
}

// 带有MG感的逐字动画
const animateBodyLines = () => {
  const container = bodyRef.value
  if (!container) return
  
  let cumulativeDelay = 0
  
  bodyLines.forEach((text, lineIndex) => {
    const lineDiv = document.createElement('div')
    lineDiv.className = 'story-line'
    container.appendChild(lineDiv)
    
    const chars = text.split('')
    const charCount = chars.length
    const charDuration = LINE_DURATION / charCount
    
    for (let i = 0; i < charCount; i++) {
      const span = document.createElement('span')
      span.textContent = chars[i]
      span.className = 'story-char'
      
      // 初始状态：透明、轻微下移、略微模糊
      span.style.opacity = '0'
      span.style.transform = 'translateY(0.5em)'
      span.style.filter = 'blur(2px)'
      
      lineDiv.appendChild(span)
      
      const charDelay = cumulativeDelay + i * charDuration * CHAR_DELAY_FACTOR
      
      const tid = setTimeout(() => {
        span.style.transition = 'opacity 0.2s cubic-bezier(0.2, 0.9, 0.3, 1), transform 0.3s cubic-bezier(0.2, 0.9, 0.3, 1), filter 0.25s ease'
        span.style.opacity = '1'
        span.style.transform = 'translateY(0)'
        span.style.filter = 'blur(0)'
      }, charDelay)
      
      timeouts.push(tid)
    }
    
    cumulativeDelay += LINE_DURATION
    
    if (lineIndex === bodyLines.length - 1) {
      const finishTid = setTimeout(() => {
        showStartButton.value = true
        isAllShown.value = true
      }, cumulativeDelay + 200)
      timeouts.push(finishTid)
    }
  })
}

onMounted(() => {
  animateBodyLines()
})

onUnmounted(() => {
  clearAllTimeouts()
})
</script>

<style>
/* 页面整体：完全无容器，仅保留背景透出 */
.welcome-page {
  height: 100%;
  overflow-y: auto;
  padding: 3rem 2rem 2rem 2rem;
  scroll-behavior: smooth;
  position: relative;
  font-family: 'Cinzel', 'Cormorant Garamond', 'Times New Roman', serif;
  color: #f0f0f0;
  background: transparent;
}

/* 右上角控制区域 */
.page-controls {
  position: absolute;
  top: 24px;
  right: 32px;
  z-index: 10;
}

.skip-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  background: transparent;
  border: none;
  padding: 6px 12px;
  color: rgba(220, 220, 240, 0.5);
  font-size: 0.9rem;
  font-family: inherit;
  cursor: pointer;
  transition: color 0.2s;
  border-radius: 20px;
}

.skip-btn:hover {
  color: rgba(230, 210, 170, 0.9);
  background: rgba(255, 255, 255, 0.03);
}

.skip-text {
  letter-spacing: 1px;
}

/* 标题：大气、无容器感，金色渐变 */
.welcome-title {
  font-size: 9vh;
  margin: 0 0 2.5rem 0;
  font-family: 'Cinzel', 'UnifrakturMaguntia', serif;
  background: linear-gradient(135deg, #e6d5a8 0%, #b88a44 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  text-align: center;
  letter-spacing: 6px;
  font-weight: 700;
  text-shadow: 0 0 20px rgba(180, 130, 70, 0.3);
  line-height: 1.2;
  word-break: keep-all;
}

/* 正文容器：无背景、无边框、无阴影，仅用于布局，外边距缩小 */
.welcome-body {
  max-width: 800px;        /* 稍微缩小宽度，文字更集中 */
  margin: 0 auto;
  padding: 0 0.5rem;       /* 左右内边距减半，更贴近边缘 */
  /* 完全透明，只负责排版 */
}

/* 每一行作为一个块，行间距缩小，更紧凑 */
.story-line {
  margin-bottom: 1.2rem;    /* 原为1.8rem */
  line-height: 1.6;
  text-align: left;
}

/* 每个字符的初始样式 */
.story-char {
  display: inline-block;
  font-size: 3.5vh;         /* 维持原大小，保证可读性 */
  color: #e8e6e0;
  font-weight: 300;
  letter-spacing: 0.01em;   /* 更紧凑的字间距 */
  text-shadow: 0 1px 4px rgba(0, 0, 0, 0.5);
  white-space: pre-wrap;
  will-change: opacity, transform, filter;
}

/* 针对不同屏幕微调 */
@media (min-width: 1600px) {
  .story-char {
    font-size: 2.5vh;
  }
}

/* 游戏开始按钮包装 */
.game-start-wrapper {
  max-width: 800px;
  margin: 2.5rem auto 0;    /* 略微减少上边距 */
  text-align: center;
  padding: 0 0.5rem;
}

/* 游戏开始标题：无容器，仅文字悬停效果 */
.game-start {
  display: inline-block;
  font-size: 4vh;
  font-weight: 700;
  font-family: 'Cinzel', serif;
  background: linear-gradient(135deg, #f2e5c0, #c9a87b);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  cursor: pointer;
  letter-spacing: 8px;
  padding: 0.8rem 2.5rem;
  transition: all 0.3s cubic-bezier(0.2, 0.9, 0.4, 1);
  text-shadow: 0 0 15px rgba(200, 170, 120, 0.3);
  border: 1px solid transparent;
  border-radius: 40px;
}

.game-start:hover {
  transform: scale(1.02);
  text-shadow: 0 0 25px rgba(220, 190, 140, 0.6);
  border-color: rgba(200, 170, 120, 0.3);
  background-color: rgba(30, 30, 35, 0.2);
  backdrop-filter: blur(4px);
  padding: 0.8rem 3rem;
}

/* 过渡动画 */
.fade-scale-enter-active,
.fade-scale-leave-active {
  transition: opacity 0.5s ease, transform 0.5s cubic-bezier(0.2, 0.9, 0.4, 1);
}
.fade-scale-enter-from,
.fade-scale-leave-to {
  opacity: 0;
  transform: scale(0.96);
}

/* 滚动条保持透明风格 */
.welcome-page::-webkit-scrollbar {
  width: 6px;
}
.welcome-page::-webkit-scrollbar-track {
  background: transparent;
}
.welcome-page::-webkit-scrollbar-thumb {
  background: rgba(180, 160, 120, 0.25);
  border-radius: 10px;
}
.welcome-page::-webkit-scrollbar-thumb:hover {
  background: rgba(200, 180, 140, 0.4);
}
</style>