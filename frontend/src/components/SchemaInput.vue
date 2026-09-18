<template>
  <div class="schema-input">
    <div v-for="(prop, key) in properties" :key="key" class="field">
      <label>
        <span class="label-text">{{ prop.title || key }}</span>
        <span v-if="isRequired(key)" class="req">*</span>
      </label>
      <!-- 嵌套对象：递归（单层即可，避免复杂度） -->
      <div v-if="prop.type === 'object' && prop.properties" class="nested">
        <SchemaInput :schema="prop" v-model="innerObj[key]" />
      </div>
      <!-- 文件上传 -->
      <div v-else-if="prop.widget === 'file_upload'" class="file-upload">
        <input
          type="file"
          :accept="prop.accept || ''"
          style="display:none"
          :ref="(el) => { if (el) fileInputs[key] = el }"
          @change="onUpload(key, $event)"
        />
        <div class="file-row">
          <button type="button" class="file-btn" @click="pickFile(key)">
            {{ uploading[key] ? '上传中…' : '选择文件上传' }}
          </button>
          <button
            v-if="samplePath"
            type="button"
            class="file-btn sample"
            :disabled="uploading[key]"
            @click="loadSample(key)"
          >
            使用内置样本文件
          </button>
        </div>
        <div v-if="innerObj[key] && fileMeta[key]" class="file-meta">
          <span class="file-ok">已选择：{{ fileMeta[key].filename }}</span>
          <span class="file-size">{{ metaSize(fileMeta[key]) }}</span>
          <span v-if="fileMeta[key].page_count" class="file-size">· {{ fileMeta[key].page_count }} 页</span>
          <a class="file-clear" @click="clearFile(key)">清除</a>
          <div v-if="fileMeta[key].note" class="file-note">{{ fileMeta[key].note }}</div>
        </div>
        <div v-else-if="innerObj[key]" class="file-meta">
          <span class="file-ok">已选择文件：{{ String(innerObj[key]).slice(0, 12) }}…</span>
          <a class="file-clear" @click="clearFile(key)">清除</a>
        </div>
        <div v-if="uploadError[key]" class="file-err">{{ uploadError[key] }}</div>
        <div v-if="!innerObj[key]" class="file-hint">{{ prop.placeholder || '支持 PDF / Word，文件仅用于本次审核' }}</div>
      </div>
      <!-- widget 渲染 -->
      <textarea
        v-else-if="prop.widget === 'textarea'"
        v-model="innerObj[key]"
        :placeholder="prop.placeholder || ''"
        :rows="prop.rows || 3"
      />
      <select
        v-else-if="prop.widget === 'select'"
        v-model="innerObj[key]"
      >
        <option value="">（请选择）</option>
        <option v-for="opt in selectOptions(prop)" :key="opt" :value="opt">{{ opt }}</option>
      </select>
      <input
        v-else-if="prop.widget === 'number' || prop.type === 'number' || prop.type === 'integer'"
        type="number"
        v-model.number="innerObj[key]"
      />
      <input
        v-else-if="prop.widget === 'date'"
        type="date"
        v-model="innerObj[key]"
      />
      <input
        v-else
        type="text"
        v-model="innerObj[key]"
        :placeholder="prop.placeholder || ''"
      />
    </div>
  </div>
</template>

<script setup>
import { computed, reactive } from 'vue'
import { api } from '../api/client'

const props = defineProps({
  schema: { type: Object, default: () => ({}) },
  modelValue: { type: Object, default: () => ({}) },
  moduleCode: { type: String, default: '' },
  // 模块存在内置样本时传入端点路径（如 /sample），空串不显示按钮
  samplePath: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'file-selected'])

const properties = computed(() => props.schema?.properties || {})
const required = computed(() => props.schema?.required || [])

const innerObj = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const fileInputs = reactive({})
const uploading = reactive({})
const uploadError = reactive({})
// file_id -> {filename,size,page_count,note}
const fileMeta = reactive({})

function isRequired(key) {
  return required.value.includes(key)
}

function selectOptions(prop) {
  const opts = prop.options || []
  // 兼容字符串数组与 {label,value} 对象数组
  return opts.map((o) => (typeof o === 'object' && o !== null ? (o.label ?? o.value ?? '') : o))
}

function metaSize(meta) {
  if (!meta?.size && meta?.size !== 0) return ''
  const kb = meta.size / 1024
  return kb > 1024 ? `（${(kb / 1024).toFixed(1)} MB）` : `（${kb.toFixed(0)} KB）`
}

function pickFile(key) {
  fileInputs[key]?.click()
}

async function onUpload(key, ev) {
  const file = ev.target.files?.[0]
  ev.target.value = ''
  if (!file) return
  uploading[key] = true
  uploadError[key] = ''
  try {
    const data = await api.uploadFile(file)
    innerObj.value = { ...innerObj.value, [key]: data.file_id }
    fileMeta[key] = { file_id: data.file_id, filename: data.filename, size: data.size }
    emit('file-selected', { key, file_id: data.file_id, meta: fileMeta[key] })
  } catch (e) {
    uploadError[key] = '文件上传失败：' + (e?.message || e)
  } finally {
    uploading[key] = false
  }
}

async function loadSample(key) {
  if (!props.moduleCode) return
  uploading[key] = true
  uploadError[key] = ''
  try {
    const data = await api.loadSample(props.moduleCode, props.samplePath || '/sample')
    innerObj.value = { ...innerObj.value, [key]: data.file_id }
    fileMeta[key] = {
      file_id: data.file_id,
      filename: data.filename,
      size: data.size,
      page_count: data.page_count,
      note: data.note,
    }
    emit('file-selected', { key, file_id: data.file_id, meta: fileMeta[key] })
  } catch (e) {
    uploadError[key] = '样本载入失败：' + (e?.message || e)
  } finally {
    uploading[key] = false
  }
}

function clearFile(key) {
  delete fileMeta[key]
  innerObj.value = { ...innerObj.value, [key]: '' }
}

defineExpose({ loadSample })
</script>

<style scoped>
.field { margin-bottom: 12px; }
label { display: block; font-size: 13px; color: var(--text-secondary); margin-bottom: 4px; }
.req { color: var(--accent-danger); margin-left: 2px; }
input, textarea, select {
  width: 100%;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text-primary);
  padding: 8px;
  font-family: inherit;
  font-size: 14px;
}
input:focus, textarea:focus, select:focus { outline: none; border-color: var(--accent); }
select option { background: var(--bg-elevated); color: var(--text-primary); }
.nested { padding-left: 12px; border-left: 2px solid var(--border); margin-top: 8px; }

.file-row { display: flex; gap: 8px; flex-wrap: wrap; }
.file-btn {
  padding: 7px 14px;
  font-size: 13px;
  border-radius: 6px;
  border: 1px solid var(--accent);
  background: transparent;
  color: var(--accent);
  cursor: pointer;
}
.file-btn:hover { background: rgba(64, 158, 255, 0.1); }
.file-btn.sample { border-style: dashed; }
.file-btn:disabled { opacity: 0.6; cursor: default; }
.file-meta { margin-top: 6px; font-size: 13px; color: var(--text-primary); }
.file-ok { color: #67c23a; }
.file-size { color: var(--text-secondary); margin-left: 6px; }
.file-clear { margin-left: 10px; color: var(--accent-danger); cursor: pointer; }
.file-note { color: var(--text-secondary); font-size: 12px; margin-top: 2px; }
.file-hint { margin-top: 6px; font-size: 12px; color: var(--text-secondary); }
.file-err { margin-top: 6px; font-size: 12px; color: var(--accent-danger); }
</style>
