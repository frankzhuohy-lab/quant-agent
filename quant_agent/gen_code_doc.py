#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成代码完整文档 docx（constitution_006）。"""
import os
import sys

from docx import Document
from docx.shared import Pt

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def lines_of(fp):
    try:
        with open(os.path.join(BASE, fp), encoding="utf-8", errors="ignore") as f:
            return str(sum(1 for _ in f))
    except OSError:
        return "-"


MODS = [
 ("quant_agent/__init__.py", "包入口：路径常量（CONFIG/VAR/UCACHE 等）、宪法版本与封存边界常量"),
 ("quant_agent/config/constitution.json", "宪法 constitution_006：窗口划分、执行规则（费用/涨停复核/现金约束）、研究模式门禁、Critic 降级"),
 ("quant_agent/config/universe_rule.json", "选股宇宙规则：基本面与流动性门槛、执行时点声明（point-in-time）"),
 ("quant_agent/seed.py", "规格规范化与物化：normalize（白名单补丁字段）到 materialize（策略函数注入、宇宙过滤）"),
 ("quant_agent/strategies/adapter.py", "策略适配层：意图层策略语义、特征预计算（Keltner/均线/ATR）、涨跌停事前过滤（带宽表）"),
 ("qsys/qengine.py", "冻结的回测引擎 run_r443：权重口径意图订单生成器（2026-10-01 修复末段双计 d+1<xd）"),
 ("qsys/qdata.py", "行情缓存：proxy.finance.qq.com 拉取、原子写（tmp+os.replace）"),
 ("quant_agent/backtest/runner.py", "运行编排：BacktestRunner、run_window 封存硬约束、metrics 汇总、产物落盘与哈希"),
 ("quant_agent/backtest/executor.py", "现金执行层：整手成交、现金上限 sizing（双上界取小+回验）、_fee=max(比例,最低5元)、成交时点涨停复核（可独立开关）、卖单未成交不释放资金、敞口漂移记录"),
 ("quant_agent/backtest/streaming_ref.py", "逐日流式参考引擎：严格时序推进、不索引未来元素的独立第二实现（S8d 对拍基准）"),
 ("quant_agent/validation/evaluate.py", "评估：bootstrap 夏普检验、回撤/费用/敞口门槛、WF 折、成本建模（买0.20 per mille/卖0.25 per mille）"),
 ("quant_agent/validation/sealed_eval.py", "封存评估唯一显式放行点（_allow_sealed）"),
 ("quant_agent/validation/sealed_audit.py", "封存审计：WF 折逐日越界判定、B2 决策影响表、双封存界（V1=2026-01-01 / 现行=2026-09-01）"),
 ("quant_agent/gate/rules.py", "Gate：bootstrap P(<=0)<0.05、P(DEV提升)>=0.80、门槛相对基线收紧 0/5pp；证据完整违反门槛=REJECT，证据缺失=NEEDS_MORE_TESTING"),
 ("quant_agent/experiments/store.py", "实验库：experiments/gate_decisions/cost_breakdown/champion_events/corrections 五表；corrections 修正事件可追溯"),
 ("quant_agent/data/snapshot.py", "数据快照：manifest 钉文件（含冻结副本）、check_no_future_bars、build_combined_manifest"),
 ("quant_agent/data/drift_monitor.py", "行情修订巡检：冻结=独立拷贝+清单自哈希；先自检冻结侧完整性再比对源，写 revision_log"),
 ("quant_agent/data/universe.py", "宇宙构建：多源交集候选、189 只面板、22 只池映射"),
 ("quant_agent/orchestrator/loop.py", "主循环：generate 到 run 到 gate 到 update_champion 守护进程（--health）"),
 ("quant_agent/run_research.py", "单实验入口：提案文件到实验到 Gate 到 champion 指针"),
 ("quant_agent/forward/daily_forward.py", "前向跟踪：champion 默认、sealed_access_log、提案补丁白名单过滤"),
 ("quant_agent/audit/structural.py", "审计：SEALED/降级标签查询"),
 ("quant_agent/batch2.py", "批次二：min_fee 补丁规范化+存档"),
 ("quant_agent/batch3.py", "批次三：代码修复版全量重放+指标分歧报告（284 笔台账）"),
 ("quant_agent/batch4.py", "批次四：可执行现金基线+EXP_0003 全约束重跑"),
 ("quant_agent/batch5.py", "批次五：旧快照 integrity_uncertain + 组合快照 v5 + baseline_004 + EXP_0003 REJECT"),
 ("quant_agent/batch6.py", "批次六：引擎勘误后重建 baseline_005 + 消融登记"),
 ("quant_agent/batch6b.py", "批次六消融重跑：2x2 独立开关对照（run_window 全量产物）"),
 ("quant_agent/tests/test_timing.py", "时序验证（边界窗口、索引映射、空持仓、极端收益）"),
 ("quant_agent/tests/test_execution.py", "执行层验证（费用、整手、现金上限、跳过登记）"),
 ("quant_agent/tests/test_recovery.py", "恢复/持久化验证"),
 ("quant_agent/tests/test_e5_execution.py", "E5 现金执行（费用+约束+台账）"),
 ("quant_agent/tests/test_e6_cash_linkage.py", "E6 现金-持仓联动 + E6-5 最低收费边界（五子测试）"),
 ("quant_agent/tests/test_snapshot_immutability.py", "S7 快照不可变（源文件四种更新冻结哈希不变+篡改检出）"),
 ("quant_agent/tests/test_s8_lookahead.py", "S8 前视因果性（截断一致/界后行情扰动/触发日移动）"),
 ("quant_agent/tests/test_s8_streaming_diff.py", "S8d/S8e 多截断点对拍 + 延迟成交场景固定期限起算"),
 ("quant_agent/tests/test_s9_sealed_guard.py", "S9 封存硬约束（正常路径拒跑/显式放行不被拦截）"),
]

TESTS = [
 ("timing/execution/recovery/E5", "引擎时序与执行层基础验证", "全绿"),
 ("E6 + E6-5", "现金-持仓联动；最低收费边界（1004元买100股被挡/1050成交）", "全绿"),
 ("S7", "快照不可变：覆盖/追加/删建/替换四种源更新，冻结哈希不变；冻结侧篡改可检出", "全绿"),
 ("S8", "前视因果性：截断界内28笔逐字段一致；界后行情扰动，界前订单不动；触发日移动解析一致", "全绿"),
 ("S8d/S8e", "多截断点(T=30/40/50/59)预扫描 vs 独立逐日流式：交易/日收益/敞口/现金/持仓批次(含未平仓)/费用全等；延迟成交下固定期限自成交日起算", "全绿（81-110笔，maxdiff 2.8e-19）"),
 ("S9", "封存拒跑（正常路径抛错）+ 显式放行路径不被拦截、产物盖戳", "全绿"),
]

KNOWN = [
 "费用模型为「已加入最低佣金的费用模型」：买 0.20‰/卖 0.25‰ + 最低 5 元/笔；卖出税费历史变化、收费舍入、部分成交计费粒度未建模。",
 "涨停复核为「开盘价对前收、板块带宽」近似；除权日、风险状态、特殊上市阶段未处理；样本中执行层复核零触发（意图层同规则事前过滤，复核为防御性重检）。",
 "封存保护为程序约定而非权限隔离：研究进程可直接调用引擎或读取缓存绕过 run_window（已实测演示）；冻结可靠性靠独立拷贝+清单哈希检出（检测），项目空间卷为 exFAT，文件权限位不生效。",
 "当前为现金约束下的简化成交模拟：开盘撮合、整手、无滑点、无部分成交；未证明真实执行能力。",
 "历史实验绝对收益水平因引擎末段双计修复全部重述（baseline_005 重建 champion）；同一引擎版本内候选与基线的相对比较方向仍有效。",
 "候选池为有限候选超集的历史可交易股票池，存在候选名单选择偏差；基线为当前研究比较基线，champion 不等于部署资格。",
]


def build():
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = "PingFang SC"
    st.font.size = Pt(10)

    doc.add_heading("quant_agent 系统代码完整文档（constitution_006 / 2026-10-01）", 0)
    doc.add_paragraph(
        "研究自动化系统：AI 提假设 + 确定性代码回测判定。收益数字全部来自实际运行；"
        "交易规则/指标/Gate 为确定性组件，LLM 无否决豁免权；point-in-time 数据血缘；"
        "封存隔离；失败实验全留存。")

    doc.add_heading("一、架构与数据流", 1)
    doc.add_paragraph(
        "提案(proposal JSON) → seed.normalize（补丁字段白名单）→ seed.materialize（策略函数注入）→ "
        "qengine.run_r443（冻结引擎，权重口径意图订单）→ executor.execute_cash（现金约束/费用/涨停复核，"
        "确定性成交模拟）→ evaluate（bootstrap+门槛）→ gate.rules（REJECT/NEEDS_MORE_TESTING）→ "
        "store（五表 + champion 指针 + corrections）。")
    doc.add_paragraph(
        "封存控制面：runner.run_window 拒绝触及 2026-09-01 起窗口（S9）；唯一显式放行点为 sealed_eval "
        "内部注入 _allow_sealed（git 可追溯）。前向 daily_forward 默认跟踪 champion，"
        "每次运行写 sealed_access_log。")

    doc.add_heading("二、模块清单（完整代码见随附 zip，共 136 个文件）", 1)
    t = doc.add_table(rows=1, cols=3)
    t.style = "Light Grid Accent 1"
    for i, h in enumerate(("模块", "行数", "职责")):
        t.rows[0].cells[i].text = h
    for path, desc in MODS:
        r = t.add_row().cells
        r[0].text = path
        r[1].text = lines_of(path)
        r[2].text = desc

    doc.add_heading("三、测试清单（九项全绿，2026-10-01）", 1)
    t = doc.add_table(rows=1, cols=3)
    t.style = "Light Grid Accent 1"
    for i, h in enumerate(("测试", "验收内容", "结果")):
        t.rows[0].cells[i].text = h
    for a, b, c in TESTS:
        r = t.add_row().cells
        r[0].text, r[1].text, r[2].text = a, b, c

    doc.add_heading("四、运行方式", 1)
    for cmd in [
        "python3 -m quant_agent.orchestrator.loop --health      # 系统自检",
        "python3 -m quant_agent.run_research --proposal <文件>  # 单实验+Gate",
        "python3 -m quant_agent.batch6b                          # 消融重跑",
        "python3 -m quant_agent.data.drift_monitor --freeze     # 冻结缓存；不带参数=巡检",
        "python3 -m quant_agent.forward.daily_forward           # 前向跟踪（champion）",
        "python3 -m quant_agent.tests.test_s8_streaming_diff    # S8d 对拍",
    ]:
        doc.add_paragraph(cmd, style="List Bullet")

    doc.add_heading("五、constitution_006 窗口", 1)
    t = doc.add_table(rows=1, cols=2)
    t.style = "Light Grid Accent 1"
    t.rows[0].cells[0].text = "区间"
    t.rows[0].cells[1].text = "身份"
    for a, b in [
        ("2025-12-31 及以前", "IS 样本内"),
        ("2025-07 ~ 2025-12", "DEV 开发"),
        ("2026-01-01 ~ 2026-08-31", "降级 dev（WF 第 6-9 折越入，结果进过 Gate 决策）"),
        ("2026-09-01 起", "新封存（S9 硬拒跑 + 访问日志）"),
    ]:
        r = t.add_row().cells
        r[0].text, r[1].text = a, b

    doc.add_heading("六、已知边界（诚实声明）", 1)
    for s in KNOWN:
        doc.add_paragraph(s, style="List Bullet")

    out = os.path.join(BASE, "quant_agent/var/docs/quant_agent_代码完整文档_v6_20261001.docx")
    doc.save(out)
    print("OK", out)


if __name__ == "__main__":
    build()
