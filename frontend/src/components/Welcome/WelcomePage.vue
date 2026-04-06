<template>
  <div class="welcome-container">
    <div class="welcome-card">
      <h1>欢迎来到 TRPG 平台</h1>
      <p>这是一个跑团助手工具，支持 AI 对话和骰子判定。</p>
      <p>点击左侧导航体验功能。</p>
    </div>

    <!-- 添加一些测试内容，方便看滚动效果 -->
    <div class="test-scroll">
      <div class="test-item">✨ 功能 1：AI 智能对话</div>
      <div class="test-item">🎲 功能 2：骰子判定系统</div>
      <div class="test-item">📖 功能 3：跑团记录管理</div>
      <div class="test-item">👥 功能 4：多人协作</div>
      <div class="test-item">🎨 功能 5：角色卡编辑</div>
      <div class="test-item">⚔️ 功能 6：战斗模拟器</div>
      <div class="test-item">🏆 功能 7：成就系统</div>
      <div class="test-item">📊 功能 8：数据统计</div>
      <div class="test-item">🔧 功能 9：自定义规则</div>
      <div class="test-item">💾 功能 10：云端存档</div>
    </div>
  </div>
</template>

<style scoped>
.welcome-container {
  width: 100%;
  padding: 32px;
  box-sizing: border-box;
}

.welcome-card {
  text-align: center;
  border: 1px dashed rgba(255, 255, 255, 0.2);
  border-radius: 24px;
  padding: 48px 32px;
  margin-bottom: 32px;
  background: rgba(255, 255, 255, 0.02);
}

.welcome-card h1 {
  margin-bottom: 16px;
  font-size: 28px;
  font-weight: 600;
}

.welcome-card p {
  margin: 8px 0;
  color: #8e8e93;
}

.test-scroll {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}

.test-item {
  padding: 20px;
  background: rgba(255, 255, 255, 0.03);
  border: 0.5px solid rgba(255, 255, 255, 0.1);
  border-radius: 16px;
  font-size: 16px;
  transition: all 0.2s;
}

.test-item:hover {
  background: rgba(255, 255, 255, 0.06);
  transform: translateY(-2px);
}
</style>