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
          <td v-for="col in cols" :key="col">{{ format(row[col], col) }}</td>
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
// 引用对象 → 自然语言：手册"文档名 第N页 条款 v版本"；工单"工单 WO-xxxx"
function refFmt(r) {
  if (!r || typeof r !== 'object') return String(r ?? '')
  if (r.doc) {
    const parts = [r.doc]
    if (r.page) parts.push(`第${r.page}页`)
    if (r.clause) parts.push(String(r.clause))
    if (r.version) parts.push(`v${r.version}`)
    return parts.join(' ')
  }
  if (r.wo_id) return `工单 ${r.wo_id}`
  return Object.values(r).filter(Boolean).join(' ')
}

function format(v, col) {
  if (v === null || v === undefined) return ''
  if (typeof v === 'boolean') return v ? '是' : '否'
  // 概率字段：0.75 → 75%
  if (typeof v === 'number' && /prob/i.test(String(col || '')) && v >= 0 && v <= 1) {
    return `${Math.round(v * 100)}%`
  }
  if (Array.isArray(v)) {
    if (v.length === 0) return ''
    if (v.every((x) => x === null || typeof x !== 'object')) return v.filter((x) => x !== '').join('；')
    return v.map(refFmt).filter(Boolean).join('；')
  }
  if (typeof v === 'object') return Object.values(v).filter(Boolean).join(' ')
  return String(v)
}
</script>

<style scoped>
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { border: 1px solid var(--border); padding: 6px 8px; text-align: left; vertical-align: top; }
th { background: var(--bg-elevated); color: var(--text-secondary); font-weight: 500; }
.mono { font-family: var(--font-mono); word-break: break-word; }
</style>
