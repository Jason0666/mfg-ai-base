import { createRouter, createWebHistory } from 'vue-router'
import ShowroomView from '../views/ShowroomView.vue'
import WorkbenchView from '../views/WorkbenchView.vue'

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
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 生产模式（demo_mode=false）访问业务页时若未登录 → 跳登录页。
// 登录态以 localStorage 的 token 判断；DEMO 模式不拦（后端也不拦）。
router.beforeEach((to) => {
  if (to.name === 'login') return true
  const token = localStorage.getItem('mfg_token')
  if (token) return true
  // 尚未探测 demo_mode 前（刷新直链），由页面内 fetchMe/401 处理兜底；
  // 这里仅对 admin 做跳转（生产下 admin 页需要登录态）。
  if (to.name === 'admin' && sessionStorage.getItem('mfg_demo_mode') === 'false') {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  return true
})

export default router
