# mfg-ai-base · 制造业 AI 应用底座

> 用「能点开跑的工程底座」替代「简历 + 经验口述」。客户拿到能直接跑，改配置接库后即可上线。

---

## 5 分钟跑起来

### 前提

- Python 3.9+
- Node.js 18+（推荐 22）
- 任意 OpenAI 兼容 LLM API Key（如 DeepSeek）

### 一键启动

```bash
# 后端
cd backend
cp .env.example .env  # 编辑 .env 填入 LLM_API_KEY
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 前端（另开终端）
cd frontend
npm install
npm run dev
```

浏览器打开 http://localhost:5173 ，首页出现 5 个模块卡片即成功。

### Docker 一键

```bash
cd deploy
cp .env.example .env  # 编辑 .env 填入 LLM_API_KEY
docker compose up -d
```

访问 http://localhost:5173 。

### 默认账号（生产模式）

| 角色 | 用户名 | 密码 | 权限 |
|------|--------|------|------|
| 管理员 | admin | Admin@123 | 全部模块 + 审计页 |
| 分析员 | analyst | Analyst@123 | 全部模块 |
| 查看 | viewer | Viewer@123 | 被分配的模块 |

> 首次启动自动创建，生产环境务必登录后改密。

---

## 5 个已上线模块

| 模块 | 场景 | 类型 |
|------|------|------|
| 工艺 SOP 智能问答 | 口语化提问精准定位 SOP 条款 | 知识 |
| 设备故障智能诊断 | 输入现象输出诊断结论+历史工单 | 知识 |
| 招标文件智能审核 | 上传标书自动定位废标风险 | 文档 |
| 生产数据问答 | 自然语言问数→SQL→图表+归因 | 数据 |
| 质量异常分析与8D报告 | 5Why+鱼骨图+一键导出 Word/PDF | 文档 |

---

## 演示 vs 生产模式

| 行为 | DEMO_MODE=true | DEMO_MODE=false |
|------|----------------|-----------------|
| 鉴权 | 匿名放行（viewer） | 必须 Bearer Token |
| 种子数据 | 自动初始化样例数据 | 不自动初始化 |
| 审计 | 记录（user_id=null） | 记录（带 user_id） |
| 限流 | 生效 | 生效 |

切换：`.env` 改 `DEMO_MODE=false`，重启后端。

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
backend/app/modules/m06_xxx/
├── manifest.yaml   # 清单（驱动注册+前端渲染）
├── logic.py        # 业务逻辑
└── api.py          # 5 行路由骨架
```

重启后端，首页自动出现新卡片。**不改主框架代码**。

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

**0:00-0:30** — 启动：`docker compose up -d`，访问 localhost:5173，首页 5 卡片

**0:30-1:30** — SOP 问答：点击"工艺 SOP 智能问答"→ 点推荐问题"注塑银纹怎么处理"→ 看流式回答 + 5 条引用卡片（含版本管理）

**1:30-2:30** — 设备诊断：点击"设备故障智能诊断"→ 输入"CNC-07 主轴振动"→ 看结构化诊断（原因/步骤/备件/历史工单）

**2:30-3:30** — 数据问答：点击"生产数据问答"→ 问"9月11日不良率为什么突降"→ 看 SQL+表格+趋势图+归因

**3:30-4:30** — 质量报告：点击"质量异常分析"→ 输入气泡问题→ 看 5Why+鱼骨图→ 一键导出 Word

**4:30-5:00** — 审计页：点"管理"→ 看调用统计/7日趋势/审计日志（谁问了什么、答了什么、引用了什么）
