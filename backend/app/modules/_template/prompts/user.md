# 用户提示词模板（Jinja2）

用户问题：{{ question }}

{% if context.recent_change %}
近期变更：{{ context.recent_change }}
{% endif %}

{% if context.running_hours %}
累计运行小时：{{ context.running_hours }}
{% endif %}

参考资料：
{% for chunk in retrieved_chunks %}
---
[{{ loop.index }}] 来源：{{ chunk.doc_title }} 第{{ chunk.page }}页 第{{ chunk.clause }}条
内容：{{ chunk.content }}
{% endfor %}

请按 output_schema 输出结构化结果，并标注引用。
