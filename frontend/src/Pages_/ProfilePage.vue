<!-- frontend/src/Pages_/ProfilePage.vue -->
<template>
  <div class="profile-container">
    <!-- 头部区域（左侧直角正方形头像 + 右侧文本） -->
    <div class="profile-header">
      <div class="avatar-frame">
        <div class="avatar-inner">
          <User class="avatar-icon" :size="64" stroke-width="1.2" />
        </div>
      </div>
      <div class="profile-info">
        <h1 class="display-name">{{ userInfo.displayName }}</h1>
        <p class="user-title">{{ userInfo.title }}</p>
      </div>
    </div>

    <!-- 渐隐分隔线 -->
    <div class="divider-line"></div>

    <!-- 冒险者档案 -->
    <div class="info-card">
      <div class="card-header">
        <FileText class="card-icon" :size="18" />
        <h3>冒险者档案</h3>
        <button class="edit-icon-btn" @click="handleEditProfile" title="编辑档案">
          <Wrench :size="14" />
        </button>
      </div>
      <div class="info-list">
        <div class="info-item">
          <span class="info-label"><Mail :size="14" />冒险者ID</span>
          <span class="info-value">{{ userInfo.username }}</span>
        </div>
        <div class="info-item">
          <span class="info-label"><Calendar :size="14" />加入日期</span>
          <span class="info-value">{{ userInfo.joinDate }}</span>
        </div>
        <div class="info-item">
          <span class="info-label"><MapPin :size="14" />账号 ID</span>
          <span class="info-value">{{ userInfo.accountId }}</span>
        </div>
        <div class="info-item full-width">
          <span class="info-label"><MessageSquare :size="14" />冒险宣言</span>
          <span class="info-value bio">{{ userInfo.bio || '暂无宣言' }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, type Component } from 'vue'
import {
  User,
  Mail,
  Calendar,
  MapPin,
  MessageSquare,
  FileText,
  Wrench,
} from 'lucide-vue-next'

// 用户基本信息
const userInfo = reactive({
  username: 'Dragon_Knight_47',
  displayName: '格里芬·龙裔',
  title: '🌟 传奇冒险者 · 龙骑士团长',
  joinDate: '2024年·霜降之月',
  accountId: 'UID: 102347',        // 账号 ID
  bio: '',                          // 冒险宣言已清空
})

// 方法
const handleEditProfile = () => {
  alert('档案编辑功能开发中...')
}
</script>

<style scoped>
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@400;600;700;800&display=swap');

.profile-container {
  background: #000000;
  min-height: 100%;
  padding: 40px 48px;
  overflow-y: auto;
  scrollbar-width: thin;
}

.profile-container::-webkit-scrollbar {
  width: 6px;
}
.profile-container::-webkit-scrollbar-track {
  background: transparent;
}
.profile-container::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.15);
  border-radius: 10px;
}

/* ========== 头部区域：左侧直角正方形头像 + 右侧文本 ========== */
.profile-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 32px;
  gap: 0 !important;
}

.profile-header .avatar-frame {
  flex-shrink: 0;
  margin: 0 !important;
}

.profile-header .profile-info {
  flex: 1;
  margin: 0 !important;
  padding: 0 0 0 10px !important;
}

.avatar-inner {
  width: 120px;
  height: 120px;
  background: #1a1a1a;
  border-radius: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 2px solid rgba(184, 138, 68, 0.6);
  box-shadow: 0 0 20px rgba(184, 138, 68, 0.2);
  transition: all 0.3s ease;
}

.avatar-icon {
  color: #b88a44;
}

.display-name {
  font-family: 'Cinzel', serif;
  font-size: 2rem;
  font-weight: 700;
  margin: 0 0 8px 0;
  line-height: 1.2;
  background: linear-gradient(135deg, #e6d5a8 0%, #b88a44 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  letter-spacing: 1px;
}

.user-title {
  color: rgba(230, 230, 195, 0.7);
  font-size: 0.9rem;
  letter-spacing: 1px;
  margin: 0;
  line-height: 1.4;
}

/* ========== 渐隐分隔线 ========== */
.divider-line {
  height: 1px;
  width: 100%;
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(255, 255, 255, 0.05) 15%,
    rgba(255, 215, 180, 0.15) 50%,
    rgba(255, 255, 255, 0.05) 85%,
    transparent 100%
  );
  margin: 32px 0;
}

/* 卡片通用样式 */
.info-card {
  background: transparent;
  border: none;
  box-shadow: none;
  padding: 0;
  margin-bottom: 0;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
  position: relative;
}

.card-header h3 {
  font-family: 'Cinzel', serif;
  font-size: 1.2rem;
  font-weight: 600;
  color: #e6d5a8;
  margin: 0;
  letter-spacing: 1px;
}

.card-icon {
  color: #b88a44;
}

/* 编辑图标按钮（小扳手） */
.edit-icon-btn {
  margin-left: auto;
  background: transparent;
  border: 1px solid rgba(184, 138, 68, 0.3);
  border-radius: 50%;
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(184, 138, 68, 0.75);
  cursor: pointer;
  transition: all 0.2s ease;
  padding: 0;
  flex-shrink: 0;
}

.edit-icon-btn:hover {
  background: rgba(184, 138, 68, 0.15);
  border-color: #b88a44;
  color: #e6d5a8;
  box-shadow: 0 0 10px rgba(184, 138, 68, 0.2);
}

/* 信息列表 */
.info-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.info-item {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  font-size: 0.95rem;
}

.info-label {
  min-width: 100px;
  display: flex;
  align-items: center;
  gap: 8px;
  color: rgba(230, 230, 195, 0.6);
}

.info-value {
  color: #e6e6c3;
  flex: 1;
}

.info-value.bio {
  font-style: italic;
  line-height: 1.5;
  color: rgba(230, 230, 195, 0.8);
}

.full-width {
  flex-direction: column;
}

.full-width .info-label {
  margin-bottom: 8px;
}

/* 响应式 */
@media (max-width: 600px) {
  .profile-container {
    padding: 24px 20px;
  }
  .profile-header {
    flex-direction: column;
    align-items: flex-start;
  }
  .profile-header .avatar-frame {
    margin: 0 0 16px 0 !important;
  }
  .profile-header .profile-info {
    padding: 0 !important;
  }
  .display-name {
    font-size: 1.5rem;
  }
}
</style>