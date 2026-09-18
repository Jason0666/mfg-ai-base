<template>
  <div class="container">
    <div class="card">
      <div class="head">
        <h1>管理与审计</h1>
        <span v-if="demoMode" class="mode-tag demo">演示模式 · 审计已持久化到本地 SQLite</span>
        <span v-else class="mode-tag prod">生产模式</span>
      </div>

      <!-- 需要登录（生产模式非 admin） -->
      <div v-if="needLogin" class="login-tip">
        <p>当前页面需要 <strong>admin</strong> 角色登录后查看。</p>
        <router-link class="btn primary" :to="{ name: 'login', query: { redirect: '/admin' } }">
          前往登录 →
        </router-link>
      </div>

      <template v-else>
        <!-- 调用统计 -->
        <h2>调用统计</h2>
        <div v-if="stats" class="stats">
          <div class="stat"><strong>{{ stats.total_invocations }}</strong><span>总调用</span></div>
          <div class="stat"><strong class="ok">{{ stats.success }}</strong><span>成功</span></div>
          <div class="stat"><strong class="warn">{{ stats.degraded }}</strong><span>降级</span></div>
          <div class="stat"><strong class="bad">{{ stats.failed }}</strong><span>失败</span></div>
          <div class="stat"><strong>{{ stats.avg_latency_ms }}</strong><span>平均延迟 ms</span></div>
        </div>

        <!-- 近 7 日趋势 -->
        <div v-if="stats && stats.trend_7d && stats.trend_7d.length" class="trend">
          <div v-for="d in stats.trend_7d" :key="d.date" class="trend-col">
            <div class="trend-bar-wrap">
              <div class="trend-bar" :style="{ height: barHeight(d.count) + 'px' }" :title="`${d.date}: ${d.count}`"></div>
            </div>
            <span class="trend-count">{{ d.count }}</span>
            <span class="trend-date">{{ d.date.slice(5) }}</span>
          </div>
        </div>

        <!-- 模块分布 -->
        <div v-if="stats && stats.by_module && stats.by_module.length" class="by-module">
          <span v-for="m in stats.by_module" :key="m.module" class="pill mono">
            {{ m.module }} · {{ m.count }}
          </span>
        </div>

        <!-- 筛选栏 -->
        <h2>审计日志</h2>
        <div class="filters">
          <select v-model="filters.module">
            <option value="">全部模块</option>
            <option v-for="m in modules" :key="m.code" :value="m.code">{{ m.name }}（{{ m.code }}）</option>
          </select>
          <select v-model="filters.status">
            <option value="">全部状态</option>
            <option value="success">success</option>
            <option value="degraded">degraded</option>
            <option value="failed">failed</option>
          </select>
          <select v-model="filters.action">
            <option value="">全部动作</option>
            <option value="invoke">invoke</option>
            <option value="login">login</option>
            <option value="upload">upload</option>
            <option value="export">export</option>
          </select>
          <input v-model.trim="filters.q" type="text" placeholder="搜索问题/答案关键词…" />
          <button class="btn" @click="applyFilters">查询</button>
        </div>

        <!-- 日志表 -->
        <div v-if="logs.length" class="logs">
          <div v-for="log in logs" :key="log.id" class="log-item" @click="toggle(log)">
            <div class="log-row">
              <span class="time mono">{{ fmtTime(log.created_at) }}</span>
              <span class="uid">{{ log.username || ('用户' + (log.user_id ?? '-')) }}</span>
              <span class="tag" :class="log.status">{{ log.status }}</span>
              <span class="action">{{ log.action }}</span>
              <span class="module mono">{{ log.module_code || '—' }}</span>
              <span class="latency">{{ log.latency_ms ?? '-' }}ms</span>
              <span class="trace mono">{{ (log.trace_id || '').slice(0, 8) }}</span>
            </div>
            <div v-if="expanded[log.id]" class="log-detail" @click.stop>
              <div class="detail-sec">
                <div class="label">问题</div>
                <pre class="mono">{{ log.question || '（无）' }}</pre>
              </div>
              <div class="detail-sec">
                <div class="label">回答</div>
                <pre class="mono answer">{{ log.answer || '（无）' }}</pre>
              </div>
              <div v-if="log.citations && log.citations.length" class="detail-sec">
                <div class="label">引用（{{ log.citations.length }}）</div>
                <ul class="cites">
                  <li v-for="(c, i) in log.citations" :key="i" class="mono">
                    {{ c.doc || c.doc_name || '' }} {{ c.version ? `v${c.version}` : '' }} {{ c.clause ? `条款${c.clause}` : '' }} {{ c.page ? `第${c.page}页` : '' }}
                  </li>
                </ul>
              </div>
              <div class="detail-sec meta-line mono">
                model: {{ log.model || '—' }} · tokens: {{ log.token_in ?? '-' }}/{{ log.token_out ?? '-' }} · trace: {{ log.trace_id }}
              </div>
            </div>
          </div>
        </div>
        <p v-else class="empty">暂无审计记录</p>

        <!-- 分页 -->
        <div v-if="total > filters.limit" class="pager">
          <button class="btn" :disabled="filters.offset === 0" @click="page(-1)">上一页</button>
          <span class="pager-info">{{ filters.offset + 1 }}-{{ Math.min(filters.offset + filters.limit, total) }} / 共 {{ total }} 条</span>
          <button class="btn" :disabled="filters.offset + filters.limit >= total" @click="page(1)">下一页</button>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { api } from '../api/client'
import { auth, fetchMe } from '../store/auth'

const demoMode = ref(true)
const needLogin = ref(false)
const stats = ref(null)
const logs = ref([])
const total = ref(0)
const modules = ref([])
const expanded = reactive({})

const filters = reactive({
  module: '',
  status: '',
  action: '',
  q: '',
  limit: 50,
  offset: 0,
})

function fmtTime(t) {
  if (!t) return ''
  const d = new Date(t)
  if (isNaN(d)) return String(t).replace('T', ' ').slice(0, 19)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function barHeight(count) {
  const s = stats.value
  if (!s || !s.trend_7d) return 4
  const max = Math.max(...s.trend_7d.map((d) => d.count), 1)
  return 8 + Math.round((count / max) * 56)
}

function toggle(log) {
  expanded[log.id] = !expanded[log.id]
}

function applyFilters() {
  filters.offset = 0
  loadLogs()
}

function page(dir) {
  filters.offset += dir * filters.limit
  loadLogs()
}

async function loadLogs() {
  try {
    const params = { limit: filters.limit, offset: filters.offset }
    if (filters.module) params.module = filters.module
    if (filters.status) params.status = filters.status
    if (filters.action) params.action = filters.action
    if (filters.q) params.q = filters.q
    const data = await api.adminAudit(params)
    logs.value = data.items || []
    total.value = data.total || 0
    for (const it of logs.value) expanded[it.id] = false
  } catch (e) {
    console.error('audit load failed', e)
  }
}

onMounted(async () => {
  try {
    const h = await api.health()
    demoMode.value = !!h.demo_mode
    auth.demoMode = demoMode.value
    sessionStorage.setItem('mfg_demo_mode', String(demoMode.value))
  } catch (e) { /* ignore */ }

  // 生产模式下校验身份；DEMO 直接放行
  if (!demoMode.value) {
    const me = await fetchMe()
    if (!me || me.role !== 'admin') {
      needLogin.value = true
      return
    }
  }

  try {
    stats.value = await api.adminStats()
    const mods = await api.listModules()
    modules.value = mods.modules || mods || []
  } catch (e) {
    console.error('stats load failed', e)
  }
  loadLogs()
})
</script>

<style scoped>
.head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
h1 { margin: 0; }
h2 { font-size: 15px; margin: 18px 0 8px; }
.mode-tag { font-size: 12px; padding: 2px 10px; border-radius: 10px; border: 1px solid var(--border); color: var(--text-secondary); }
.mode-tag.prod { color: var(--accent); border-color: var(--accent); }

.login-tip {
  padding: 32px 0;
  text-align: center;
  color: var(--text-secondary);
}
.login-tip .btn { display: inline-block; margin-top: 12px; padding: 8px 18px; border-radius: 8px; background: var(--accent); color: #0F1720; text-decoration: none; }

.stats { display: flex; flex-wrap: wrap; gap: 12px; }
.stat {
  flex: 1;
  min-width: 110px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 14px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.stat strong { font-size: 20px; }
.stat span { font-size: 12px; color: var(--text-secondary); }
.ok { color: var(--accent); }
.warn { color: var(--accent-warn); }
.bad { color: var(--accent-danger); }

.trend { display: flex; align-items: flex-end; gap: 14px; margin-top: 14px; padding: 10px; background: var(--bg-elevated); border-radius: 8px; }
.trend-col { display: flex; flex-direction: column; align-items: center; gap: 3px; }
.trend-bar-wrap { height: 64px; display: flex; align-items: flex-end; }
.trend-bar { width: 26px; background: var(--accent); border-radius: 3px 3px 0 0; opacity: 0.85; }
.trend-count { font-size: 12px; color: var(--text-primary); }
.trend-date { font-size: 11px; color: var(--text-secondary); }

.by-module { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.pill { font-size: 12px; padding: 2px 10px; border-radius: 12px; background: var(--bg-elevated); border: 1px solid var(--border); color: var(--text-secondary); }

.filters { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.filters select, .filters input {
  padding: 7px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-elevated);
  color: var(--text-primary);
  font-size: 13px;
}
.filters input { flex: 1; min-width: 200px; }
.btn { padding: 7px 16px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-elevated); color: var(--text-primary); cursor: pointer; font-size: 13px; }
.btn:disabled { opacity: 0.4; cursor: not-allowed; }

.logs { display: flex; flex-direction: column; }
.log-item { border-bottom: 1px dashed var(--border); cursor: pointer; }
.log-item:hover { background: var(--bg-elevated); }
.log-row { display: grid; grid-template-columns: 150px 64px 78px 70px 1fr 70px 90px; gap: 8px; align-items: center; padding: 6px 6px; font-size: 12.5px; }
.time { color: var(--text-secondary); }
.uid { color: var(--text-secondary); font-size: 12px; }
.module { color: var(--accent); overflow-wrap: anywhere; }
.action { color: var(--text-secondary); }
.trace { color: var(--text-secondary); font-size: 11px; text-align: right; }
.tag { padding: 1px 8px; border-radius: 4px; font-size: 11px; text-align: center; }
.tag.success { background: var(--accent); color: #0F1720; }
.tag.degraded { background: var(--accent-warn); color: #0F1720; }
.tag.failed { background: var(--accent-danger); color: #fff; }

.log-detail { padding: 4px 10px 12px 12px; }
.detail-sec { margin-top: 8px; }
.label { font-size: 12px; color: var(--text-secondary); margin-bottom: 3px; }
.log-detail pre {
  margin: 0;
  padding: 8px 10px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 220px;
  overflow: auto;
}
.cites { margin: 0; padding-left: 18px; font-size: 12px; line-height: 1.7; color: var(--text-secondary); }
.meta-line { font-size: 11.5px; color: var(--text-secondary); }

.empty { color: var(--text-secondary); font-size: 13px; }
.pager { display: flex; align-items: center; gap: 14px; margin-top: 12px; }
.pager-info { font-size: 12.5px; color: var(--text-secondary); }
.mono { font-family: var(--font-mono); }
</style>
