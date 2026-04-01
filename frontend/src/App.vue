<script setup lang="ts">
import { ref } from 'vue'

// 定义响应式变量，用于在页面上显示数据
const backendMessage = ref('')
const inputText = ref('')
const chatReply = ref('')

// 测试 GET 请求 (请求根目录)
const testGet = async () => {
  try {
    const response = await fetch('http://127.0.0.1:8000/')
    const data = await response.json()
    backendMessage.value = data.message
  } catch (error) {
    backendMessage.value = '连接后端失败，请检查后端是否启动！'
    console.error(error)
  }
}

// 测试 POST 请求 (模拟发送聊天给 LangGraph)
const testPost = async () => {
  if (!inputText.value) return

  try {
    // 注意：这里我们用 query 参数传递文字，对应后端 main.py 里的 query: str
    const response = await fetch(`http://127.0.0.1:8000/chat?query=${inputText.value}`, {
      method: 'POST'
    })
    const data = await response.json()
    chatReply.value = data.reply
  } catch (error) {
    chatReply.value = '发送失败！'
    console.error(error)
  }
}
</script>

<template>
  <div class="test-container">
    <h1>🚀 跨语言 Monorepo 测试</h1>

    <!-- GET 测试区域 -->
    <div class="card">
      <h3>1. 测试基础连接 (GET)</h3>
      <button @click="testGet">获取后端问候语</button>
      <p class="result">后端响应: <span style="color: #42b883">{{ backendMessage }}</span></p>
    </div>

    <!-- POST 测试区域 -->
    <div class="card">
      <h3>2. 测试带参数对话 (POST)</h3>
      <input v-model="inputText" placeholder="输入要发送给后端的话..." />
      <button @click="testPost">发送给后端</button>
      <p class="result">后端回复: <span style="color: #42b883">{{ chatReply }}</span></p>
    </div>
  </div>
</template>

<style scoped>
.test-container {
  font-family: Arial, sans-serif;
  max-width: 600px;
  margin: 50px auto;
  text-align: center;
}
.card {
  border: 1px solid #ccc;
  border-radius: 8px;
  padding: 20px;
  margin-bottom: 20px;
  background-color: #f9f9f9;
}
input {
  padding: 8px;
  margin-right: 10px;
  width: 200px;
}
button {
  padding: 8px 16px;
  background-color: #42b883; /* Vue 的主题绿 */
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}
button:hover {
  background-color: #33a06f;
}
.result {
  margin-top: 15px;
  font-weight: bold;
}
</style>