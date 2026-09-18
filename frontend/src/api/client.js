import axios from 'axios'

// 开发环境通过 vite proxy 走 /api；生产环境用 VITE_API_BASE
const baseURL = import.meta.env.VITE_API_BASE || ''

export const http = axios.create({
  baseURL,
  timeout: 30000,
})

// 请求拦截：附带 Bearer token
http.interceptors.request.use((config) => {
  const token = localStorage.getItem('mfg_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
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
    // 401：清除本地会话并跳登录页（DEMO 模式后端不返回 401）
    if (err.response && err.response.status === 401) {
      localStorage.removeItem('mfg_token')
      localStorage.removeItem('mfg_user')
      const { path } = err.config || {}
      if (!window.location.pathname.startsWith('/login')) {
        window.location.href = '/login'
      }
      return Promise.reject(new Error('未登录或登录已过期'))
    }
    return Promise.reject(err)
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
  const resp = await fetch(`${baseURL}/api/modules/${code}/invoke`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'text/event-stream',
      ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ inputs, stream: true }),
    signal,
  })
  if (resp.status === 401) {
    window.location.href = '/login'
    throw new Error('未登录或登录已过期')
  }
  if (resp.status === 403) {
    throw new Error('当前账号无权访问该模块')
  }
  if (!resp.ok || !resp.body) {
    throw new Error(`invoke failed: ${resp.status}`)
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buf = ''
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
}
