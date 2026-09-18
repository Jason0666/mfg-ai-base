<template>
  <div class="container">
    <div class="card">
      <h1>落地路径与部署方式</h1>
      <p class="sub">本底座提供 3 种部署形态，同一套代码，按配置切换：</p>

      <div class="modes">
        <div class="mode">
          <h3>① 样板间（DEMO_MODE=true）</h3>
          <p>公网可访问，给潜在客户点击体验。使用种子数据，预设剧本回答，限流保护 LLM Key。</p>
          <pre class="mono">docker compose up -d</pre>
        </div>
        <div class="mode">
          <h3>② 私有化（DEMO_MODE=false）</h3>
          <p>客户现场部署，数据不出厂。接客户数据库 + 改 .env 即可。</p>
          <pre class="mono">docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d</pre>
        </div>
        <div class="mode">
          <h3>③ 离线部署</h3>
          <p>客户内网无公网出口场景。导出镜像 tar 包，docker load 后启动。</p>
          <pre class="mono">bash scripts/package_offline.sh</pre>
        </div>
      </div>

      <h2>技术架构</h2>
      <pre class="arch mono">
┌─ 前端 Vue 3 + Vite ─────────────────────────┐
│   模块市场 / 通用工作台（schema 驱动）        │
└────────────────↑ REST + SSE─────────────────┘
┌─ 后端 FastAPI ─────────────────────────────┐
│   注册中心 → 业务模块（可插拔）              │
│   公共能力 core/（RAG / LLM / 模板 / NL2SQL）│
│   数据接入层 adapters/（生产改造唯一落点）    │
└────────────────────────────────────────────┘
┌─ 部署 Docker Compose ──────────────────────┐
│   PostgreSQL 16 + pgvector · 不用 K8s       │
└────────────────────────────────────────────┘</pre>

      <h2>验收标准模板</h2>
      <ul>
        <li>首页模块市场展示全部已上线模块</li>
        <li>5 个模块均可独立跑通完整链路</li>
        <li>所有 AI 输出均带可点击的引用来源</li>
        <li>知识库无相关内容时明确提示，零编造</li>
        <li>NL2SQL 拒绝任何非 SELECT 语句</li>
        <li>模型不可用时系统降级不崩溃</li>
      </ul>
    </div>
  </div>
</template>

<script setup></script>

<style scoped>
.sub { color: var(--text-secondary); }
.modes { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin: 16px 0; }
.mode { background: var(--bg-elevated); padding: 12px; border-radius: 8px; border: 1px solid var(--border); }
.mode h3 { margin: 0 0 8px; color: var(--accent); font-size: 15px; }
.mode p { font-size: 13px; color: var(--text-secondary); }
.mode pre, .arch { background: var(--bg-base); padding: 8px; border-radius: 4px; font-size: 12px; overflow-x: auto; }
.arch { line-height: 1.4; padding: 12px; }
ul { line-height: 1.8; }
</style>
