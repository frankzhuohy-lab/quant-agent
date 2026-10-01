#!/usr/bin/env node
/* 做T纸面系统 · 实时看板 — 零依赖静态服务
   /api/maket: 聚合 ~/.maket/ 运行时数据（账本/预测/筛选池/报告/提醒流水）
   2026-09-17: 主视图整体替换为 maket 做T系统（旧 R443 看板内容已移除） */
const http = require('http');
const https = require('https');
const fs = require('fs');
const os = require('os');
const path = require('path');

const PORT = Number(process.env.PORT) || 3600;
const DIR = __dirname;
const MAKET = process.env.MAKET_DIR || path.join(os.homedir(), '.maket');

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
};

function json(res, code, body) {
  res.writeHead(code, {
    'Content-Type': 'application/json; charset=utf-8',
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'SAMEORIGIN',
  });
  res.end(typeof body === 'string' ? body : JSON.stringify(body));
}
function readJson(p) { try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e) { return null; } }
function readText(p) { try { return fs.readFileSync(p, 'utf8'); } catch (e) { return null; } }
function todayKey(d) {
  const t = d || new Date();
  const p = n => String(n).padStart(2, '0');
  return '' + t.getFullYear() + p(t.getMonth() + 1) + p(t.getDate());
}
function nextTradingDayKey() {
  const d = new Date();
  do { d.setDate(d.getDate() + 1); } while (d.getDay() === 0 || d.getDay() === 6);
  const p = n => String(n).padStart(2, '0');
  return '' + d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate());
}
function latestFile(prefix) {
  try {
    const files = fs.readdirSync(MAKET + '/reports').filter(f => f.startsWith(prefix)).sort();
    return files.length ? MAKET + '/reports/' + files[files.length - 1] : null;
  } catch (e) { return null; }
}

function fetchQuotes(symbols) {
  return new Promise(resolve => {
    if (!symbols.length) return resolve({});
    const req = https.get('https://qt.gtimg.cn/q=' + symbols.join(','),
      { timeout: 5000 }, res => {
        const chunks = [];
        res.on('data', c => chunks.push(c));
        res.on('end', () => {
          const text = Buffer.concat(chunks).toString('latin1');
          const out = {};
          for (const line of text.split(';')) {
            const m = line.match(/v_(\w+)="([^"]*)"/);
            if (!m) continue;
            const f = m[2].split('~');
            if (f.length < 35) continue;
            const px = parseFloat(f[3]);
            if (px > 0) out[m[1]] = px;
          }
          resolve(out);
        });
      });
    req.on('error', () => resolve({}));
    req.on('timeout', () => { req.destroy(); resolve({}); });
  });
}

async function buildMaket() {
  const ledger = readJson(MAKET + '/ledger_t.json') || { rounds: [], open: {} };
  const rounds = ledger.rounds || [];
  const bookable = rounds.filter(r => !r.t1_void);  // T+1不可执行回合不入统计
  const wins = bookable.filter(r => r.ret_net > 0).length;
  const total = bookable.reduce((a, r) => a + r.ret_net, 0);
  const byDay = {};
  for (const r of bookable) byDay[r.date] = (byDay[r.date] || 0) + r.ret_net;

  const preds = readJson(MAKET + '/predictions.json') || [];
  const scored = preds.filter(p => p.status === 'scored' && p.score_result && !p.score_result.error);
  const pending = preds.filter(p => p.status === 'pending');
  const hit = { P1: [0, 0], P2: [0, 0], P3: [0, 0] };
  for (const p of scored) {
    const s = (p.score_result && p.score_result.score) || {};
    for (const k of ['P1', 'P2', 'P3']) {
      const v = s[k];
      if (v && (v.verdict === '对' || v.verdict === '错')) {
        hit[k][1]++;
        if (v.verdict === '对') hit[k][0]++;
      }
    }
  }

  const dyn = readJson(MAKET + '/whitelist_dynamic.json') || { stocks: [], expire: '' };
  const zones = readJson(MAKET + '/zones_' + todayKey() + '.json');
  const pool = [{ code: '300765', name: '石药创新', resident: true }];
  for (const s of (dyn.stocks || [])) {
    pool.push({ code: s.code, name: s.name, symbol: s.symbol || s.code, score: s.score });
  }
  for (const p of pool) {
    const z = zones && zones.stocks && zones.stocks[p.code];
    if (z) { p.regime = z.regime; p.buy_lo = z.buy_lo; p.buy_hi = z.buy_hi; p.sell_lo = z.sell_lo; p.sell_hi = z.sell_hi; }
  }

  // 隔日买入准备: 下一交易日 = 常驻 + 晚间筛选池; zones 由收盘结算预生成
  const nk = nextTradingDayKey();
  const zonesTm = readJson(MAKET + '/zones_' + nk + '.json');
  const tmPool = [{ code: '300765', name: '石药创新', resident: true }]
    .concat((dyn.stocks || []).map(s => ({ code: s.code, name: s.name, score: s.score })));
  for (const p of tmPool) {
    const z = zonesTm && zonesTm.stocks && zonesTm.stocks[p.code];
    if (z) { p.regime = z.regime; p.buy_lo = z.buy_lo; p.buy_hi = z.buy_hi; p.sell_lo = z.sell_lo; p.sell_hi = z.sell_hi; }
  }
  const tmPreds = pending.filter(p => (p.target_date || '').replace(/-/g, '') === nk);

  // 持仓明细: 底仓 × 实时价(失败回退收盘快照)
  const acc = readJson(MAKET + '/account.json') || {};
  const bases = acc.bases || {};
  const symOf = c => (c.startsWith('6') || c.startsWith('9') ? 'sh' : 'sz') + c;
  const quotes = await fetchQuotes(Object.keys(bases).map(symOf)).catch(() => ({}));
  const lastPx = (acc.last_mark && acc.last_mark.close_px) || {};
  const positions = Object.entries(bases).map(([code, b]) => {
    const px = quotes[symOf(code)] || lastPx[code] || null;
    const cost = b.avg_cost * b.shares;
    const mv = px != null ? px * b.shares : null;
    return { code, name: b.name || code, shares: b.shares, avg_cost: b.avg_cost,
             px, mv, pnl: mv != null ? mv - cost : null,
             pct: px != null ? (px / b.avg_cost - 1) * 100 : null };
  }).sort((a, b) => (b.mv || 0) - (a.mv || 0));

  // 个股做T收益（已平回合按票聚合）
  const byStock = {};
  for (const r of bookable) {
    const s = byStock[r.code] || (byStock[r.code] = { code: r.code, name: r.name, n: 0, wins: 0, total: 0 });
    s.n++; if (r.ret_net > 0) s.wins++; s.total = Math.round((s.total + r.ret_net) * 100) / 100;
  }
  const tstats = Object.values(byStock).map(s => ({ ...s, avg: Math.round(s.total / s.n * 1000) / 1000 }))
    .sort((a, b) => b.total - a.total);

  const tk = todayKey();
  let closeRep = readText(MAKET + '/reports/close_' + tk + '.txt');
  if (!closeRep) { const f = latestFile('close_'); if (f) closeRep = readText(f); }
  const planRep = readText(MAKET + '/reports/plan_' + tk + '.txt') || readText(latestFile('plan_'));
  let alerts = readText(MAKET + '/reports/alerts_' + tk + '.log');
  if (!alerts) { const f = latestFile('alerts_'); if (f) alerts = readText(f); }
  // 过滤每分钟持仓心跳, 只留真实动作
  const alertLines = alerts ? alerts.trim().split('\n').filter(l => !l.includes('\u23f3')).slice(-80) : [];
  const signals = alertLines.filter(l => l.includes('低吸买入') || l.includes('🔔')).slice(-8).reverse();
  const account = readJson(MAKET + '/account.json');
  const screenRep = readText(MAKET + '/reports/screen_' + tk + '.txt') || readText(latestFile('screen_'));

  return {
    generated_at: new Date().toLocaleString('zh-CN', { hour12: false }),
    today: tk,
    rules: '开盘价-0.6%低吸 → +0.6%高抛 · 止损-1.5% · 无兜底 · 9:45-14:55 · 60秒轮询 · 双边成本0.12%',
    stats: {
      rounds: bookable.length,
      wins,
      win_rate: bookable.length ? wins / bookable.length : 0,
      total_pnl: Math.round(total * 100) / 100,
      avg: bookable.length ? Math.round(total / bookable.length * 1000) / 1000 : 0,
    },
    byDay: Object.keys(byDay).sort().map(d => ({ date: d, pnl: Math.round(byDay[d] * 100) / 100 })),
    rounds: rounds.slice().reverse(),
    open: ledger.open || {},
    hit,
    scored: scored.slice(-10).reverse(),
    pending,
    pool,
    close_report: closeRep,
    plan_report: planRep,
    screen_report: screenRep,
    alert_lines: alertLines,
    signals,
    account,
    positions,
    tstats,
    tomorrow: { date: nk, pool: tmPool, preds: tmPreds, zones_ready: !!zonesTm },
    predictions_log: readText(MAKET + '/predictions_log.md'),
  };
}

http.createServer(async (req, res) => {
  const url = req.url.split('?')[0];

  if (url === '/api/maket') {
    res.setHeader('Cache-Control', 'no-store');
    try { json(res, 200, await buildMaket()); } catch (e) { json(res, 500, { error: e.message }); }
    return;
  }
  if (url === '/api/data') {
    const raw = readText(path.join(DIR, 'dashboard_data.json'));
    if (raw) { json(res, 200, raw); } else { json(res, 404, { error: 'legacy data 未生成' }); }
    return;
  }
  if (url === '/api/ping') { json(res, 200, { ok: true }); return; }

  let rel = url === '/' || url === '' ? '/index.html' : url;
  const fp = path.normalize(path.join(DIR, rel));
  if (!fp.startsWith(DIR)) { res.writeHead(403); res.end('403'); return; }
  fs.readFile(fp, (err, buf) => {
    if (err) { res.writeHead(404, { 'Content-Type': 'text/plain' }); res.end('404'); return; }
    const ext = path.extname(fp).toLowerCase();
    res.writeHead(200, {
      'Content-Type': TYPES[ext] || 'application/octet-stream',
      'X-Content-Type-Options': 'nosniff',
      'X-Frame-Options': 'SAMEORIGIN',
      'Cache-Control': 'no-store',
    });
    res.end(buf);
  });
}).listen(PORT, '127.0.0.1', () => {
  console.log('maket dashboard on 127.0.0.1:' + PORT);
});
