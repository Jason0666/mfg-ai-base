// 访客时效访问状态（§2.1）：token 存 sessionStorage（关标签页即失效，需重新用链接校验）
import { reactive } from 'vue'

const KEY = 'mfg_guest_token'
const BASE = import.meta.env.VITE_API_BASE || ''

export const guest = reactive({
  token: sessionStorage.getItem(KEY) || '',
  info: null, // { note, exp, remaining_seconds, max_calls, used_calls, allow_real_llm, ... }
})

export function isGuest() {
  return !!guest.token
}

export function clearGuest() {
  guest.token = ''
  guest.info = null
  sessionStorage.removeItem(KEY)
}

function save(token, info) {
  guest.token = token
  guest.info = info
  sessionStorage.setItem(KEY, token)
}

// 独立走 fetch（不经 api/client 拦截器，避免 401 跳登录的循环）
async function verifyRaw(token) {
  const resp = await fetch(`${BASE}/api/guest/verify`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  })
  const body = await resp.json().catch(() => ({}))
  return body
}

/**
 * 校验并保存访客令牌。返回 { ok, reason?, message?, info? }
 * reason: invalid | expired | revoked | quota
 */
export async function verifyToken(token) {
  const body = await verifyRaw(token)
  if (body && body.code === 0 && body.data) {
    save(token, body.data)
    return { ok: true, info: body.data }
  }
  clearGuest()
  return { ok: false, reason: body?.reason || 'invalid', message: body?.message || '访问链接无效' }
}

/** 刷新剩余时效/配额（浮条轮询用）；失败时清除本地访客态并返回原因 */
export async function refreshInfo() {
  if (!guest.token) return { ok: false, reason: 'invalid' }
  const body = await verifyRaw(guest.token)
  if (body && body.code === 0 && body.data) {
    guest.info = body.data
    return { ok: true, info: body.data }
  }
  clearGuest()
  return { ok: false, reason: body?.reason || 'invalid', message: body?.message }
}

export function remainingMinutes() {
  if (!guest.info?.exp) return 0
  return Math.max(0, Math.ceil((guest.info.exp * 1000 - Date.now()) / 60000))
}

export function remainingCalls() {
  if (!guest.info) return 0
  return Math.max(0, (guest.info.max_calls || 0) - (guest.info.used_calls || 0))
}
