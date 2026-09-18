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
          <StreamText :text="streamText" :streaming="streaming" />
          <CitationCard v-if="citations.length" :items="citations" />
          <SchemaOutput v-if="result" :schema="manifest.output_schema" :data="result" />
          <DeployCard v-if="manifest.deployment_requirements" :req="manifest.deployment_requirements" />
        </section>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
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
  streaming.value = true
  try {
    await invokeModule(code.value, inputs.value, {
      onChunk: (p) => { streamText.value += p.text || '' },
      onCitation: (p) => citations.value.push(p),
      onResult: (p) => { result.value = p.structured || null },
      onDone: () => { streaming.value = false },
      onError: (p) => { streamText.value += `\n[错误] ${p.message || JSON.stringify(p)}`; streaming.value = false },
    })
  } catch (e) {
    streamText.value += `\n[调用失败] ${e.message || e}`
    streaming.value = false
  }
}

function reset() {
  streamText.value = ''
  citations.value = []
  result.value = null
}

watch(code, loadManifest)
onMounted(loadManifest)
</script>

<style scoped>
.head h1 { margin: 0 0 4px; font-size: 22px; }
.summary { color: var(--text-secondary); margin: 0 0 16px; }
.suggest { display: flex; flex-wrap: wrap; gap: 8px; }
.suggest button { font-size: 13px; }
.workbench { display: grid; grid-template-columns: 1fr 1.4fr; gap: 16px; }
.input-panel .actions { display: flex; gap: 8px; margin-top: 12px; }
.output-panel { min-height: 240px; }
@media (max-width: 1199px) { .workbench { grid-template-columns: 1fr; } }
</style>
