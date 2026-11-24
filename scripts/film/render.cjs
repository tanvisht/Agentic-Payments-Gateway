// node scripts/film/render.cjs <outdir> [fps] -> PNG frames of one seamless loop
const { chromium } = require('playwright');
const http = require('http'), fs = require('fs'), path = require('path');
const ROOT = path.resolve(__dirname, '../..');
(async () => {
  const out = process.argv[2], fps = +(process.argv[3] || 24);
  const srv = http.createServer((q, r) => { const f = path.join(ROOT, decodeURIComponent(q.url.split('?')[0])); if (!f.startsWith(ROOT) || !fs.existsSync(f)) { r.writeHead(404); return r.end(); }
    r.writeHead(200, { 'Content-Type': f.endsWith('.js') ? 'text/javascript' : 'text/html' }); fs.createReadStream(f).pipe(r); }).listen(8799);
  const b = await chromium.launch({ args: ['--use-gl=swiftshader', '--enable-webgl', '--ignore-gpu-blocklist'] });
  const p = await b.newPage({ viewport: { width: 1920, height: 1080 } });
  p.on('pageerror', e => console.error('PAGEERROR', e.message));
  await p.goto('http://127.0.0.1:8799/scripts/film/film.html'); await p.waitForFunction(() => window.ready, null, { timeout: 60000 });
  const loop = await p.evaluate(() => window.LOOP), n = loop * fps;
  fs.mkdirSync(out, { recursive: true });
  for (let i = 0; i < n; i++) {
    await p.evaluate((t) => window.renderAt(t), i / fps);
    await p.locator('#c').screenshot({ path: path.join(out, `f${String(i).padStart(4, '0')}.png`) });
    if (i % 48 === 0) console.log('frame', i, '/', n);
  }
  await b.close(); srv.close();
})();
