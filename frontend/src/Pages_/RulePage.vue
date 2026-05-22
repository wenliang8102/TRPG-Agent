<!-- frontend/src/Pages_/WelcomePage.vue -->
<template>
  <div class="welcome-page">
    <!-- 背景轮播层 - 改为 absolute 相对于父容器 -->
    <div class="background-carousel">
      <div 
        v-for="(image, index) in backgroundImages" 
        :key="index"
        class="carousel-slide"
        :class="{ active: currentIndex === index }"
        :style="{ backgroundImage: `url(${image})` }"
      />
      <div class="carousel-overlay" />
    </div>

    <!-- 切换按钮 -->
    <button class="carousel-prev" @click="prevSlide">‹</button>
    <button class="carousel-next" @click="nextSlide">›</button>
    <div class="carousel-dots">
      <span 
        v-for="(_, index) in backgroundImages" 
        :key="index"
        class="dot"
        :class="{ active: currentIndex === index }"
        @click="currentIndex = index"
      />
    </div>

    <!-- 前景悬浮内容 -->
    <div class="foreground">
      <div class="glass-card">
        <!-- 内容保持不变 -->
        <div class="card-header">
          <h1 class="logo">TRPG<span class="accent">-AGENT</span></h1>
          <div class="auth-links">
            <a href="#" class="auth-link">登录</a>
            <span class="divider">|</span>
            <a href="#" class="auth-link">注册</a>
          </div>
        </div>

        <div class="card-section">
          <h2 class="section-title">关于我们 ABOUT US</h2>
          <p class="section-text">
            TRPG-AGENT 是一款ai对话游戏<br>
            我们希望通过 AI 技术，让每一次冒险都更加精彩，<br>
            致力于提供最流畅的游戏体验。
          </p>
        </div>

        <div class="card-section">
          <h2 class="section-title">作品 PROJECTS</h2>
          <div class="projects-grid">
            <div class="project-card">
              <div class="project-icon">1</div>
              <span>8</span>
            </div>
            <div class="project-card">
              <div class="project-icon">2</div>
              <span>/</span>
            </div>
            <div class="project-card">
              <div class="project-icon">3</div>
              <span>/</span>
            </div>
            <div class="project-card">
              <div class="project-icon">4</div>
              <span>/</span>
            </div>
          </div>
        </div>

        <div class="card-section two-columns">
          <div class="col">
            <h2 class="section-title">加入我们 CAREER</h2>
            <p class="section-text">
              我们在寻找更多热爱跑团和创作的成员，<br>
              请关注后续动态，或点击查看开放职位。
            </p>
            <a href="#" class="link-btn">查看热招职位 →</a>
          </div>
          <div class="col">
            <h2 class="section-title">联系我们 CONTACT US</h2>
            <p class="section-text">
              请联系：www.wbzd.com<br>
              或在 GitHub 上提交 Issue！
            </p>
            <a href="#" class="link-btn">GitHub →</a>
          </div>
        </div>

        <div class="card-footer">
          <div class="footer-logos">
            <span class="footer-logo">TRPG</span>
            <span class="footer-logo">AGENT</span>
          </div>
          <p class="copyright">
            Copyright © 2024 TRPG-AGENT. All rights reserved.<br>
            沪ICP备xxxxxx号-1 沪公网安备xxxxxxxx号
          </p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'

// 背景图片列表（图片路径你填）
const backgroundImages = ref([
  'src/assets/textimage1.jpg',
  'src/assets/textimage2.jpg',
  'src/assets/textimage3.jpg',
])

const currentIndex = ref(0)
let autoTimer: ReturnType<typeof setInterval> | null = null

const nextSlide = () => {
  currentIndex.value = (currentIndex.value + 1) % backgroundImages.value.length
}

const prevSlide = () => {
  currentIndex.value = (currentIndex.value - 1 + backgroundImages.value.length) % backgroundImages.value.length
}

const startAutoPlay = () => {
  autoTimer = setInterval(() => {
    nextSlide()
  }, 5000)
}

const stopAutoPlay = () => {
  if (autoTimer) {
    clearInterval(autoTimer)
    autoTimer = null
  }
}

onMounted(() => {
  startAutoPlay()
})

onUnmounted(() => {
  stopAutoPlay()
})
</script>

<style scoped>
.welcome-page {
  position: relative;
  width: 100%;
  min-height: 100%;
  background: #0d0d0d;
}

/* ========== 背景轮播层 ========== */
.background-carousel {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  z-index: 0;
  overflow: hidden;
  border-radius: 0;
}

.carousel-slide {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background-size: cover;
  background-position: center;
  opacity: 0;
  transition: opacity 0.8s ease-in-out;
}

.carousel-slide.active {
  opacity: 1;
}

.carousel-overlay {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: rgba(0, 0, 0, 0.6);
}

/* 轮播控制按钮 */
.carousel-prev,
.carousel-next {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  z-index: 20;
  background: rgba(255, 255, 255, 0.2);
  backdrop-filter: blur(10px);
  border: 0.5px solid rgba(255, 255, 255, 0.3);
  color: white;
  font-size: 32px;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}

.carousel-prev {
  left: 20px;
}

.carousel-next {
  right: 20px;
}

.carousel-prev:hover,
.carousel-next:hover {
  background: rgba(66, 184, 131, 0.8);
  transform: translateY(-50%) scale(1.05);
}

.carousel-dots {
  position: absolute;
  bottom: 20px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 20;
  display: flex;
  gap: 12px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.5);
  cursor: pointer;
  transition: all 0.2s;
}

.dot.active {
  width: 24px;
  border-radius: 4px;
  background: #42b883;
}

/* ========== 前景悬浮内容 ========== */
.foreground {
  position: relative;
  z-index: 10;
  padding: 40px 80px;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
}

.glass-card {
  background: rgba(20, 20, 25, 0.8);
  backdrop-filter: blur(20px);
  border-radius: 32px;
  border: 0.5px solid rgba(255, 255, 255, 0.1);
  padding: 48px 56px;
  max-width: 1000px;
  width: 100%;
  box-shadow: 0 20px 40px rgba(0, 0, 0, 0.3);
}

/* 头部 */
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 32px;
  border-bottom: 0.5px solid rgba(255, 255, 255, 0.1);
  margin-bottom: 32px;
}

.logo {
  font-size: 28px;
  font-weight: 600;
  color: white;
  margin: 0;
}

.logo .accent {
  color: #42b883;
}

.auth-links {
  display: flex;
  gap: 12px;
  color: #8e8e93;
}

.auth-link {
  color: #8e8e93;
  text-decoration: none;
  transition: color 0.2s;
}

.auth-link:hover {
  color: #42b883;
}

.divider {
  color: #3a3a40;
}

/* 区块 */
.card-section {
  margin-bottom: 48px;
}

.section-title {
  font-size: 14px;
  letter-spacing: 2px;
  color: #42b883;
  margin-bottom: 16px;
  font-weight: 500;
}

.section-text {
  color: #d1d1d6;
  line-height: 1.6;
  font-size: 15px;
}

/* 作品网格 */
.projects-grid {
  display: flex;
  gap: 24px;
  margin-top: 20px;
}

.project-card {
  background: rgba(255, 255, 255, 0.05);
  border-radius: 16px;
  padding: 20px 24px;
  text-align: center;
  transition: all 0.2s;
  cursor: pointer;
  flex: 1;
}

.project-card:hover {
  background: rgba(66, 184, 131, 0.15);
  transform: translateY(-4px);
}

.project-icon {
  font-size: 32px;
  margin-bottom: 12px;
}

.project-card span {
  color: #e5e5ea;
  font-size: 14px;
}

/* 两列布局 */
.two-columns {
  display: flex;
  gap: 48px;
}

.two-columns .col {
  flex: 1;
}

.link-btn {
  display: inline-block;
  margin-top: 16px;
  color: #42b883;
  text-decoration: none;
  font-size: 14px;
  transition: all 0.2s;
}

.link-btn:hover {
  transform: translateX(4px);
}

/* 底部 */
.card-footer {
  margin-top: 48px;
  padding-top: 32px;
  border-top: 0.5px solid rgba(255, 255, 255, 0.1);
  text-align: center;
}

.footer-logos {
  display: flex;
  justify-content: center;
  gap: 24px;
  margin-bottom: 20px;
}

.footer-logo {
  font-size: 12px;
  color: #8e8e93;
  letter-spacing: 2px;
}

.copyright {
  font-size: 11px;
  color: #6c6c70;
  line-height: 1.5;
}

/* 响应式 */
@media (max-width: 768px) {
  .foreground {
    padding: 20px;
  }
  
  .glass-card {
    padding: 28px 20px;
  }
  
  .projects-grid {
    flex-wrap: wrap;
  }
  
  .two-columns {
    flex-direction: column;
    gap: 32px;
  }
  
  .card-header {
    flex-direction: column;
    gap: 16px;
    text-align: center;
  }
}
</style>