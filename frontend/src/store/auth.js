// 登录态管理：token + 用户信息，localStorage 持久化
import { reactive } from 'vue'
import { api } from '../api/client'

const TOKEN_KEY = 'mfg_token'
const USER_KEY = 'mfg_user'

export const auth = reactive({
  token: localStorage.getItem(TOKEN_KEY) || '',
  user: JSON.parse(localStorage.getItem(USER_KEY) || 'null'),
  demoMode: true, // 由 App.vue 启动时从 /api/health 更新

  get isLoggedIn() {
    return !!this.token || this.demoMode // DEMO 模式视为已登录（匿名 viewer）
  },
})

export function saveSession(token, user) {
  auth.token = token
  auth.user = user
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

export function clearSession() {
  auth.token = ''
  auth.user = null
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export async function login(username, password) {
  const data = await api.login(username, password)
  saveSession(data.token, data.user)
  return data.user
}

export async function fetchMe() {
  try {
    const user = await api.me()
    if (user && user.id !== undefined) {
      // 与本地 token 对齐：DEMO 匿名不覆盖已保存会话
      if (auth.token) saveSession(auth.token, user)
      return user
    }
  } catch (e) {
    // ignore
  }
  return null
}

export function logout() {
  clearSession()
}
