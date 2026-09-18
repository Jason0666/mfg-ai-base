<template>
  <div class="stream-text">
    <div class="text">{{ text }}<span v-if="streaming" class="cursor">▍</span></div>
  </div>
</template>

<script setup>
defineProps({
  text: { type: String, default: '' },
  streaming: { type: Boolean, default: false },
})
</script>

<style scoped>
.text {
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  min-height: 1.5em;
}
.cursor {
  color: var(--accent);
  animation: blink 1s steps(2) infinite;
}
@keyframes blink { 0%, 50% { opacity: 1; } 50.01%, 100% { opacity: 0; } }
</style>
