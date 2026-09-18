<template>
  <div v-if="req" class="deploy card">
    <div class="title">★ 落地条件</div>
    <div class="row">
      <div class="k">数据需求</div>
      <div class="v">
        <div v-for="(d, i) in req.data_needed || []" :key="i">· {{ d }}</div>
      </div>
    </div>
    <div class="row">
      <div class="k">集成方式</div>
      <div class="v">{{ req.integration || '—' }}</div>
    </div>
    <div class="row">
      <div class="k">上线周期</div>
      <div class="v">{{ req.timeline || '—' }}</div>
    </div>
    <div class="row">
      <div class="k">验收标准</div>
      <div class="v">
        <div v-for="(c, i) in req.acceptance_criteria || []" :key="i">· {{ c }}</div>
      </div>
    </div>
    <div v-if="req.remarks" class="row remarks">
      <div class="k">备注</div>
      <div class="v">{{ req.remarks }}</div>
    </div>
  </div>
</template>

<script setup>
defineProps({ req: { type: Object, default: () => null } })
</script>

<style scoped>
.title { font-size: 15px; font-weight: 600; color: var(--accent); margin-bottom: 8px; }
.row { display: grid; grid-template-columns: 100px 1fr; gap: 12px; padding: 6px 0; border-bottom: 1px solid var(--border); }
.row:last-child { border-bottom: none; }
.k { color: var(--text-secondary); font-size: 13px; }
.v { font-size: 13px; color: var(--text-primary); }
.remarks .v { color: var(--accent-warn); }
</style>
