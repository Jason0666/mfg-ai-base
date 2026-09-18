<template>
  <div>
    <div v-if="demoMode" class="demo-banner">
      演示模式 · 数据为样例，非真实企业数据
    </div>
    <nav class="topnav">
      <router-link to="/">模块市场</router-link>
      <router-link to="/solution">落地路径</router-link>
      <router-link to="/admin">管理</router-link>
      <span class="spacer"></span>
      <template v-if="user">
        <span class="user-chip">
          {{ user.display_name || user.username }}
          <span class="role" :class="user.role">{{ roleLabel(user.role) }}</span>
        </span>
        <a href="#" @click.prevent="onLogout">退出</a>
      </template>
      <router-link v-else-if="!demoMode" to="/login">登录</router-link>
    </nav>
    <router-view />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from './api/client'
import { auth, logout } from './store/auth'

const router = useRouter()
const demoMode = ref(false)
const user = computed(() => auth.user)

function roleLabel(role) {
  return { admin: '管理员', analyst: '分析员', viewer: '查看' }[role] || role
}

function onLogout() {
  logout()
  router.push('/')
}

onMounted(async () => {
  try {
    const h = await api.health()
    demoMode.value = !!h.demo_mode
    auth.demoMode = !!h.demo_mode
    sessionStorage.setItem('mfg_demo_mode', String(!!h.demo_mode))
  } catch (e) {
    // 后端未就绪时不显示横幅，不阻断首页
    console.warn('health check failed', e)
  }
})
</script>

<style scoped>
.topnav {
  display: flex;
  gap: 16px;
  align-items: center;
  padding: 10px 16px;
  background: var(--bg-panel);
  border-bottom: 1px solid var(--border);
}
.topnav a { color: var(--text-secondary); font-weight: 500; }
.topnav a.router-link-exact-active { color: var(--accent); }
.spacer { flex: 1; }
.user-chip { display: inline-flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text-primary); }
.role {
  font-size: 11px;
  padding: 1px 8px;
  border-radius: 10px;
  border: 1px solid var(--border);
  color: var(--text-secondary);
}
.role.admin { color: var(--accent); border-color: var(--accent); }
.role.analyst { color: var(--accent-warn); border-color: var(--accent-warn); }
</style>
