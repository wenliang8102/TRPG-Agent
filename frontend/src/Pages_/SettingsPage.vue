<template>
  <div class="settings-page">
    <div class="settings-shell">
      <header class="settings-hero">
        <div class="hero-copy-block">
          <p class="eyebrow">Settings</p>
          <h1>设置</h1>
        </div>
        <div class="hero-actions">
          <button class="icon-btn" type="button" title="恢复界面默认值" @click="resetSettings">
            <RotateCcw :size="17" />
          </button>
        </div>
      </header>

      <section class="settings-flow">
        <article class="settings-section">
          <div class="card-header">
            <div>
              <p class="card-kicker">Chat</p>
              <h2>聊天</h2>
            </div>
          </div>

          <div class="setting-item slider-row">
            <div class="setting-copy">
              <h3>字体尺寸</h3>
              <p>聊天文本显示比例。</p>
            </div>
            <div class="slider-control">
              <input
                :value="settings.fontScale"
                class="range-slider"
                type="range"
                min="85"
                max="120"
                step="1"
                @input="updateFontScale(Number(($event.target as HTMLInputElement).value))"
              />
              <span class="slider-value">{{ settings.fontScale }}%</span>
            </div>
          </div>

          <label class="toggle-row">
            <div class="setting-copy">
              <h3>自动滚动消息</h3>
              <p>新消息到达时默认滚动到底部。</p>
            </div>
            <input
              :checked="settings.autoScrollChat"
              class="toggle-input"
              type="checkbox"
              @change="updateBooleanSetting('autoScrollChat', ($event.target as HTMLInputElement).checked)"
            />
            <span class="toggle-slider"></span>
          </label>

          <label class="toggle-row">
            <div class="setting-copy">
              <h3>跳过输出动画</h3>
              <p>直接显示完整回复内容。</p>
            </div>
            <input
              :checked="settings.skipOutputAnimation"
              class="toggle-input"
              type="checkbox"
              @change="updateBooleanSetting('skipOutputAnimation', ($event.target as HTMLInputElement).checked)"
            />
            <span class="toggle-slider"></span>
          </label>

          <label class="toggle-row">
            <div class="setting-copy">
              <h3>默认开启调试模式</h3>
              <p>进入聊天页时默认显示工具消息。</p>
            </div>
            <input
              :checked="settings.defaultDebugMode"
              class="toggle-input"
              type="checkbox"
              @change="updateBooleanSetting('defaultDebugMode', ($event.target as HTMLInputElement).checked)"
            />
            <span class="toggle-slider"></span>
          </label>
        </article>

        <article class="settings-section model-section">
          <div class="card-header model-header">
            <div>
              <p class="card-kicker">Models</p>
              <h2>模型方案</h2>
            </div>
            <div class="model-actions">
              <button class="secondary-btn" type="button" :disabled="modelUi.loading" @click="loadModelConfig">
                <RefreshCw :size="15" />
                刷新
              </button>
              <button class="primary-btn" type="button" :disabled="!canSaveModelConfig" @click="saveModelConfig">
                <Save :size="15" />
                保存并应用
              </button>
            </div>
          </div>

          <p v-if="modelUi.error" class="status-text error">{{ modelUi.error }}</p>
          <p v-else-if="modelUi.status" class="status-text">{{ modelUi.status }}</p>

          <div v-if="modelUi.loading" class="loading-line">正在读取模型配置...</div>

          <div v-else-if="modelConfig && activeProfile" class="model-config-grid">
            <aside class="profile-list">
              <button
                v-for="profile in modelConfig.profiles"
                :key="profile.id"
                class="profile-option"
                :class="{ active: profile.id === modelConfig.active_profile_id }"
                type="button"
                @click="activateProfile(profile.id)"
              >
                <span>{{ profile.name || '未命名方案' }}</span>
                <Check v-if="profile.id === modelConfig.active_profile_id" :size="16" />
              </button>
              <div class="profile-tools">
                <button class="icon-text-btn" type="button" @click="addProfile">
                  <Plus :size="15" />
                  新建
                </button>
                <button class="icon-text-btn" type="button" @click="duplicateActiveProfile">
                  <Copy :size="15" />
                  复制
                </button>
                <button class="icon-text-btn danger" type="button" :disabled="modelConfig.profiles.length <= 1" @click="deleteActiveProfile">
                  <Trash2 :size="15" />
                  删除
                </button>
              </div>
            </aside>

            <div class="model-editor">
              <div class="field-grid profile-name-row">
                <label class="text-field">
                  <span>方案名称</span>
                  <input v-model.trim="activeProfile.name" type="text" autocomplete="off" />
                </label>
                <label class="toggle-row compact-toggle">
                  <div class="setting-copy">
                    <h3>压缩记忆</h3>
                    <p>后台情节摘要调用。</p>
                  </div>
                  <input v-model="activeProfile.memory_summary_enabled" class="toggle-input" type="checkbox" />
                  <span class="toggle-slider"></span>
                </label>
              </div>

              <section
                v-for="section in endpointSections"
                :key="section.key"
                class="endpoint-section"
              >
                <div class="endpoint-heading">
                  <div>
                    <p class="card-kicker">{{ section.kicker }}</p>
                    <h3>{{ section.title }}</h3>
                  </div>
                  <button
                    v-if="section.key !== 'llm'"
                    class="mini-btn"
                    type="button"
                    @click="copyMainEndpoint(section.key)"
                  >
                    同步主模型凭据
                  </button>
                </div>

                <div class="field-grid">
                  <label class="text-field">
                    <span>模型名</span>
                    <input v-model.trim="activeProfile[section.key].model" type="text" autocomplete="off" />
                  </label>
                  <label class="text-field sensitive-field">
                    <span>API Key</span>
                    <input v-model.trim="activeProfile[section.key].api_key" type="password" autocomplete="new-password" />
                  </label>
                  <label class="text-field wide-field">
                    <span>Base URL</span>
                    <input v-model.trim="activeProfile[section.key].base_url" type="url" autocomplete="off" placeholder="https://api.openai.com/v1" />
                  </label>
                  <label v-if="section.generation" class="text-field numeric-field">
                    <span>温度</span>
                    <input v-model.number="activeProfile[section.key].temperature" type="number" min="0" max="2" step="0.1" />
                  </label>
                  <label class="text-field numeric-field">
                    <span>超时秒数</span>
                    <input v-model.number="activeProfile[section.key].timeout_seconds" type="number" min="1" step="1" />
                  </label>
                  <label class="text-field numeric-field">
                    <span>重试次数</span>
                    <input v-model.number="activeProfile[section.key].max_retries" type="number" min="0" step="1" />
                  </label>
                  <label v-if="section.thinking" class="text-field">
                    <span>Thinking</span>
                    <select v-model="activeProfile[section.key].thinking_mode">
                      <option value="disabled">关闭</option>
                      <option value="enabled">供应商默认</option>
                    </select>
                  </label>
                </div>
              </section>
            </div>
          </div>
        </article>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Check, Copy, Plus, RefreshCw, RotateCcw, Save, Trash2 } from 'lucide-vue-next'
import {
  createModelProfile,
  loadAppSettings,
  modelConfigService,
  resetAppSettings,
  saveAppSettings,
  type AppSettings,
  type ModelConfigState,
  type ModelProfile,
} from '../Services_/SettingsPageService'

type BooleanSettingKey = 'skipOutputAnimation' | 'autoScrollChat' | 'defaultDebugMode'
type EndpointKey = 'llm' | 'summary' | 'embedding' | 'rerank'

interface EndpointSection {
  key: EndpointKey
  kicker: string
  title: string
  generation: boolean
  thinking: boolean
}

// 设置页直接使用本地持久化结果，避免依赖后端账户同步。
const settings = reactive<AppSettings>(loadAppSettings())

const modelConfig = ref<ModelConfigState | null>(null)
const modelUi = reactive({
  loading: false,
  saving: false,
  error: '',
  status: '',
})

const endpointSections: EndpointSection[] = [
  { key: 'llm', kicker: 'Primary', title: '主模型', generation: true, thinking: true },
  { key: 'summary', kicker: 'Compression', title: '压缩模型', generation: true, thinking: true },
  { key: 'embedding', kicker: 'Retrieval', title: 'Embedding', generation: false, thinking: false },
  { key: 'rerank', kicker: 'Retrieval', title: 'Rerank', generation: false, thinking: false },
]

const activeProfile = computed<ModelProfile | null>(() => {
  const config = modelConfig.value
  if (!config) {
    return null
  }
  return config.profiles.find((profile) => profile.id === config.active_profile_id) ?? config.profiles[0] ?? null
})

const canSaveModelConfig = computed(() => Boolean(modelConfig.value && activeProfile.value && !modelUi.saving))

// 每次用户调整都立即保存，避免额外的“提交”步骤。
const persistSettings = () => {
  saveAppSettings({ ...settings })
}

const updateFontScale = (value: number) => {
  settings.fontScale = value
  persistSettings()
}

const updateBooleanSetting = (key: BooleanSettingKey, value: boolean) => {
  settings[key] = value
  persistSettings()
}

const resetSettings = () => {
  const resetValue = resetAppSettings()
  Object.assign(settings, resetValue)
}

const syncModelConfig = (nextState: ModelConfigState) => {
  modelConfig.value = nextState
}

const runModelConfigAction = async (action: () => Promise<ModelConfigState>, successText: string) => {
  modelUi.error = ''
  modelUi.status = ''
  try {
    const nextState = await action()
    syncModelConfig(nextState)
    modelUi.status = successText
  } catch (error) {
    modelUi.error = error instanceof Error ? error.message : String(error)
  }
}

const loadModelConfig = async () => {
  modelUi.loading = true
  await runModelConfigAction(() => modelConfigService.load(), '已加载当前运行时方案。')
  modelUi.loading = false
}

const saveModelConfig = async () => {
  if (!modelConfig.value) {
    return
  }
  modelUi.saving = true
  await runModelConfigAction(() => modelConfigService.save(modelConfig.value as ModelConfigState), '已保存并应用到后端。')
  modelUi.saving = false
}

const activateProfile = async (profileId: string) => {
  if (!modelConfig.value || profileId === modelConfig.value.active_profile_id) {
    return
  }
  await runModelConfigAction(() => modelConfigService.activate(profileId), '已切换方案。')
}

const addProfile = () => {
  if (!modelConfig.value) {
    return
  }
  const profile = createModelProfile(activeProfile.value ?? undefined)
  profile.name = '新方案'
  modelConfig.value.profiles.push(profile)
  modelConfig.value.active_profile_id = profile.id
  modelUi.status = '新方案尚未保存。'
}

const duplicateActiveProfile = () => {
  if (!modelConfig.value || !activeProfile.value) {
    return
  }
  const profile = createModelProfile(activeProfile.value)
  modelConfig.value.profiles.push(profile)
  modelConfig.value.active_profile_id = profile.id
  modelUi.status = '方案副本尚未保存。'
}

const deleteActiveProfile = () => {
  if (!modelConfig.value || !activeProfile.value || modelConfig.value.profiles.length <= 1) {
    return
  }
  const removingId = activeProfile.value.id
  modelConfig.value.profiles = modelConfig.value.profiles.filter((profile) => profile.id !== removingId)
  modelConfig.value.active_profile_id = modelConfig.value.profiles[0].id
  modelUi.status = '删除结果尚未保存。'
}

const copyMainEndpoint = (key: Exclude<EndpointKey, 'llm'>) => {
  if (!activeProfile.value) {
    return
  }
  activeProfile.value[key].api_key = activeProfile.value.llm.api_key
  activeProfile.value[key].base_url = activeProfile.value.llm.base_url
}

onMounted(loadModelConfig)
</script>

<style scoped>
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@500;600;700;800&family=UnifrakturMaguntia&family=Noto+Serif+SC:wght@400;500;600&display=swap');

.settings-page {
  min-height: 100%;
  background:
    linear-gradient(180deg, rgba(184, 138, 68, 0.04), transparent 16%),
    linear-gradient(180deg, #0b0b0d 0%, #101115 52%, #0d0d10 100%);
  color: #efe3c2;
}

.settings-shell {
  max-width: 1180px;
  margin: 0 auto;
  padding: 44px 42px 60px;
  position: relative;
}

.settings-shell::before {
  content: '';
  position: absolute;
  inset: 18px 0 0;
  pointer-events: none;
  opacity: 0.35;
  background-image:
    linear-gradient(90deg, transparent 0, transparent calc(50% - 1px), rgba(184, 138, 68, 0.08) calc(50% - 1px), rgba(184, 138, 68, 0.08) calc(50% + 1px), transparent calc(50% + 1px)),
    linear-gradient(180deg, transparent 0, rgba(184, 138, 68, 0.05) 48%, transparent 100%);
  background-size: 100% 100%, 100% 220px;
  background-repeat: no-repeat, repeat-y;
}

.settings-hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 28px;
  margin-bottom: 14px;
  padding: 10px 0 22px;
}

.eyebrow {
  margin: 0 0 8px;
  color: rgba(230, 213, 168, 0.5);
  text-transform: uppercase;
  letter-spacing: 2.4px;
  font-size: 0.68rem;
  line-height: 1;
}

.settings-hero h1 {
  margin: 0;
  font-family: 'Cinzel', 'UnifrakturMaguntia', 'Noto Serif SC', serif;
  font-size: 2.56rem;
  font-weight: 700;
  line-height: 1.06;
  letter-spacing: 2.6px;
  text-transform: uppercase;
  text-align: left;
  background: linear-gradient(135deg, #e6d5a8 0%, #b88a44 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  text-shadow: 2px 2px 5px rgba(0, 0, 0, 0.45);
  transform: skew(-1deg) rotate(-0.5deg);
}

.hero-copy-block {
  position: relative;
}

.hero-actions,
.model-actions,
.profile-tools {
  display: flex;
  align-items: center;
  gap: 10px;
}

button {
  font: inherit;
}

.primary-btn,
.secondary-btn,
.icon-text-btn,
.mini-btn,
.icon-btn {
  border: 1px solid rgba(210, 180, 140, 0.22);
  background: rgba(31, 31, 36, 0.92);
  color: #f2ddb0;
  cursor: pointer;
  transition: all 0.2s ease;
}

.primary-btn,
.secondary-btn,
.icon-text-btn {
  min-height: 38px;
  padding: 0 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.primary-btn {
  background: rgba(152, 106, 42, 0.52);
  border-color: rgba(230, 213, 168, 0.38);
}

.icon-btn {
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
}

.mini-btn {
  min-height: 30px;
  padding: 0 10px;
  font-size: 0.78rem;
}

button:hover:not(:disabled) {
  border-color: rgba(230, 213, 168, 0.44);
  transform: translateY(-1px);
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.48;
}

.settings-flow {
  position: relative;
  padding-top: 10px;
}

.settings-flow::before {
  content: '';
  position: absolute;
  inset: 0;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  pointer-events: none;
}

.settings-section {
  padding: 24px 0 28px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  position: relative;
}

.settings-section::after {
  content: '';
  position: absolute;
  left: 0;
  bottom: -1px;
  width: 220px;
  height: 1px;
  background: linear-gradient(90deg, rgba(184, 138, 68, 0.32), rgba(184, 138, 68, 0.08), transparent 78%);
}

.card-header,
.endpoint-heading {
  margin-bottom: 16px;
  position: relative;
  padding-left: 16px;
}

.card-header::before,
.endpoint-heading::before {
  content: '';
  position: absolute;
  left: 0;
  top: 7px;
  width: 7px;
  height: 7px;
  transform: rotate(45deg);
  border: 1px solid rgba(184, 138, 68, 0.5);
  background: rgba(184, 138, 68, 0.1);
}

.card-kicker {
  margin: 0 0 6px;
  color: rgba(230, 213, 168, 0.46);
  text-transform: uppercase;
  letter-spacing: 1.5px;
  font-size: 0.68rem;
  line-height: 1.1;
}

.card-header h2,
.endpoint-heading h3 {
  margin: 0;
  font-family: 'Cinzel', 'Noto Serif SC', serif;
  color: #f0ddb2;
  letter-spacing: 0.6px;
  line-height: 1.2;
}

.card-header h2 {
  font-size: 1.24rem;
}

.endpoint-heading h3 {
  font-size: 1.02rem;
}

.setting-item,
.toggle-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 24px;
  align-items: center;
  padding: 16px 0;
  border-top: 1px solid rgba(255, 255, 255, 0.05);
}

.setting-item:first-of-type,
.toggle-row:first-of-type {
  border-top: none;
  padding-top: 0;
}

.setting-copy h3 {
  margin: 0 0 6px;
  font-size: 1rem;
  font-weight: 600;
  line-height: 1.35;
  color: #f7e7bc;
}

.setting-copy p {
  margin: 0;
  color: rgba(239, 227, 194, 0.62);
  line-height: 1.58;
  font-size: 0.9rem;
  max-width: 42rem;
}

.slider-row {
  align-items: center;
}

.slider-control {
  display: flex;
  align-items: center;
  gap: 14px;
  min-width: 280px;
}

.range-slider {
  width: 220px;
  appearance: none;
  height: 4px;
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(184, 138, 68, 0.8), rgba(255, 255, 255, 0.16));
  outline: none;
}

.range-slider::-webkit-slider-thumb {
  appearance: none;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #f1dfb6;
  border: 1px solid rgba(115, 82, 36, 0.7);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.28);
  cursor: pointer;
}

.range-slider::-moz-range-thumb {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #f1dfb6;
  border: 1px solid rgba(115, 82, 36, 0.7);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.28);
  cursor: pointer;
}

.slider-value {
  min-width: 48px;
  color: #f3e3ba;
  font-size: 0.92rem;
  text-align: right;
}

.toggle-row {
  position: relative;
  cursor: pointer;
}

.toggle-input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.toggle-slider {
  position: relative;
  width: 56px;
  height: 30px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(210, 180, 140, 0.1);
  transition: 0.2s ease;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03);
}

.toggle-slider::after {
  content: '';
  position: absolute;
  top: 3px;
  left: 3px;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #f4e4be;
  transition: 0.2s ease;
}

.toggle-input:checked + .toggle-slider {
  background: rgba(184, 138, 68, 0.35);
  border-color: rgba(230, 213, 168, 0.4);
}

.toggle-input:checked + .toggle-slider::after {
  transform: translateX(26px);
  background: #fff3d0;
}

.model-header,
.endpoint-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.status-text,
.loading-line {
  margin: 0 0 18px;
  color: rgba(239, 227, 194, 0.68);
  font-size: 0.9rem;
}

.status-text.error {
  color: #ffb6a3;
}

.model-config-grid {
  display: grid;
  grid-template-columns: 230px minmax(0, 1fr);
  gap: 24px;
}

.profile-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.profile-option {
  min-height: 44px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.03);
  color: rgba(239, 227, 194, 0.76);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 12px;
  text-align: left;
}

.profile-option.active {
  border-color: rgba(230, 213, 168, 0.44);
  background: rgba(184, 138, 68, 0.16);
  color: #ffe8b8;
}

.profile-tools {
  flex-wrap: wrap;
  padding-top: 6px;
}

.icon-text-btn {
  min-height: 34px;
  padding: 0 10px;
  font-size: 0.82rem;
}

.icon-text-btn.danger {
  color: #ffc1b7;
}

.model-editor,
.endpoint-section {
  min-width: 0;
}

.endpoint-section {
  padding-top: 18px;
  margin-top: 18px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}

.field-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.profile-name-row {
  align-items: center;
}

.text-field {
  display: grid;
  gap: 8px;
  min-width: 0;
}

.text-field span {
  color: rgba(239, 227, 194, 0.66);
  font-size: 0.82rem;
}

.text-field input,
.text-field select {
  width: 100%;
  min-width: 0;
  height: 38px;
  border: 1px solid rgba(210, 180, 140, 0.16);
  background: rgba(8, 8, 10, 0.48);
  color: #f6e5bd;
  padding: 0 11px;
  outline: none;
}

.text-field input:focus,
.text-field select:focus {
  border-color: rgba(230, 213, 168, 0.42);
}

.wide-field {
  grid-column: 1 / -1;
}

.numeric-field {
  max-width: 190px;
}

.compact-toggle {
  padding: 0;
  border-top: 0;
}

@media (max-width: 860px) {
  .model-config-grid {
    grid-template-columns: 1fr;
  }

  .profile-list {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .profile-tools {
    grid-column: 1 / -1;
  }
}

@media (max-width: 680px) {
  .settings-shell {
    padding: 32px 18px 44px;
  }

  .settings-shell::before {
    display: none;
  }

  .settings-hero,
  .model-header,
  .endpoint-heading {
    flex-direction: column;
    align-items: flex-start;
  }

  .settings-hero h1 {
    font-size: 2.06rem;
    letter-spacing: 1.8px;
  }

  .setting-item,
  .toggle-row,
  .field-grid,
  .profile-list {
    grid-template-columns: 1fr;
  }

  .slider-control,
  .model-actions,
  .profile-tools {
    min-width: 100%;
    width: 100%;
  }

  .primary-btn,
  .secondary-btn,
  .icon-text-btn {
    flex: 1;
  }

  .range-slider {
    width: 100%;
  }
}
</style>
