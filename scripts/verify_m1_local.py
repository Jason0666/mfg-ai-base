"""M1 DoD 本地自检脚本（不依赖 FastAPI/pydantic-settings）。

直接验证：
1. _template/manifest.yaml 能被 Pydantic 校验通过
2. registry.scan() 能发现 _template 模块
3. 演示剧本文件能被加载

运行：python scripts/verify_m1_local.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 让 backend/ 成为可导入根
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.registry import ModuleManifest, ModuleRegistry

MODULES_DIR = ROOT / "backend" / "app" / "modules"


def main() -> int:
    print("=" * 60)
    print("M1 DoD Local Self-Check")
    print("=" * 60)

    failures: list[str] = []

    # 1. registry 扫描
    reg = ModuleRegistry()
    reg.scan(MODULES_DIR)
    modules = reg.get_all()
    print(f"[1] scanned modules: {[m.code for m in modules]}")
    if not modules:
        failures.append("no modules loaded")
    if not any(m.code == "_template" for m in modules):
        failures.append("_template not in registry (DoD requires it)")

    # 2. _template manifest 校验
    tmpl = reg.get_by_code("_template")
    if tmpl is None:
        failures.append("_template manifest missing")
    else:
        print(f"[2] _template loaded: name='{tmpl.name}' category={tmpl.category} order={tmpl.order}")
        if not tmpl.input_schema:
            failures.append("_template input_schema empty")
        if not tmpl.output_schema:
            failures.append("_template output_schema empty")
        if not tmpl.demo or not tmpl.demo.suggested_questions:
            failures.append("_template demo.suggested_questions empty")
        if not tmpl.deployment_requirements:
            failures.append("_template deployment_requirements empty")
        else:
            print(
                f"[3] deploy req: data={len(tmpl.deployment_requirements.data_needed)} "
                f"timeline='{tmpl.deployment_requirements.timeline}' "
                f"accept={len(tmpl.deployment_requirements.acceptance_criteria)}"
            )

    # 3. 演示剧本文件
    script_path = MODULES_DIR / "_template" / "seed" / "demo_scripts.json"
    if not script_path.exists():
        failures.append(f"demo_scripts.json missing: {script_path}")
    else:
        scripts = json.loads(script_path.read_text(encoding="utf-8"))
        print(f"[4] demo scripts: {len(scripts)} entries")
        if len(scripts) < 1:
            failures.append("demo scripts empty")
        for s in scripts:
            if "keywords" not in s or "answer" not in s:
                failures.append(f"demo script missing fields: {list(s.keys())}")

    # 4. 模块路由可导入性（不强依赖 fastapi，仅检查文件存在）
    api_path = MODULES_DIR / "_template" / "api.py"
    logic_path = MODULES_DIR / "_template" / "logic.py"
    if not api_path.exists():
        failures.append(f"api.py missing: {api_path}")
    if not logic_path.exists():
        failures.append(f"logic.py missing: {logic_path}")
    print(f"[5] api.py={'OK' if api_path.exists() else 'MISSING'} logic.py={'OK' if logic_path.exists() else 'MISSING'}")

    # 总结
    print("=" * 60)
    if failures:
        print(f"FAIL ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: all M1 self-checks passed")
    print()
    print("DoD 提示：")
    print("  1. 本脚本只验证 manifest + registry 逻辑链路")
    print("  2. 完整 DoD 还需 docker compose up 后访问 http://localhost:5173")
    print("     看到 _template 模块卡片，点击进入 /m/_template 完成空转调用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
