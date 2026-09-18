# 用户输入

## 故障现象

{symptom}

## 设备编号

{equipment_code}

## 补充信息

- 近期变更：{recent_change}
- 累计运行小时：{running_hours}

## 检索到的手册与工单片段（已按 BM25 检索 + 设备过滤 + 重排）

{chunks}

## 任务

基于上述片段输出结构化诊断结论。严格遵守系统提示词的规则：
- 只用片段中的信息，每条 cause 带 source_refs
- 手册引用带 doc/version/page/clause；工单引用带 wo_id
- 输出严格 JSON，不要 markdown 代码块，不要前后文字
- 检索片段不足以支撑结论时，返回空结果并附 note 说明
