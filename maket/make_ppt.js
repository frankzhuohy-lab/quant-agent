/* 石药创新专属做T · 三年回测报告 PPT */
const pptxgen = require("pptxgenjs");
const p = new pptxgen();
p.layout = "LAYOUT_WIDE";           // 13.33 × 7.5
p.author = "maket 系统";
p.title = "石药创新(300765) 专属做T · 三年回测报告";

const W = 13.33, H = 7.5, M = 0.55;
// 金融深色主题: 深墨蓝底 / 青碧主色 / 金橙强调 / 红绿为数据语义色
const BG = "0E1A26", BG2 = "13222F", CARD = "182B3A";
const PRIM = "2AA79B", PRIM_D = "1E7A72";
const ACC = "E8B84B";              // 金橙 = 关键数字
const UP = "E05C5C", DN = "3FA46A"; // A股语义: 红涨绿跌
const TXT = "EAF2F6", MUT = "8FA6B4", FAINT = "5C7180";
const F = "PingFang SC";
const src = (s) => ({ x: M, y: H - 0.42, w: W - 2 * M, h: 0.3, fontSize: 10.5, fontFace: F, color: FAINT, margin: 0 });

// 通用内容页头
function head(s, k, t, sub) {
  s.background = { color: BG };
  s.addText(k, { x: M, y: 0.42, w: 2.0, h: 0.4, fontSize: 15, fontFace: F, color: PRIM, bold: true, charSpacing: 2, margin: 0 });
  s.addText(t, { x: M, y: 0.74, w: W - 2 * M, h: 0.62, fontSize: 30, fontFace: F, color: TXT, bold: true, margin: 0 });
  if (sub) s.addText(sub, { x: M, y: 1.36, w: W - 2 * M, h: 0.36, fontSize: 14, fontFace: F, color: MUT, margin: 0 });
}

/* ── S1 封面 ── */
let s = p.addSlide();
s.background = { color: BG };
// 网格价位示意（右侧视觉焦点）
const midY = 2.9, loY = 4.35, hiY = 1.45;
s.addShape(p.shapes.LINE, { x: 8.1, y: midY, w: 4.35, h: 0, line: { color: FAINT, width: 1, dashType: "dash" } });
s.addShape(p.shapes.LINE, { x: 8.1, y: loY, w: 4.35, h: 0, line: { color: DN, width: 2 } });
s.addShape(p.shapes.LINE, { x: 8.1, y: hiY, w: 4.35, h: 0, line: { color: UP, width: 2 } });
s.addText("开盘 −0.6%  低吸买入", { x: 9.0, y: loY + 0.06, w: 3.4, h: 0.3, fontSize: 12.5, fontFace: F, color: DN, margin: 0 });
s.addText("开盘 +0.6%  高抛卖出", { x: 9.0, y: hiY - 0.36, w: 3.4, h: 0.3, fontSize: 12.5, fontFace: F, color: UP, margin: 0 });
[[8.45, 2.55], [9.2, 3.0], [9.95, 3.75], [10.7, 3.5], [11.45, 2.7], [12.2, 2.0]].forEach(([x, y], i) => {
  s.addShape(p.shapes.OVAL, { x, y, w: 0.11, h: 0.11, fill: { color: i < 3 ? DN : UP }, line: { type: "none" } });
});
s.addText("石药创新 · 300765", { x: M, y: 1.7, w: 7, h: 0.5, fontSize: 20, fontFace: F, color: PRIM, bold: true, charSpacing: 3, margin: 0 });
s.addText("专属做T\n三年回测报告", { x: M, y: 2.15, w: 7.2, h: 2.1, fontSize: 52, fontFace: F, color: TXT, bold: true, lineSpacing: 62, margin: 0 });
s.addText("每日一回合网格：开盘 −0.6% 低吸 → +0.6% 高抛", { x: M, y: 4.45, w: 7, h: 0.42, fontSize: 17, fontFace: F, color: MUT, margin: 0 });
s.addText([
  { text: "125 天分钟级精确回测  +44.3%", options: { color: ACC, bold: true } },
  { text: "   ·   三年日K悲观界 +213%", options: { color: TXT, bold: true } },
], { x: M, y: 5.05, w: 7.4, h: 0.5, fontSize: 19, fontFace: F, margin: 0 });
s.addText("maket 系统 · 2026-09-14 · 双边成本 0.12% · 纸面验证口径", { x: M, y: 6.7, w: 8, h: 0.35, fontSize: 12.5, fontFace: F, color: FAINT, margin: 0 });

/* ── S2 结论一页看 ── */
s = p.addSlide();
head(s, "01  结论", "全组参数为正，但这不是「天天赚」", "12 组参数组合全部正收益——其它 13 只自选股无一是正；盈利结构是胜多败小，不是每天必赚");
const stats = [
  ["+44.3%", "125天合计收益", "采用参数 d=0.6%", ACC],
  ["56.0%", "交易日为正", "44% 的天小亏", TXT],
  ["+0.354%", "单笔平均净收益", "125 回合 · 每日恰一回合", TXT],
  ["−18.3%", "最大累计回撤", "存在亏损月(3月 −10.4%)", TXT],
];
stats.forEach(([num, lab, sub2, c], i) => {
  const x = M + i * 3.1;
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y: 2.0, w: 2.85, h: 2.5, rectRadius: 0.09, fill: { color: CARD }, line: { type: "none" } });
  s.addText(num, { x, y: 2.3, w: 2.85, h: 0.95, fontSize: 44, fontFace: F, color: c, bold: true, align: "center", margin: 0 });
  s.addText(lab, { x, y: 3.35, w: 2.85, h: 0.4, fontSize: 16, fontFace: F, color: TXT, align: "center", margin: 0 });
  s.addText(sub2, { x, y: 3.75, w: 2.85, h: 0.35, fontSize: 12, fontFace: F, color: MUT, align: "center", margin: 0 });
});
const bu = () => ({ code: "25B8", indent: 12 });
s.addText([
  { text: "盈利机制：到达 +0.6% 目标的天赚约 1.2%，兜底/强平天亏 0.1–1%，靠 56:44 的胜率差积累", options: { bullet: bu(), breakLine: true } },
  { text: "与其它票对比：同一 90 天窗口，做全部自选股合计 −88%；立昂微 −2.2%；石药创新是唯一稳定为正的票", options: { bullet: bu(), breakLine: true } },
  { text: "参数从同一段历史选出，有过拟合风险——上线真金白银前，以纸面账本样本外验证为准", options: { bullet: bu() } },
], { x: M, y: 4.95, w: W - 2 * M, h: 1.7, fontSize: 15.5, fontFace: F, color: TXT, paraSpaceAfter: 10, margin: 0, lineSpacingMultiple: 1.15 });
s.addText("来源: bt_3y_300765.py 分钟级回测 / bt_always.py 90日全票对照", src());

/* ── S3 数据前提 ── */
s = p.addSlide();
head(s, "02  数据前提", "三年分钟数据不存在，回测采用两段式", "三大免费数据源实测封顶约 6 个月；分钟级用最深窗口，三年用日K上下界");
s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y: 1.95, w: 6.0, h: 2.1, rectRadius: 0.09, fill: { color: CARD }, line: { type: "none" } });
s.addText("① 分钟级 · 精确回测", { x: M + 0.3, y: 2.15, w: 5.4, h: 0.4, fontSize: 17, fontFace: F, color: PRIM, bold: true, margin: 0 });
s.addText([
  { text: "窗口：2026-03-16 ~ 09-11（125 个交易日）", options: { breakLine: true } },
  { text: "逐 5 分钟 bar 重放，含盘中先后顺序", options: { breakLine: true } },
  { text: "成本 0.12%，信号与实盘同一代码路径", options: {} },
], { x: M + 0.3, y: 2.6, w: 5.5, h: 1.3, fontSize: 13.5, fontFace: F, color: TXT, paraSpaceAfter: 6, margin: 0 });
s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M + 6.5, y: 1.95, w: 6.0, h: 2.1, rectRadius: 0.09, fill: { color: CARD }, line: { type: "none" } });
s.addText("② 三年日K · 区间估算", { x: M + 6.8, y: 2.15, w: 5.4, h: 0.4, fontSize: 17, fontFace: F, color: PRIM, bold: true, margin: 0 });
s.addText([
  { text: "窗口：2023-09-13 ~ 2026-09-11（716 个交易日）", options: { breakLine: true } },
  { text: "日K无法还原盘中顺序 → 给乐观/悲观两界", options: { breakLine: true } },
  { text: "真实值介于两界之间，方向与①互证", options: {} },
], { x: M + 6.8, y: 2.6, w: 5.5, h: 1.3, fontSize: 13.5, fontFace: F, color: TXT, paraSpaceAfter: 6, margin: 0 });
// 数据源实测表
const rows = [
  [{ text: "数据源", options: { bold: true, color: TXT, fill: { color: BG2 } } },
   { text: "5分钟线最深历史", options: { bold: true, color: TXT, fill: { color: BG2 } } },
   { text: "实测结果", options: { bold: true, color: TXT, fill: { color: BG2 } } }],
  ["腾讯 ifzq mkline", "约 125 个交易日", "6,000 根封顶，翻页到头即空"],
  ["东方财富 push2his", "约 1 个月", "1,488 根封顶"],
  ["新浪", "约 42 天", "1,023 根封顶"],
  [{ text: "结论", options: { bold: true, color: ACC } },
   { text: "免费三年分钟数据不存在", options: { bold: true, color: ACC } },
   { text: "两段式为可得最优，非偷懒", options: { bold: true, color: ACC } }],
];
s.addTable(rows, {
  x: M, y: 4.35, w: W - 2 * M, colW: [3.6, 3.6, 5.0], rowH: 0.42,
  fontSize: 13.5, fontFace: F, color: TXT, fill: { color: CARD },
  border: { pt: 0.75, color: "20344A" }, valign: "middle", margin: 0.08,
});
s.addText("来源: 三源实测 2026-09-14（bt_3y_300765.py / bt_3y_daily_bounds.py）", src());

/* ── S4 125天参数搜索 ── */
s = p.addSlide();
head(s, "03  精确回测", "125 天分钟级：12 组参数全部为正", "UB只正T 全线占优；d=0.6% 攻守最均衡（日为正 56% × 合计 +44.3% × 回撤 −18.3）");
const labels = ["0.4%", "0.5%", "0.6%", "0.8%", "1.0%", "1.2%", "0.4%", "0.5%", "0.6%", "0.8%", "1.0%", "1.2%"];
const ubVals = [32.2, 39.6, 44.3, 43.5, 48.3, 46.8];
const uaVals = [20.9, 27.7, 33.7, 39.8, 41.0, 32.5];
s.addChart(p.charts.BAR, [
  { name: "UB 只正T", labels: labels.slice(0, 6), values: ubVals },
  { name: "UA 双向", labels: labels.slice(0, 6), values: uaVals },
], {
  x: M, y: 1.95, w: 7.6, h: 4.35, barDir: "col", barGrouping: "clustered",
  chartColors: [PRIM, "44607A"],
  chartArea: { fill: { color: BG } }, plotArea: { fill: { color: BG } },
  catAxisLabelColor: MUT, catAxisLabelFontSize: 12, catAxisLabelFontFace: F,
  valAxisLabelColor: MUT, valAxisLabelFontSize: 11, valAxisLabelFontFace: F,
  valAxisTitle: "125天合计收益 %", showValAxisTitle: true, valAxisTitleColor: MUT, valAxisTitleFontSize: 11,
  valGridLine: { color: "20344A", size: 0.5 }, catGridLine: { style: "none" },
  showValue: true, dataLabelPosition: "outEnd", dataLabelColor: TXT, dataLabelFontSize: 10, dataLabelFontFace: F,
  showLegend: true, legendPos: "b", legendColor: MUT, legendFontSize: 12, legendFontFace: F,
});
// 右侧采用参数卡
s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 8.55, y: 1.95, w: 4.22, h: 4.35, rectRadius: 0.09, fill: { color: CARD }, line: { type: "none" } });
s.addText("采用参数", { x: 8.85, y: 2.2, w: 3.6, h: 0.4, fontSize: 15, fontFace: F, color: PRIM, bold: true, margin: 0 });
s.addText("d = 0.6%", { x: 8.85, y: 2.6, w: 3.6, h: 0.8, fontSize: 40, fontFace: F, color: ACC, bold: true, margin: 0 });
s.addText([
  { text: "胜率 56.0%（70/125 天）", options: { breakLine: true } },
  { text: "单笔均值 +0.354%", options: { breakLine: true } },
  { text: "最差单日 −6.81%", options: { breakLine: true } },
  { text: "累计回撤 −18.3，全组最小档", options: { breakLine: true } },
  { text: "d=1.0% 合计更高(+48.3%) 但日为正仅 50.4%", options: { color: MUT } },
], { x: 8.85, y: 3.55, w: 3.7, h: 2.5, fontSize: 14, fontFace: F, color: TXT, paraSpaceAfter: 8, margin: 0 });
s.addText("来源: bt_3y_result.json · 每组 125 回合 · 石药创新 2026-03-16~09-11", src());

/* ── S5 三年区间估算 ── */
s = p.addSlide();
head(s, "04  三年视角", "716 个交易日：悲观界仍大幅为正", "日K 区间估算（真实值介于两界之间）；三档成本敏感性下方向不变");
s.addChart(p.charts.BAR, [
  { name: "乐观界", labels: ["d=0.5%", "d=0.6%", "d=0.8%", "d=1.0%"], values: [561, 672, 874, 1062] },
  { name: "悲观界", labels: ["d=0.5%", "d=0.6%", "d=0.8%", "d=1.0%"], values: [219, 213, 188, 202] },
], {
  x: M, y: 1.95, w: 7.6, h: 4.35, barDir: "col", barGrouping: "clustered",
  chartColors: ["44607A", ACC],
  chartArea: { fill: { color: BG } },
  catAxisLabelColor: MUT, catAxisLabelFontSize: 12, catAxisLabelFontFace: F,
  valAxisLabelColor: MUT, valAxisLabelFontSize: 11, valAxisLabelFontFace: F,
  valAxisTitle: "三年合计收益 %（区间估算）", showValAxisTitle: true, valAxisTitleColor: MUT, valAxisTitleFontSize: 11,
  valGridLine: { color: "20344A", size: 0.5 }, catGridLine: { style: "none" },
  showValue: true, dataLabelPosition: "outEnd", dataLabelColor: TXT, dataLabelFontSize: 10, dataLabelFontFace: F,
  showLegend: true, legendPos: "b", legendColor: MUT, legendFontSize: 12, legendFontFace: F,
});
s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 8.55, y: 1.95, w: 4.22, h: 2.35, rectRadius: 0.09, fill: { color: CARD }, line: { type: "none" } });
s.addText("估算口径", { x: 8.85, y: 2.15, w: 3.6, h: 0.38, fontSize: 15, fontFace: F, color: PRIM, bold: true, margin: 0 });
s.addText([
  { text: "乐观界：盘中冲到高线即算达标", options: { breakLine: true } },
  { text: "悲观界：收盘站上高线才算，冲高回落不算", options: { breakLine: true } },
  { text: "日K 无盘中顺序，两界即真实值包络", options: {} },
], { x: 8.85, y: 2.58, w: 3.7, h: 1.6, fontSize: 13, fontFace: F, color: TXT, paraSpaceAfter: 7, margin: 0 });
s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 8.55, y: 4.5, w: 4.22, h: 1.8, rectRadius: 0.09, fill: { color: CARD }, line: { type: "none" } });
s.addText("成本敏感性（d=0.6%）", { x: 8.85, y: 4.68, w: 3.6, h: 0.38, fontSize: 15, fontFace: F, color: PRIM, bold: true, margin: 0 });
s.addText([
  { text: "0.12%：+44.3%（采用假设）", options: { breakLine: true } },
  { text: "0.20%：合计缩水约两成", options: { breakLine: true } },
  { text: "0.30%：方向仍为正", options: {} },
], { x: 8.85, y: 5.1, w: 3.7, h: 1.1, fontSize: 13, fontFace: F, color: TXT, paraSpaceAfter: 6, margin: 0 });
s.addText("来源: bt_3y_daily_bounds.py · 716 个交易日 · 2023-09-13~2026-09-11", src());

/* ── S6 月度一致性 ── */
s = p.addSlide();
head(s, "05  一致性", "月度拆解：7 个月中 6 个月为正", "盈利不是均匀的——3 月亏 10.4% 提醒：连续亏损期必然存在，别在最深回撤处放弃纪律");
const months = ["26-03", "26-04", "26-05", "26-06", "26-07", "26-08", "26-09*"];
const mvals = [-10.4, 0.5, 4.0, 7.3, 23.9, 18.5, 4.4];
const mcolors = mvals.map(v => v >= 0 ? PRIM : UP);
s.addChart(p.charts.BAR, [
  { name: "当月合计%", labels: months, values: mvals },
], {
  x: M, y: 2.0, w: 7.7, h: 4.3, barDir: "col", varyColors: true, chartColors: mcolors,
  chartArea: { fill: { color: BG } },
  catAxisLabelColor: MUT, catAxisLabelFontSize: 12, catAxisLabelFontFace: F,
  valAxisLabelColor: MUT, valAxisLabelFontSize: 11, valAxisLabelFontFace: F,
  valGridLine: { color: "20344A", size: 0.5 }, catGridLine: { style: "none" },
  showValue: true, dataLabelPosition: "outEnd", dataLabelColor: TXT, dataLabelFontSize: 11, dataLabelFontFace: F,
  showLegend: false,
});
s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 8.65, y: 2.0, w: 4.12, h: 4.3, rectRadius: 0.09, fill: { color: CARD }, line: { type: "none" } });
s.addText("读法", { x: 8.95, y: 2.25, w: 3.5, h: 0.4, fontSize: 15, fontFace: F, color: PRIM, bold: true, margin: 0 });
s.addText([
  { text: "7月+8月贡献了近六成利润（+42.4%）", options: { breakLine: true } },
  { text: "3月 −10.4%：单月回撤可吞掉数月利润", options: { breakLine: true } },
  { text: "日为正比例 39%~62% 随月波动，无一月达 100%", options: { breakLine: true } },
  { text: "* 2026-09 仅 9 个交易日", options: { color: MUT } },
], { x: 8.95, y: 2.7, w: 3.6, h: 3.2, fontSize: 13.5, fontFace: F, color: TXT, paraSpaceAfter: 9, margin: 0 });
s.addText("来源: bt_3y_result.json 最优参数月度拆分（UB 1.0% 口径，d=0.6% 结构一致）", src());

/* ── S7 风险与运行 ── */
s = p.addSlide();
head(s, "06  落地", "系统已上线，真金白银前看纸面账本", "launchd 全自动：9:20 计划 · 盘中每10分钟信号弹窗 · 15:05 复盘");
s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y: 1.95, w: 6.0, h: 3.1, rectRadius: 0.09, fill: { color: CARD }, line: { type: "none" } });
s.addText("现在每天自动发生什么", { x: M + 0.3, y: 2.15, w: 5.4, h: 0.4, fontSize: 16, fontFace: F, color: PRIM, bold: true, margin: 0 });
const steps = [
  ["09:20", "同步自选股 · 生成当日网格价位"],
  ["09:45–14:30", "跌破低吸线 → 通知中心弹窗+提示音"],
  ["14:30", "未触发则兜底进场（保证天天有交易）"],
  ["14:55", "未到目标强制平仓 · 记入纸面账本"],
  ["15:05", "收盘复盘：当日盈亏 + 累计胜率"],
];
steps.forEach(([t, d], i) => {
  const y = 2.62 + i * 0.47;
  s.addShape(p.shapes.OVAL, { x: M + 0.32, y: y + 0.05, w: 0.13, h: 0.13, fill: { color: PRIM }, line: { type: "none" } });
  s.addText([
    { text: t + "   ", options: { color: ACC, bold: true } },
    { text: d, options: { color: TXT } },
  ], { x: M + 0.62, y, w: 5.3, h: 0.4, fontSize: 13.5, fontFace: F, margin: 0 });
});
s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M + 6.5, y: 1.95, w: 6.0, h: 3.1, rectRadius: 0.09, fill: { color: CARD }, line: { type: "none" } });
s.addText("必须接受的三件事", { x: M + 6.8, y: 2.15, w: 5.4, h: 0.4, fontSize: 16, fontFace: F, color: UP, bold: true, margin: 0 });
const risks = [
  "44% 的交易日是亏损的——赚的是月度合计，不是每一天",
  "参数(0.6%)从同一段历史选出，样本外可能衰减",
  "跳空/消息日网格失效；暴跌日单日可亏 6.8%",
];
risks.forEach((r, i) => {
  const y = 2.66 + i * 0.75;
  s.addShape(p.shapes.OVAL, { x: M + 6.82, y: y + 0.06, w: 0.13, h: 0.13, fill: { color: UP }, line: { type: "none" } });
  s.addText(r, { x: M + 7.12, y, w: 5.2, h: 0.7, fontSize: 13.5, fontFace: F, color: TXT, margin: 0, lineSpacingMultiple: 1.1 });
});
s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y: 5.35, w: W - 2 * M, h: 1.15, rectRadius: 0.09, fill: { color: BG2 }, line: { type: "none" } });
s.addText([
  { text: "上真钱的判断标准：", options: { color: ACC, bold: true } },
  { text: "纸面账本连续 4 周与回测同向（胜率≥50%、单笔均值>0）才考虑投入，且首期不超过计划做T资金的 1/10。系统仅决策支持，不构成投资建议。", options: { color: TXT } },
], { x: M + 0.35, y: 5.55, w: W - 2 * M - 0.7, h: 0.8, fontSize: 14.5, fontFace: F, margin: 0, lineSpacingMultiple: 1.2 });
s.addText("报告与脚本: maket/bt_3y_report.md · bt_3y_300765.py · bt_3y_daily_bounds.py", src());

p.writeFile({ fileName: "/Users/francishy/Desktop/石药创新专属做T-三年回测报告.pptx" })
  .then(() => console.log("PPT 已生成"));
