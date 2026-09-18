import axios from 'axios'

// 开发环境通过 vite proxy 走 /api；生产环境用 VITE_API_BASE
const baseURL = import.meta.env.VITE_API_BASE || ''

const GUEST_KEY = 'mfg_guest_token'

function guestToken() {
  return sessionStorage.getItem(GUEST_KEY) || ''
}

// 访客会话结束（过期/吊销/超次）→ 轻量结束页，不透露任何页面内容
function endGuestSession(message) {
  sessionStorage.removeItem(GUEST_KEY)
  if (!window.location.pathname.startsWith('/expired')) {
    window.location.href = '/expired'
  }
  return Promise.reject(new Error(message || '本次演示访问已结束'))
}

export const http = axios.create({
  baseURL,
  timeout: 30000,
})

// 请求拦截：平台用户 Bearer + 访客 X-Guest-Token
http.interceptors.request.use((config) => {
  const token = localStorage.getItem('mfg_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  const gtoken = guestToken()
  if (gtoken) config.headers['X-Guest-Token'] = gtoken
  return config
})

// 统一响应解包
http.interceptors.response.use(
  (resp) => {
    const body = resp.data
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code === 0) return body.data
      return Promise.reject(new Error(body.message || 'api error'))
    }
    return body
  },
  (err) => {
    const st = err.response?.status
    const body = err.response?.data || {}
    const msg = body.message || err.message
    // 访客态：会话结束类错误 → 结束页（401 过期/吊销/无效；429 quota 超次）
    if (guestToken()) {
      if (st === 401) return endGuestSession(msg)
      if (st === 429 && body.reason === 'quota') return endGuestSession(msg)
      if (st === 429 || st === 403) {
        return Promise.reject(new Error(msg || '请求被拒绝'))
      }
    }
    // 401：清除本地会话并跳登录页
    if (st === 401) {
      localStorage.removeItem('mfg_token')
      localStorage.removeItem('mfg_user')
      if (!window.location.pathname.startsWith('/login')) {
        window.location.href = '/login'
      }
      return Promise.reject(new Error(msg || '未登录或登录已过期'))
    }
    return Promise.reject(err instanceof Error ? err : new Error(msg || 'api error'))
  },
)

// ===== 平台接口 =====
export const api = {
  health: () => http.get('/api/health'),
  listModules: () => http.get('/api/registry/modules'),
  getModule: (code) => http.get(`/api/registry/modules/${code}`),
  login: (username, password) => http.post('/api/auth/login', { username, password }),
  me: () => http.get('/api/auth/me'),
  adminAudit: (params) => http.get('/api/admin/audit', { params }),
  adminStats: () => http.get('/api/admin/stats'),
  // 访客时效链接管理（§2.1，admin）
  adminGuestCreate: (payload) => http.post('/api/admin/guest-tokens', payload),
  adminGuestList: () => http.get('/api/admin/guest-tokens'),
  adminGuestRevoke: (jti) => http.delete(`/api/admin/guest-tokens/${jti}`),
  uploadFile: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return http.post('/api/files/upload', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  // 模块内置样本文件（如 M02 的模拟招标文件），path 来自 manifest.extra_endpoints
  loadSample: (code, path = '/sample') => http.get(`/api/modules/${code}${path}`),
}

// ===== 模块 invoke（SSE 流式） =====
export async function invokeModule(code, inputs, { onChunk, onCitation, onResult, onDone, onError, signal } = {}) {
  const token = localStorage.getItem('mfg_token') || ''
  const gtoken = guestToken()
  const resp = await fetch(`${baseURL}/api/modules/${code}/invoke`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'text/event-stream',
      ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
      ...(gtoken ? { 'X-Guest-Token': gtoken } : {}),
    },
    body: JSON.stringify({ inputs, stream: true }),
    signal,
  })
  if (!resp.ok) {
    // 解析统一门禁返回的错误体（401/403/429 均带 message/reason）
    let body = {}
    try { body = await resp.json() } catch { /* ignore */ }
    const msg = body.message || `invoke failed: ${resp.status}`
    if (resp.status === 401) {
      if (gtoken) return endGuestSession(msg)
      window.location.href = '/login'
      throw new Error(msg || '未登录或登录已过期')
    }
    if (resp.status === 429 && body.reason === 'quota' && gtoken) {
      return endGuestSession(msg)
    }
    throw new Error(msg)
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buf = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      // SSE 帧以双换行分隔
      const frames = buf.split('\n\n')
      buf = frames.pop()
      for (const frame of frames) {
        const lines = frame.split('\n')
        let event = 'message'
        let data = ''
        for (const line of lines) {
          if (line.startsWith('event:')) event = line.slice(6).trim()
          else if (line.startsWith('data:')) data += line.slice(5).trim()
        }
        let payload = {}
        try { payload = JSON.parse(data) } catch { payload = { raw: data } }
        if (event === 'chunk' && onChunk) onChunk(payload)
        else if (event === 'citation' && onCitation) onCitation(payload)
        else if (event === 'result' && onResult) onResult(payload)
        else if (event === 'done' && onDone) onDone(payload)
        else if (event === 'error' && onError) onError(payload)
        else if (event === 'meta') { /* noop, 可记录 trace_id */ }
      }
    }
  } finally {
    // 访客调用完成后通知浮条扣减剩余次数（服务端已扣）
    if (gtoken) window.dispatchEvent(new CustomEvent('guest:used'))
  }
}
