/* 本地自测：起 server.js 子进程 → 请求各端点 → 停掉 */
const { spawn } = require('child_process');
const path = require('path');

const srv = spawn(process.execPath, [path.join(__dirname, 'server.js')], {
  env: Object.assign({}, process.env, { PORT: '3600' }),
  stdio: ['ignore', 'pipe', 'pipe'],
});
srv.stdout.on('data', d => console.log('[srv]', d.toString().trim()));
srv.stderr.on('data', d => console.error('[srv-err]', d.toString().trim()));

function get(url) {
  return new Promise((res, rej) => {
    require('http').get(url, r => {
      let b = '';
      r.on('data', c => b += c);
      r.on('end', () => res({ status: r.statusCode, body: b, ct: r.headers['content-type'] }));
    }).on('error', rej);
  });
}

setTimeout(async () => {
  let fail = 0;
  try {
    const ping = await get('http://127.0.0.1:3600/api/ping');
    console.log('ping:', ping.status, ping.body);
    if (ping.status !== 200) fail++;

    const data = await get('http://127.0.0.1:3600/api/data');
    const d = JSON.parse(data.body);
    console.log('data:', data.status, '| keys:', Object.keys(d).join(','));
    console.log('  bt:', d.backtest.dates.length, 'dates,', d.backtest.nav.length, 'nav,', d.backtest.trades.length, 'trades');
    console.log('  model:', d.model.name, '| paper nav:', d.paper.snapshot.nav);
    if (data.status !== 200 || !d.backtest) fail++;

    const html = await get('http://127.0.0.1:3600/');
    const hasChart = html.body.includes('paperChart') && html.body.includes('btChart');
    console.log('html:', html.status, '| charts:', hasChart, '| fetch rel:', html.body.includes("fetch('api/data'"));
    if (html.status !== 200 || !hasChart) fail++;

    const bad = await get('http://127.0.0.1:3600/../etc/passwd');
    console.log('path traversal:', bad.status, '(expect 403/404)');
  } catch (e) {
    console.error('FAIL:', e.message); fail++;
  }
  srv.kill();
  console.log(fail === 0 ? 'ALL PASS' : 'FAILED: ' + fail);
  process.exit(fail === 0 ? 0 : 1);
}, 1200);
