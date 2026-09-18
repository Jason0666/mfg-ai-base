<template>
  <div class="guest-bar" role="status">
    <span class="dot"></span>
    <span class="text">
      演示访问<template v-if="note">（{{ note }}）</template>
      · 剩余 <strong>{{ minutes }}</strong> 分钟
      · 剩余 <strong>{{ calls }}</strong> 次调用
    </span>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { guest, refreshInfo, remainingMinutes, remainingCalls } from '../store/guest'

const router = useRouter()
const tick = ref(0)
const minutes = computed(() => { void tick.value; return remainingMinutes() })
const calls = computed(() => { void tick.value; return remainingCalls() })
const note = computed(() => guest.info?.note || '')

let timer = null

function onUsed() {
  // 服务端已在 invoke 时扣减，这里本地同步递减保证"实时递减"观感
  if (guest.info) guest.info.used_calls = (guest.info.used_calls || 0) + 1
  tick.value++
}

async function poll() {
  tick.value++
  const r = await refreshInfo()
  if (!r.ok) {
    // 过期/吊销/超次 → 结束页（零内容泄露）
    router.push({ name: 'expired', query: { reason: r.reason } })
  }
}

onMounted(() => {
  window.addEventListener('guest:used', onUsed)
  poll() // 挂载立即拉取一次（刷新后 info 为内存空，需用 token 重新换取剩余时效/配额）
  timer = setInterval(poll, 30000)
})

onBeforeUnmount(() => {
  window.removeEventListener('guest:used', onUsed)
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.guest-bar {
  position: sticky;
  top: 0;
  z-index: 60;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 6px 16px;
  font-size: 13px;
  color: #7C5A00;
  background: linear-gradient(90deg, #FFF4D6, #FFEDBF);
  border-bottom: 1px solid #EAD79A;
}
.guest-bar strong { color: #5C4200; }
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #E8A800;
  animation: pulse 2s infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
.text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
