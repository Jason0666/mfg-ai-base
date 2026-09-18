<template>
  <div v-if="rows.length" class="data-table">
    <table>
      <thead>
        <tr>
          <th v-for="col in cols" :key="col">{{ colTitle(col) }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, i) in rows" :key="i">
          <td v-for="col in cols" :key="col" class="mono">{{ format(row[col]) }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({
  rows: { type: Array, default: () => [] },
  // 列 key → 中文标题（来自 manifest output_schema 的 items.properties）
  titles: { type: Object, default: () => ({}) },
})
const cols = computed(() => {
  const set = new Set()
  for (const r of props.rows) if (r && typeof r === 'object') for (const k of Object.keys(r)) set.add(k)
  return Array.from(set)
})
function colTitle(col) {
  return props.titles?.[col] || col
}
function format(v) {
  if (v === null || v === undefined) return ''
  if (typeof v === 'boolean') return v ? '是' : '否'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}
</script>

<style scoped>
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { border: 1px solid var(--border); padding: 6px 8px; text-align: left; vertical-align: top; }
th { background: var(--bg-elevated); color: var(--text-secondary); font-weight: 500; }
.mono { font-family: var(--font-mono); word-break: break-word; }
</style>
