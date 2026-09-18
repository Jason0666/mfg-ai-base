<template>
  <div class="container">
    <!-- §1.4 价值主张 -->
    <section class="hero">
      <h1>用「能点开跑的工程底座」替代「简历 + 经验口述」</h1>
      <p class="sub">80% 骨架已就位，剩 20% 是「接你数据」。客户拿到能直接跑，改配置接库后即可上线。</p>
      <div class="contrast">
        <div><strong>传统做法</strong>：设备故障靠老师傅经验 / 几百页标书人工逐条比对 / 质量报告写一整天 / SOP 沦为摆设 / 想看数据要找 IT 拉报表</div>
        <div><strong>本底座</strong>：知识库+检索溯源 / 智能审核零漏检 / 一句话生成 8D 初稿 / 口语化提问精准定位 / 自然语言直接问数</div>
      </div>
    </section>

    <!-- §10.1 模块卡片网格，按 category 分组 -->
    <section v-if="loading" class="card"><p>加载模块清单中…</p></section>
    <section v-else-if="error" class="card"><p style="color: var(--accent-danger)">加载失败：{{ error }}</p></section>
    <section v-else>
      <div v-for="cat in categories" :key="cat.name" style="margin-top: 24px;">
        <h2 class="cat-title">{{ cat.label }} <span class="count">({{ cat.items.length }})</span></h2>
        <div class="grid">
          <div v-for="m in cat.items" :key="m.code" class="card module-card" @click="open(m.code)">
            <div class="card-head">
              <span class="icon">{{ iconMap[m.icon] || '◆' }}</span>
              <div>
                <div class="title">{{ m.name }}</div>
                <div class="code mono">{{ m.code }}</div>
              </div>
            </div>
            <p class="summary">{{ m.summary }}</p>
            <div v-if="m.pain_points && m.pain_points.length" class="pain">
              <strong>痛点：</strong>{{ m.pain_points[0] }}
            </div>
            <div v-if="m.deployment_requirements" class="deploy">
              <span class="tag low">数据需求 {{ m.deployment_requirements.data_needed.length }} 项</span>
              <span class="tag low">周期 {{ m.deployment_requirements.timeline || '—' }}</span>
            </div>
          </div>
        </div>
      </div>
      <p v-if="!modules.length" class="card">尚未加载到任何模块。</p>
    </section>

    <!-- §10.1 底部落地路径图 -->
    <section class="card" style="margin-top: 32px;">
      <h2>落地路径</h2>
      <ol class="roadmap">
        <li><strong>14 天 POC</strong>：选定一个场景，接入客户数据，跑通单模块</li>
        <li><strong>单场景试点</strong>：扩到一条产线/一个车间，验证采纳率</li>
        <li><strong>横向复制</strong>：把同套底座推广到其他场景/事业部</li>
      </ol>
    </section>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client'

const router = useRouter()
const modules = ref([])
const loading = ref(true)
const error = ref('')

const iconMap = {
  wrench: '🔧',
  cube: '◆',
  doc: '📄',
  chart: '📊',
  shield: '🛡️',
}

const categoryLabels = {
  document: '文档智能',
  knowledge: '知识智能',
  data: '数据智能',
}

const categories = computed(() => {
  const groups = {}
  for (const m of modules.value) {
    const c = m.category || 'other'
    if (!groups[c]) groups[c] = []
    groups[c].push(m)
  }
  return Object.keys(groups).map((name) => ({
    name,
    label: categoryLabels[name] || name,
    items: groups[name].sort((a, b) => (a.order || 0) - (b.order || 0)),
  }))
})

onMounted(async () => {
  try {
    modules.value = (await api.listModules()) || []
  } catch (e) {
    error.value = e.message || String(e)
  } finally {
    loading.value = false
  }
})

function open(code) {
  router.push(`/m/${code}`)
}
</script>

<style scoped>
.hero h1 { font-size: 24px; margin: 0 0 8px; }
.hero .sub { color: var(--text-secondary); margin: 0 0 16px; }
.contrast { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; font-size: 14px; color: var(--text-secondary); }
.contrast strong { color: var(--text-primary); }
.cat-title { font-size: 18px; margin: 0 0 12px; border-bottom: 1px solid var(--border); padding-bottom: 6px; }
.count { color: var(--text-secondary); font-size: 14px; font-weight: normal; }
.module-card { cursor: pointer; transition: border-color .15s; }
.module-card:hover { border-color: var(--accent); }
.card-head { display: flex; gap: 12px; align-items: center; }
.icon { font-size: 24px; }
.title { font-size: 16px; font-weight: 600; }
.code { color: var(--text-secondary); font-size: 12px; }
.summary { color: var(--text-secondary); font-size: 13px; margin: 8px 0; min-height: 2.4em; }
.pain { font-size: 12px; color: var(--accent-warn); }
.deploy { display: flex; gap: 6px; margin-top: 8px; }
.roadmap { padding-left: 20px; line-height: 1.9; }
@media (max-width: 768px) { .contrast { grid-template-columns: 1fr; } }
</style>
