const { chromium } = require('playwright');
const path = require('path');
const dir = __dirname;
// [file, caption line 1, line 2, top crop px, bottom crop px] in source pixels (width 1856)
const SHOTS = [
  ['grid.jpg', 'Find what to read', 'and watch next', 115, 0],
  ['seed.jpg', 'Matched by story,', 'not popularity', 110, 0],
  ['filters.jpg', 'Tune what counts', 'and filter anything', 115, 0],
  ['settings.jpg', 'Hide what you have seen,', 'with a profile each', 115, 0],
];
const html = (file, l1, l2, top, bottom) => `<!doctype html><html><head><meta charset="utf-8"><style>
@font-face { font-family: SG; src: url("file:///home/user/MangaBrain/web/fonts/space_grotesk_bold.ttf"); font-weight: 700; }
html,body{margin:0;padding:0}
body{width:1080px;height:1920px;overflow:hidden;background:radial-gradient(circle at 50% 0%, #1d2747 0%, #0e1015 62%);font-family:SG,sans-serif;color:#e6e9f0;position:relative}
.cap{position:absolute;top:110px;left:0;right:0;text-align:center;font-size:76px;font-weight:700;letter-spacing:-0.025em;line-height:1.08}
.cap span{color:#37c9a5}
.frame{position:absolute;top:400px;left:70px;width:940px;height:1600px;border-radius:44px;overflow:hidden;border:3px solid #2a3350;background:#0e1015;box-shadow:0 30px 80px rgba(0,0,0,.55)}
.inner{position:absolute;left:0;width:940px;top:${-top * 940 / 1856}px}
.inner img{width:940px;display:block}
.fade{position:absolute;left:70px;right:70px;bottom:0;height:160px;background:linear-gradient(transparent,#0e1015)}
</style></head><body>
<div class="cap">${l1}<br><span>${l2}</span></div>
<div class="frame"><div class="inner"><img id="shot" src="file://${path.join(dir, file)}"></div></div>
<div class="fade"></div>
<script>
  const img = document.getElementById('shot');
  const fit = () => {
    const shown = (img.naturalHeight - ${top}) * 940 / 1856;
    if (shown < 1600) {
      document.querySelector('.frame').style.height = shown + 'px';
      document.querySelector('.fade').style.display = 'none';
    }
  };
  if (img.complete) fit(); else img.onload = fit;
</script>
</body></html>`;
(async () => {
  const fs = require('fs');
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1080, height: 1920 }, deviceScaleFactor: 1 });
  let i = 1;
  for (const s of SHOTS) {
    const f = path.join(dir, `tmp${i}.html`);
    fs.writeFileSync(f, html(...s));
    await page.goto('file://' + f);
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(dir, `phone-${i}.jpg`), type: 'jpeg', quality: 92 });
    i++;
  }
  await browser.close();
})();
