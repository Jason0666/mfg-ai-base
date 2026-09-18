import { createRouter, createWebHistory } from 'vue-router'
import ShowroomView from '../views/ShowroomView.vue'
import WorkbenchView from '../views/WorkbenchView.vue'
import { verifyToken, clearGuest } from '../store/guest'

const routes = [
  { path: '/', name: 'showroom', component: ShowroomView },
  { path: '/m/:code', name: 'workbench', component: WorkbenchView, props: true },
  {
    path: '/solution',
    name: 'solution',
    component: () => import('../views/SolutionView.vue'),
  },
  {
    path: '/admin',
    name: 'admin',
    component: () => import('../views/AdminView.vue'),
  },
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/LoginView.vue'),
  },
  {
    // 访客会话结束页（过期/吊销/超次）：零内容泄露
    path: '/expired',
    name: 'expired',
    component: () => import('../views/GuestExpiredView.vue'),
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 访问门禁（§2.1）：
// 1. 链接携带 ?t=<token> → 先服务端校验，通过后落 sessionStorage 并去掉 URL 参数
// 2. 已登录用户（localStorage 管理员 JWT）→ 放行
// 3. 访客令牌（sessionStorage）→ 放行演示页，禁止进入 /admin
// 4. 无任何凭证 → 登录页
router.beforeEach(async (to) => {
  // 链接带访客令牌：校验后再落地（去除 URL 参数，避免令牌残留在地址栏/历史记录）
  if (to.query.t) {
    const t = String(to.query.t)
    const r = await verifyToken(t)
    if (r.ok) {
      return { path: to.path || '/', query: {}, hash: to.hash }
    }
    return { name: 'expired', query: { reason: r.reason } }
  }

  if (to.name === 'expired' || to.name === 'login') return true

  const adminToken = localStorage.getItem('mfg_token')
  if (adminToken) {
    // 已登录用户仍禁止凭访客身份外的一切进入管理页之外无需限制
    return true
  }

  const gtoken = sessionStorage.getItem('mfg_guest_token')
  if (gtoken) {
    if (to.name === 'admin') return { name: 'showroom' } // 访客禁止管理页
    return true
  }

  clearGuest()
  return { name: 'login', query: to.fullPath !== '/' ? { redirect: to.fullPath } : {} }
})

export default router
