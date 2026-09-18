<template>
  <div class="login-wrap">
    <div class="login-card">
      <h1>登录</h1>
      <p class="sub">制造业 AI 应用底座 · 账号登录</p>
      <form @submit.prevent="onSubmit">
        <label>
          用户名
          <input v-model.trim="username" type="text" autocomplete="username" placeholder="请输入用户名" />
        </label>
        <label>
          密码
          <input v-model="password" type="password" autocomplete="current-password" placeholder="请输入密码" />
        </label>
        <div v-if="error" class="error">{{ error }}</div>
        <button class="btn primary" type="submit" :disabled="loading || !username || !password">
          {{ loading ? '登录中…' : '登 录' }}
        </button>
      </form>
      <p class="hint">初始密码由部署方提供（生产首启时见后端启动日志）。</p>
      <router-link class="back" to="/">返回首页 →</router-link>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { login } from '../store/auth'

const router = useRouter()
const route = useRoute()
const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function onSubmit() {
  error.value = ''
  loading.value = true
  try {
    await login(username.value, password.value)
    router.push(route.query.redirect || '/')
  } catch (e) {
    error.value = e?.message || '登录失败，请检查用户名与密码'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  min-height: calc(100vh - 60px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.login-card {
  width: 360px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 28px 28px 20px;
}
h1 { margin: 0 0 4px; font-size: 22px; }
.sub { color: var(--text-secondary); font-size: 13px; margin: 0 0 18px; }
label { display: block; font-size: 13px; color: var(--text-secondary); margin-bottom: 12px; }
input {
  display: block;
  width: 100%;
  box-sizing: border-box;
  margin-top: 4px;
  padding: 9px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-elevated);
  color: var(--text-primary);
  font-size: 14px;
}
input:focus { outline: none; border-color: var(--accent); }
.error {
  background: rgba(220, 60, 60, 0.12);
  color: var(--accent-danger);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 13px;
  margin-bottom: 12px;
}
.btn {
  width: 100%;
  padding: 10px 0;
  border: none;
  border-radius: 8px;
  font-size: 14px;
  cursor: pointer;
}
.btn.primary { background: var(--accent); color: #0F1720; font-weight: 600; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.hint { font-size: 12px; color: var(--text-secondary); margin: 14px 0 6px; }
.back { font-size: 13px; }
</style>
