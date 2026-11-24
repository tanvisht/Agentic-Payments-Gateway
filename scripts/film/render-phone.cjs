// node scripts/film/render-phone.cjs <outdir> [fps] [onlyTimes,comma]
const { chromium } = require("playwright"); const path = require("path"); const fs = require("fs");
(async () => {
  const [out, fps = "30", only] = process.argv.slice(2); fs.mkdirSync(out, { recursive: true });
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 720, height: 900 }, deviceScaleFactor: 1 });
  await p.goto("file://" + path.join(__dirname, "phone.html"));
  const times = only ? only.split(",").map(Number) : Array.from({ length: Math.round(13 * fps) }, (_, i) => i / fps);
  for (let i = 0; i < times.length; i++) {
    await p.evaluate((t) => renderAt(t), times[i]);
    await p.screenshot({ path: path.join(out, `f${String(i).padStart(4, "0")}.png`) });
  }
  await b.close();
})();
