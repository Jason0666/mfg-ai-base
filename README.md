# mfg-ai-base · 制造业 AI 应用底座

> 用「能点开跑的工程底座」替代「简历 + 经验口述」。客户拿到能直接跑，改配置接库后即可上线。

---

## 5 分钟跑起来

### 前提

- Python 3.9+
- Node.js 18+（推荐 22）
- 任意 OpenAI 兼容 LLM API Key（如 DeepSeek）——**可选**；不配置 Key 时系统进入剧本降级模式，6 个模块的推荐问题仍可完整演示

### 一键启动

```bash
# 后端
cd backend
cp .env.example .env  # 按需编辑 .env：填入 LLM_API_KEY（可留空）、生产环境生成强 SECRET_KEY
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 前端（另开终端）
cd frontend
cp .env.example .env  # 本地开发通常无需修改，留空即走 vite 代理
npm install
npm run dev
```

浏览器打开 http://localhost:5173 ，首页出现 6 个模块卡片即成功。

### Docker 一键

```bash
cd deploy
cp .env.example .env  # 编辑 .env 填入 LLM_API_KEY
docker compose up -d
```

访问 http://localhost:5173 。

### 账号说明

系统首启自动创建三个内置角色账号：**admin**（管理员，全部功能）、**analyst**（分析员）、**viewer**（一线查看，按模块授权）。

- **生产模式**：初始密码在后端首次启动日志中生成并仅显示一次，请从启动日志获取后立即登录修改；生产部署必须配置强随机 `SECRET_KEY`（生成命令：`python -c "import secrets;print(secrets.token_hex(32))"`，见 [交付指南](docs/交付指南.md)）。
- **DEMO 模式（含本地开发）**：同样需要登录或持**访客时效链接**访问（不再匿名放行）。本地种子账号见后端启动日志，可用 admin 登录进入管理页；对外演示可在管理页生成限时/限次访客链接，访客无需账号。

---

## 6 个已上线模块

| 模块 | 场景 | 类型 |
|------|------|------|
| 工艺 SOP 智能问答 | 口语化提问精准定位 SOP 条款 | 知识 |
| 设备故障智能诊断 | 输入现象输出诊断结论+历史工单 | 知识 |
| 招标文件智能审核 | 上传标书自动定位废标风险 | 文档 |
| 生产数据问答 | 自然语言问数→SQL→图表+归因 | 数据 |
| 质量异常分析与8D报告 | 5Why+鱼骨图+一键导出 Word/PDF | 文档 |
| 能耗异常分析（示例） | 能耗异常时段诊断与节能建议（示例模块，模拟数据） | 数据 |

---

## 演示 vs 生产模式

| 行为 | DEMO_MODE=true | DEMO_MODE=false |
|------|----------------|-----------------|
| 鉴权 | 登录或访客时效链接（两种模式统一门禁） | 必须 Bearer Token 或访客链接 |
| 种子数据 | 自动初始化样例数据 | 不自动初始化 |
| 无 Key 时 | 推荐问题走内置剧本，完整可演示（响应标记"模拟数据"） | 同样降级，保证演示不中断 |
| 审计 | 记录（访客操作记录到令牌） | 记录（带 user_id） |
| 限流 | 生效（访客另有限频/限次） | 生效 |

切换：`.env` 改 `DEMO_MODE=false`，重启后端。

---

## 访客时效链接（对外演示）

在 `/admin` 管理页「访客链接」面板生成，可选有效期 **1 / 6 / 24 小时**与调用次数上限（默认 30 次，另限 5 次/分钟）。访客打开链接即可使用，无需账号：

- 仅能访问模块市场/详情页与模块调用、文件下载；管理接口返回 403；
- 到期、吊销、超次后访问自动跳到结束页，不透露任何页面内容；
- 可勾选「允许真实 LLM」并设置真实调用配额，超出后自动降级为剧本模式。

## 剧本降级模式（无 Key 也能演示）

未配置 `LLM_API_KEY`（或 Key 失效/限流）时，6 个模块的推荐问题由内置剧本（`seed/demo_scripts.json`）返回确定性的样例答案，流式输出、引用卡片、导出 Word/PDF 全链路可用；UI 明确标记"模拟数据"。访客超出真实 LLM 配额时也会自动切入该模式。

---

## 行业包

通过 `INDUSTRY_PACKAGE` 环境变量控制加载哪些模块：

```bash
# 全部模块（默认）
INDUSTRY_PACKAGE=

# 仅制造业包
INDUSTRY_PACKAGE=manufacturing

# 仅政务包（需添加 industry: government 的模块）
INDUSTRY_PACKAGE=government
```

模块在 `manifest.yaml` 中声明 `industry: manufacturing` 标签；留空表示通用，所有行业包都加载。

---

## 加新模块（30 分钟）

详见 [模块开发规范](docs/模块开发规范.md) ，最小三件套即可跑通：

```
backend/app/modules/m07_xxx/
├── manifest.yaml   # 清单（驱动注册+前端渲染）
├── logic.py        # 业务逻辑
└── api.py          # 5 行路由骨架
```

重启后端，首页自动出现新卡片。**不改主框架代码**。

---

## 公网部署（静态托管 + 云服务）

前端为 `vite build` 纯静态产物，后端为无状态 FastAPI（SQLite 单文件），推荐分离部署：

1. **后端**：Render Web Service（Docker/`backend/` 目录），环境变量在平台控制台注入 `LLM_API_KEY`、强随机 `SECRET_KEY`、`CORS_ORIGINS`（**禁止 `*`，必须填前端实际域名**）；健康检查 `GET /api/health`（免鉴权）。
2. **前端**：Cloudflare Pages 或腾讯 EdgeOne Pages，构建命令 `npm run build`，构建期设置 `VITE_API_BASE` 指向上一步后端公网地址；LLM Key 只存在于后端，永不下发前端。
3. **保活**：免费实例会休眠冷启动，用 UptimeRobot 每 5 分钟 ping 一次 `/api/health`。
4. **数据**：SQLite 适合演示量级（审计/访客记录数据量极小）；注意部分免费平台文件系统为临时盘，重启会重置，需要持久数据时挂持久磁盘或改用 PostgreSQL。
5. 容器化/私有化部署见 [交付指南](docs/交付指南.md)（`deploy/` 下 docker compose + nginx 配置）。

---

## 关键文档

- [交付指南](docs/交付指南.md) — 部署/账号/权限/审计/导出/排障全流程
- [模块开发规范](docs/模块开发规范.md) — 加新模块标准 6 步
- [施工蓝图](docs/施工蓝图.md) — 完整里程碑与验收标准

---

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | FastAPI + SQLite/PostgreSQL + PyMuPDF |
| 前端 | Vue 3 + Vite + TailwindCSS |
| LLM | OpenAI 兼容协议（DeepSeek 等） |
| RAG | BM25 倒排索引（零向量依赖） |
| 鉴权 | HS256 JWT（stdlib 手写，零依赖） |

---

## 5 分钟上手视频脚本

**0:00-0:30** — 启动：`docker compose up -d`，访问 localhost:5173，首页 6 卡片

**0:30-1:30** — SOP 问答：点击"工艺 SOP 智能问答"→ 点推荐问题"注塑银纹怎么处理"→ 看流式回答 + 5 条引用卡片（含版本管理）

**1:30-2:30** — 设备诊断：点击"设备故障智能诊断"→ 输入"CNC-07 主轴振动"→ 看结构化诊断（原因/步骤/备件/历史工单）

**2:30-3:30** — 数据问答：点击"生产数据问答"→ 问"9月11日不良率为什么突降"→ 看 SQL+表格+趋势图+归因

**3:30-4:30** — 质量报告：点击"质量异常分析"→ 输入气泡问题→ 看 5Why+鱼骨图→ 一键导出 Word

**4:30-5:00** — 审计页：用 admin 账号登录后点"管理"→ 看调用统计/7日趋势/审计日志（谁问了什么、答了什么、引用了什么）；并可在访客链接面板生成限时演示链接
