<template>
  <AppLayout />
</template>

<script setup lang="ts">
import AppLayout from './components/Layout/AppLayout.vue'
</script>

<style>
/*中世纪风格字体 */
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@400;500;600;700;800&family=UnifrakturMaguntia&display=swap');

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

html, body, #app {
  width: 100%;
  height: 100%;
}

/* 全局默认字体：中世纪风格 */
body {
  font-family: 'Cinzel', 'UnifrakturMaguntia', 'MedievalSharp', 'Germania One', serif;
  background: #0d0d0d;
  color: #e0e0e0;
}

/* 聊天区域及其内部所有元素恢复为系统无衬线字体（清晰易读） */
/* 前提：需要在 Chatpages.vue 的最外层容器上添加 class="chat-main" */
.chat-main,
.chat-main * {
  font-family: system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif !important;
}
</style>