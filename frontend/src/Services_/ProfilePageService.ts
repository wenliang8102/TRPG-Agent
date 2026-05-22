// frontend/src/Services_/ProfilePageService.ts

/**
 * 用户资料接口的临时契约。
 * 这里先固定一版最小字段集，后端接入时只需要对齐这层即可。
 */
export interface ProfileDto {
  id: string
  username: string
  display_name: string
  title: string | null
  joined_at: string
  bio: string | null
  avatar_url: string | null
}

/**
 * ProfilePage 的页面模型。
 * 页面只消费这里的字段，避免直接依赖后端命名。
 */
export interface ProfilePageData {
  username: string
  displayName: string
  title: string
  joinDate: string
  accountId: string
  bio: string
  avatarUrl: string | null
}

const PROFILE_ENDPOINT = '/api/profile/me'

/**
 * 在后端尚未就绪时提供稳定占位数据，便于页面先联通 service 层。
 */
export const PROFILE_PAGE_MOCK_DATA: ProfilePageData = {
  username: 'Dragon_Knight_47',
  displayName: '格里芬·龙裔',
  title: '传奇冒险者 · 龙骑士团长',
  joinDate: '2024-10-23',
  accountId: 'UID: 102347',
  bio: '',
  avatarUrl: null,
}

/**
 * 统一处理加入时间的展示格式，避免后端直接返回面向 UI 的文案。
 */
function formatJoinDate(joinedAt: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date(joinedAt))
}

/**
 * 将后端 DTO 映射为页面模型，隔离字段命名和展示格式。
 */
export function mapProfileDtoToPageData(profile: ProfileDto): ProfilePageData {
  return {
    username: profile.username,
    displayName: profile.display_name,
    title: profile.title ?? '暂无称号',
    joinDate: formatJoinDate(profile.joined_at),
    accountId: profile.id,
    bio: profile.bio ?? '',
    avatarUrl: profile.avatar_url,
  }
}

/**
 * 读取当前用户资料。
 * 后端第一版只需要实现 GET /api/profile/me 并返回 ProfileDto。
 */
export async function fetchProfilePageData(): Promise<ProfilePageData> {
  const response = await fetch(PROFILE_ENDPOINT)

  if (!response.ok) {
    throw new Error(`获取用户资料失败: ${response.status}`)
  }

  const profile = await response.json() as ProfileDto
  return mapProfileDtoToPageData(profile)
}
