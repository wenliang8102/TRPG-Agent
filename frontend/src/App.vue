<template>
  <AppLayout />
</template>

<script setup lang="ts">
import AppLayout from './components/Layout/AppLayout.vue'
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

html, body, #app {
  width: 100%;
  height: 100%;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #0d0d0d;
  color: #e0e0e0;
}
</style>