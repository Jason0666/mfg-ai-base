<template>
  <div ref="el" class="chart-block"></div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({ spec: { type: Object, required: true } })
const el = ref(null)
let chart = null

onMounted(() => {
  chart = echarts.init(el.value)
  render()
})

watch(() => props.spec, render, { deep: true })

function render() {
  if (chart && props.spec) {
    chart.setOption(props.spec)
    chart.resize()
  }
}
</script>

<style scoped>
.chart-block { width: 100%; height: 280px; background: var(--bg-elevated); border-radius: 8px; }
</style>
