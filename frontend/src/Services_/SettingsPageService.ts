// frontend/src/Services_/SettingsPageService.ts

/**
 * 设置页只管理前端本地偏好，不依赖后端账户模型。
 */
export interface AppSettings {
  fontScale: number
  skipOutputAnimation: boolean
  autoScrollChat: boolean
  defaultDebugMode: boolean
}

const SETTINGS_STORAGE_KEY = 'trpg-app-settings'
export const APP_SETTINGS_UPDATED_EVENT = 'trpg:app-settings-updated'

/**
 * 默认配置保持保守，避免影响现有界面行为。
 */
export const DEFAULT_APP_SETTINGS: AppSettings = {
  fontScale: 100,
  skipOutputAnimation: false,
  autoScrollChat: true,
  defaultDebugMode: false,
}

/**
 * 从本地存储加载设置；解析失败时直接回退默认值。
 */
export function loadAppSettings(): AppSettings {
  const rawValue = localStorage.getItem(SETTINGS_STORAGE_KEY)
  if (!rawValue) {
    return { ...DEFAULT_APP_SETTINGS }
  }

  try {
    const parsed = JSON.parse(rawValue) as Partial<AppSettings>
    const parsedFontScale = typeof parsed.fontScale === 'number' ? parsed.fontScale : DEFAULT_APP_SETTINGS.fontScale
    return {
      fontScale: Math.min(120, Math.max(85, parsedFontScale)),
      skipOutputAnimation: parsed.skipOutputAnimation ?? DEFAULT_APP_SETTINGS.skipOutputAnimation,
      autoScrollChat: parsed.autoScrollChat ?? DEFAULT_APP_SETTINGS.autoScrollChat,
      defaultDebugMode: parsed.defaultDebugMode ?? DEFAULT_APP_SETTINGS.defaultDebugMode,
    }
  } catch {
    return { ...DEFAULT_APP_SETTINGS }
  }
}

/**
 * 保存当前设置，统一由设置页调用。
 */
export function saveAppSettings(settings: AppSettings): void {
  localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings))
  window.dispatchEvent(new CustomEvent(APP_SETTINGS_UPDATED_EVENT, { detail: settings }))
}

/**
 * 恢复默认设置，并把结果返回给页面直接使用。
 */
export function resetAppSettings(): AppSettings {
  const resetValue = { ...DEFAULT_APP_SETTINGS }
  saveAppSettings(resetValue)
  return resetValue
}

export type ThinkingMode = 'enabled' | 'disabled'

export interface ModelEndpointConfig {
  model: string
  api_key: string
  base_url: string
  temperature: number
  timeout_seconds: number
  max_retries: number
  thinking_mode: ThinkingMode
}

export interface ModelProfile {
  id: string
  name: string
  llm: ModelEndpointConfig
  summary: ModelEndpointConfig
  embedding: ModelEndpointConfig
  rerank: ModelEndpointConfig
  memory_summary_enabled: boolean
}

export interface ModelConfigState {
  active_profile_id: string
  profiles: ModelProfile[]
}

export const DEFAULT_ENDPOINT_CONFIG: ModelEndpointConfig = {
  model: '',
  api_key: '',
  base_url: '',
  temperature: 0.7,
  timeout_seconds: 60,
  max_retries: 1,
  thinking_mode: 'disabled',
}

const requestModelConfig = async <T>(url: string, options?: RequestInit): Promise<T> => {
  const response = await fetch(url, options)
  if (!response.ok) {
    throw new Error(`模型配置请求失败（HTTP ${response.status}）`)
  }
  return await response.json()
}

// 后端保存后会立即刷新运行时 settings，前端无需额外触发重启或重连。
export const modelConfigService = {
  async load(): Promise<ModelConfigState> {
    return await requestModelConfig<ModelConfigState>('/api/model-config')
  },

  async save(state: ModelConfigState): Promise<ModelConfigState> {
    return await requestModelConfig<ModelConfigState>('/api/model-config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(state),
    })
  },

  async activate(activeProfileId: string): Promise<ModelConfigState> {
    return await requestModelConfig<ModelConfigState>('/api/model-config/active', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active_profile_id: activeProfileId }),
    })
  },
}

export const cloneEndpointConfig = (endpoint: ModelEndpointConfig = DEFAULT_ENDPOINT_CONFIG): ModelEndpointConfig => ({
  ...endpoint,
})

export const createModelProfile = (base?: ModelProfile): ModelProfile => {
  const source = base ?? {
    id: '',
    name: '新方案',
    llm: DEFAULT_ENDPOINT_CONFIG,
    summary: DEFAULT_ENDPOINT_CONFIG,
    embedding: { ...DEFAULT_ENDPOINT_CONFIG, temperature: 0 },
    rerank: { ...DEFAULT_ENDPOINT_CONFIG, temperature: 0, timeout_seconds: 30 },
    memory_summary_enabled: true,
  }

  return {
    id: crypto.randomUUID(),
    name: `${source.name} 副本`,
    llm: cloneEndpointConfig(source.llm),
    summary: cloneEndpointConfig(source.summary),
    embedding: cloneEndpointConfig(source.embedding),
    rerank: cloneEndpointConfig(source.rerank),
    memory_summary_enabled: source.memory_summary_enabled,
  }
}
