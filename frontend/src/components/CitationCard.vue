<template>
  <div v-if="items.length" class="citations card">
    <div class="title">引用来源（{{ items.length }}）</div>
    <div v-for="(c, i) in items" :key="i" class="item">
      <span class="idx">[{{ i + 1 }}]</span>
      <span class="doc mono">{{ c.doc || c.title || c.doc_id || '—' }}</span>
      <span v-if="c.page" class="pg">第 {{ c.page }} 页</span>
      <span v-if="c.clause" class="clause mono">{{ c.clause }}</span>
      <span v-if="c.doc_type" class="tag low">{{ c.doc_type }}</span>
      <span v-if="c.version" class="ver">v{{ c.version }}</span>
    </div>
  </div>
</template>

<script setup>
defineProps({ items: { type: Array, default: () => [] } })
</script>

<style scoped>
.title { font-size: 13px; color: var(--text-secondary); margin-bottom: 8px; }
.item { font-size: 13px; padding: 4px 0; border-bottom: 1px dashed var(--border); display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.idx { color: var(--accent); font-weight: 600; }
.doc { color: var(--text-primary); }
.pg, .ver { color: var(--text-secondary); font-size: 12px; }
.clause { color: var(--accent-warn); font-size: 12px; }
</style>
