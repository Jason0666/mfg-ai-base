<template>
  <div class="container">
    <div v-if="loading" class="card"><p>加载模块清单中…</p></div>
    <div v-else-if="error" class="card"><p style="color: var(--accent-danger)">模块加载失败：{{ error }}</p></div>
    <div v-else-if="!manifest" class="card"><p>未找到模块：{{ code }}</p></div>
    <div v-else>
      <header class="head">
        <h1>{{ manifest.name }}</h1>
        <p class="summary">{{ manifest.summary }}</p>
      </header>

      <!-- 示例模块横幅：硬编码模拟数据，非真实链路 -->
      <div v-if="manifest.sample" class="sample-banner">
        <span class="sample-tag">示例数据</span>
        <span>{{ manifest.sample_notice || '本模块为能力演示，数据为内置模拟数据。' }}</span>
      </div>

      <!-- 推荐问题 -->
      <section v-if="suggestedQuestions.length" class="card">
        <h3>推荐问题</h3>
        <div class="suggest">
          <button v-for="q in suggestedQuestions" :key="q" @click="useQuestion(q)">{{ q }}</button>
        </div>
      </section>

      <div class="workbench">
        <!-- 左侧输入区（按 input_schema 渲染） -->
        <section class="card input-panel">
          <h3>输入</h3>
          <SchemaInput
            ref="schemaInputRef"
            :schema="manifest.input_schema"
            v-model="inputs"
            :module-code="code"
            :sample-path="samplePath"
          />
          <div class="actions">
            <button class="primary" :disabled="streaming" @click="submit">
              {{ streaming ? '调用中…' : '提交' }}
            </button>
            <button @click="reset" :disabled="streaming">重置</button>
          </div>
        </section>

        <!-- 右侧输出区 -->
        <section class="card output-panel">
          <h3>输出</h3>
          <!-- JSON 结构化流：流式阶段显示进度骨架屏，结束后由 SchemaOutput 渲染 -->
          <div v-if="streaming && streamIsJson" class="skeleton-card">
            <div
              v-for="(s, i) in skeletonPhases"
              :key="i"
              class="skel-row"
              :class="{ active: skelPhase === i, done: skelPhase > i }"
            >
              <span class="skel-dot"></span>
              <span class="skel-text">{{ s }}</span>
            </div>
          </div>
          <!-- 纯文本流（提示信息等）正常打字机显示 -->
          <StreamText v-else :text="streamText" :streaming="streaming" />
          <CitationCard v-if="citations.length" :items="citations" />
          <SchemaOutput v-if="result" :schema="manifest.output_schema" :data="result" />
          <DeployCard v-if="manifest.deployment_requirements" :req="manifest.deployment_requirements" />
        </section>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { invokeModule, api } from '../api/client'
import SchemaInput from '../components/SchemaInput.vue'
import SchemaOutput from '../components/SchemaOutput.vue'
import StreamText from '../components/StreamText.vue'
import CitationCard from '../components/CitationCard.vue'
import DeployCard from '../components/DeployCard.vue'

const props = defineProps({ code: { type: String, required: true } })
const code = computed(() => props.code)

const manifest = ref(null)
const loading = ref(true)
const error = ref('')
const inputs = ref({})
const streamText = ref('')
const citations = ref([])
const result = ref(null)
const streaming = ref(false)
const schemaInputRef = ref(null)

// 结构化 JSON 流：流式阶段不展示原文，改用三阶段骨架屏
const streamIsJson = ref(false)
const skelPhase = ref(0)
let skelTimer = null

// 不同模块的进度文案（capabilities 微调，避免 m05 显示"检索知识库"）
const skeletonPhases = computed(() => {
  const c = code.value
  if (c === 'm05_data_bi') {
    return ['正在查询生产数据…', '正在智能分析…', '正在生成图表与结论…']
  }
  if (c === 'm06_energy_analysis') {
    return ['正在采集能耗数据…', '正在识别异常时段…', '正在生成节能建议…']
  }
  return ['正在检索知识库…', '正在生成分析…', '正在整理引用来源…']
})

function startSkeleton() {
  skelPhase.value = 0
  clearInterval(skelTimer)
  skelTimer = setInterval(() => {
    if (skelPhase.value < 2) skelPhase.value += 1
  }, 2200)
}
function stopSkeleton() {
  clearInterval(skelTimer)
  skelTimer = null
}

const suggestedQuestions = computed(() => manifest.value?.demo?.suggested_questions || [])
const fileKey = computed(() =>
  Object.entries(manifest.value?.input_schema?.properties || {})
    .find(([, p]) => p.widget === 'file_upload')?.[0] || '')
const samplePath = computed(() =>
  manifest.value?.extra_endpoints?.find((e) => /sample/i.test(e.path || ''))?.path || '')

async function loadManifest() {
  loading.value = true
  error.value = ''
  try {
    manifest.value = await api.getModule(code.value)
    // 初始化 inputs 默认值
    const props_ = manifest.value?.input_schema?.properties || {}
    const init = {}
    for (const k of Object.keys(props_)) {
      init[k] = props_[k].type === 'object' ? {} : ''
    }
    inputs.value = init
  } catch (e) {
    error.value = e.message || String(e)
  } finally {
    loading.value = false
  }
}

async function useQuestion(q) {
  // 文件型模块（如 M02）：自动载入内置样本文件，问题文本写入 focus
  if (fileKey.value) {
    if (!inputs.value[fileKey.value]) {
      await schemaInputRef.value?.loadSample(fileKey.value)
    }
    if ('focus' in inputs.value) {
      inputs.value.focus = q.split('（')[0].replace(/[？?]\s*$/, '')
    }
    return
  }
  // 推荐问题优先填入长文本字段（textarea），其次按常见字段名，最后退回第一个 string 字段
  const props_ = manifest.value?.input_schema?.properties || {}
  const keys = Object.keys(props_)
  const preferNames = ['question', 'query', 'symptom', 'defect_desc', 'content', 'text']
  let target = keys.find((k) => props_[k].widget === 'textarea' && props_[k].type === 'string')
  if (!target) target = keys.find((k) => preferNames.includes(k))
  if (!target) target = keys.find((k) => props_[k].type === 'string')
  if (target) inputs.value[target] = q
}

async function submit() {
  streamText.value = ''
  citations.value = []
  result.value = null
  streamIsJson.value = false
  skelPhase.value = 0
  streaming.value = true
  startSkeleton()
  try {
    await invokeModule(code.value, inputs.value, {
      onChunk: (p) => {
        const piece = p.text || ''
        // 首个非空片段决定流类型：JSON 结构化流不显示原文（骨架屏 + 结束后 SchemaOutput）
        if (!streamIsJson.value && streamText.value === '' && piece.trimStart().startsWith('{')) {
          streamIsJson.value = true
        }
        if (!streamIsJson.value) streamText.value += piece
      },
      onCitation: (p) => {
        citations.value.push(p)
        // 引用已到达 = 检索完成，骨架推进到"生成分析"阶段
        if (skelPhase.value < 1) skelPhase.value = 1
      },
      onResult: (p) => {
        const st = p.structured || null
        if (!st) { result.value = null; return }
        const out = { ...st }
        // 纯文本回答流：answer 已由上方打字机区展示，剔除避免正文重复 2 次
        if (
          !streamIsJson.value &&
          typeof out.answer === 'string' &&
          out.answer &&
          streamText.value.trim()
        ) {
          delete out.answer
        }
        // 引用已由 CitationCard 编号列表展示（SSE citation 事件），剔除避免列表+表格两层重复
        if (citations.value.length && Array.isArray(out.citations)) {
          delete out.citations
        }
        result.value = Object.keys(out).length ? out : null
      },
      onDone: () => { streaming.value = false; stopSkeleton() },
      onError: (p) => {
        // 结构化流出错时原文未展示，错误信息直接渲染到文本区
        if (streamIsJson.value) streamText.value = `[错误] ${p.message || JSON.stringify(p)}`
        else streamText.value += `\n[错误] ${p.message || JSON.stringify(p)}`
        streaming.value = false
        stopSkeleton()
      },
    })
  } catch (e) {
    streamText.value += `\n[调用失败] ${e.message || e}`
    streaming.value = false
    stopSkeleton()
  }
}

function reset() {
  streamText.value = ''
  citations.value = []
  result.value = null
  streamIsJson.value = false
  skelPhase.value = 0
}

watch(code, loadManifest)
onMounted(loadManifest)
onUnmounted(stopSkeleton)
</script>

<style scoped>
.head h1 { margin: 0 0 4px; font-size: 22px; }
.summary { color: var(--text-secondary); margin: 0 0 16px; }
.suggest { display: flex; flex-wrap: wrap; gap: 8px; }
.suggest button { font-size: 13px; }
.sample-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 0 16px;
  padding: 10px 14px;
  background: rgba(210, 150, 40, 0.08);
  border: 1px solid rgba(210, 150, 40, 0.35);
  border-radius: 8px;
  color: var(--accent-warn);
  font-size: 13px;
}
.sample-tag {
  flex: none;
  padding: 1px 8px;
  font-size: 11px;
  font-weight: 600;
  color: var(--accent-warn);
  background: rgba(210, 150, 40, 0.15);
  border-radius: 10px;
}
.workbench { display: grid; grid-template-columns: 1fr 1.4fr; gap: 16px; }
.input-panel .actions { display: flex; gap: 8px; margin-top: 12px; }
.output-panel { min-height: 240px; }
.skeleton-card { padding: 8px 0; }
.skel-row { display: flex; align-items: center; gap: 10px; padding: 10px 4px; color: var(--text-secondary); font-size: 14px; }
.skel-row.done { color: var(--text-secondary); opacity: 0.55; }
.skel-row.active { color: var(--text-primary); font-weight: 500; }
.skel-dot {
  width: 8px; height: 8px; border-radius: 50%; flex: none;
  background: var(--border);
}
.skel-row.active .skel-dot {
  background: var(--accent);
  animation: skel-pulse 1.1s ease-in-out infinite;
  box-shadow: 0 0 0 4px rgba(64, 120, 200, 0.12);
}
.skel-row.done .skel-dot { background: var(--accent); opacity: 0.5; }
@keyframes skel-pulse {
  0%, 100% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.35); opacity: 0.55; }
}
@media (max-width: 1199px) { .workbench { grid-template-columns: 1fr; } }
</style>
