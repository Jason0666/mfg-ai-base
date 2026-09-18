<template>
  <div class="expired-page">
    <div class="panel">
      <div class="icon">⏱</div>
      <h1>本次演示访问已结束</h1>
      <p class="desc">如需继续查看，请联系获取新的访问链接</p>
      <p v-if="reasonText" class="reason">{{ reasonText }}</p>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { clearGuest } from '../store/guest'

const route = useRoute()
const reasonText = computed(() => ({
  expired: '链接已超过有效期',
  revoked: '链接已被吊销',
  quota: '调用次数已用完',
  invalid: '',
}[route.query.reason] || ''))

onMounted(() => {
  // 双保险：确保本地不残留访客凭证
  clearGuest()
})
</script>

<style scoped>
.expired-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg, #F5F7FA);
}
.panel {
  text-align: center;
  padding: 48px 56px;
  background: var(--bg-panel, #fff);
  border: 1px solid var(--border, #E5E9F0);
  border-radius: 14px;
  box-shadow: 0 6px 24px rgba(15, 23, 32, 0.06);
}
.icon { font-size: 42px; margin-bottom: 12px; }
h1 { font-size: 20px; margin: 0 0 10px; color: var(--text-primary, #1F2A37); }
.desc { color: var(--text-secondary, #6B7686); font-size: 14px; margin: 0 0 6px; }
.reason { color: var(--text-secondary, #6B7686); font-size: 12px; opacity: 0.8; margin: 0; }
</style>
