<template>
  <div v-if="validItems.length" class="citations card">
    <div class="title">引用来源（{{ validItems.length }}）</div>
    <div v-for="(c, i) in validItems" :key="i" class="item">
      <span class="idx">[{{ i + 1 }}]</span>
      <span class="doc mono">{{ c.doc || c.title || c.wo_id || c.doc_id || '—' }}</span>
      <span v-if="c.wo_id && !c.doc" class="tag wo">工单</span>
      <span v-if="c.page" class="pg">第 {{ c.page }} 页</span>
      <span v-if="c.clause" class="clause mono">{{ c.clause }}</span>
      <span v-if="c.doc_type" class="tag low">{{ c.doc_type }}</span>
      <span v-if="c.version" class="ver">v{{ c.version }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({ items: { type: Array, default: () => [] } })
// 聚合层兜底：所有标识字段均为空的引用项不渲染
const validItems = computed(() =>
  (props.items || []).filter(
    (c) => c && (c.doc || c.title || c.wo_id || c.doc_id)
  )
)
</script>

<style scoped>
.title { font-size: 13px; color: var(--text-secondary); margin-bottom: 8px; }
.item { font-size: 13px; padding: 4px 0; border-bottom: 1px dashed var(--border); display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.idx { color: var(--accent); font-weight: 600; }
.doc { color: var(--text-primary); }
.pg, .ver { color: var(--text-secondary); font-size: 12px; }
.clause { color: var(--accent-warn); font-size: 12px; }
.tag { font-size: 11px; padding: 1px 7px; border-radius: 3px; line-height: 1.6; }
.tag.wo { background: rgba(64, 120, 200, 0.12); color: var(--accent); }
.tag.low { background: var(--bg-elevated); color: var(--text-secondary); }
</style>
