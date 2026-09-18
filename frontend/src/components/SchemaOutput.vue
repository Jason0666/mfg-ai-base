<template>
  <div class="schema-output">
    <template v-for="(prop, key) in properties" :key="key">
      <div v-if="!isEmpty(data[key])" class="block">
        <div class="block-title">{{ prop.title || key }}</div>
        <!-- chart_spec → ECharts -->
        <ChartBlock v-if="key === 'chart_spec' && isObject(data[key])" :spec="data[key]" />
        <!-- sql → 只读代码块 -->
        <pre v-else-if="key === 'sql'" class="sql-block mono">{{ data[key] }}</pre>
        <!-- export_url / export_url_pdf → 下载按钮 -->
        <a v-else-if="isExportKey(key) && typeof data[key] === 'string' && data[key].startsWith('/api/')"
           class="export-link" :href="data[key]" download>
           {{ exportLabel(key, prop) }}
        </a>
        <!-- array of objects → table -->
        <DataTable v-else-if="prop.type === 'array' && prop.items?.type === 'object' && Array.isArray(data[key])"
                   :rows="data[key]" :titles="colTitles(prop)" />
        <!-- array of strings → ordered list（含"步骤"用有序列表） -->
        <ol v-else-if="prop.type === 'array' && Array.isArray(data[key])" class="step-list">
          <li v-for="(item, i) in data[key]" :key="i" class="mono">{{ typeof item === 'string' ? item : JSON.stringify(item) }}</li>
        </ol>
        <!-- 嵌套 object 且声明了 properties → 递归渲染（鱼骨图/8D 等） -->
        <div v-else-if="isObject(data[key]) && prop.properties" class="nested-object">
          <SchemaOutput :schema="prop" :data="data[key]" />
        </div>
        <!-- object → 折叠 JSON（兜底） -->
        <details v-else-if="isObject(data[key])" class="json-details">
          <summary>查看详情</summary>
          <pre class="json-block mono">{{ JSON.stringify(data[key], null, 2) }}</pre>
        </details>
        <!-- string → plain text -->
        <div v-else class="value">{{ typeof data[key] === 'string' ? data[key] : JSON.stringify(data[key], null, 2) }}</div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import DataTable from './DataTable.vue'
import ChartBlock from './ChartBlock.vue'

const props = defineProps({
  schema: { type: Object, default: () => ({}) },
  data: { type: Object, default: () => ({}) },
})

const properties = computed(() => props.schema?.properties || {})

function isEmpty(v) {
  if (v === undefined || v === null || v === '') return true
  if (Array.isArray(v) && v.length === 0) return true
  if (isObject(v) && Object.keys(v).length === 0) return true
  return false
}

function isObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v)
}

function colTitles(prop) {
  const itemProps = prop?.items?.properties || {}
  const map = {}
  for (const [k, p] of Object.entries(itemProps)) {
    if (p?.title) map[k] = p.title
  }
  return map
}

function isExportKey(key) {
  return key === 'export_url' || key === 'export_url_pdf'
}

function exportLabel(key, prop) {
  if (key === 'export_url_pdf') return prop?.title || '下载报告（PDF .pdf）'
  return prop?.title || '下载报告（Word .docx）'
}
</script>

<style scoped>
.block { margin-top: 12px; }
.block-title { font-size: 13px; color: var(--text-secondary); margin-bottom: 4px; }
.value { font-size: 14px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
.step-list { padding-left: 24px; line-height: 1.8; }
.mono { font-family: var(--font-mono); }
.sql-block {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent-primary, #2f6fed);
  border-radius: 6px;
  padding: 10px 12px;
  font-size: 12.5px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-x: auto;
  margin: 0;
}
.json-details { margin-top: 4px; }
.json-block {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px 12px;
  font-size: 12px;
  max-height: 260px;
  overflow: auto;
}
.nested-object {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 14px 2px;
  margin-top: 4px;
}
.export-link {
  display: inline-block;
  margin-top: 4px;
  padding: 8px 16px;
  background: var(--accent-primary, #2f6fed);
  color: #fff;
  border-radius: 6px;
  font-size: 14px;
  text-decoration: none;
}
.export-link:hover { opacity: 0.9; }
</style>
