// Navigation et retours d'export sur données synthétiques, sans service ni X.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'playwright');
const root = path.resolve(__dirname, '..');
const json = body => ({ contentType: 'application/json', body: JSON.stringify(body) });
const destination = 'D:\\Recherche\\ZEVENT';
const completed = { status: 'done', tweets: 70, copied_files: 207,
  missing_files: [], display_path: destination + '\\archive_X_20260909_000820_8c4b7d10' };

(async () => {
  const browser = await chromium.launch({ headless: true,
    executablePath: process.argv[2] || process.env.CHROME_PATH });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, locale: 'fr-FR' });
    const errors = [], submitted = [];
    page.on('pageerror', e => errors.push(e.message));
    let status = { ...completed, folder_missing: true };
    const items = ['Actu2Twitch', 'ZEVENT', 'ZeratoR'].map((author, i) => ({
      id: String(1234567890 + i), author, category: 'ZEVENT 2026', status: 'done',
      text: ['Un moment à conserver : la communauté réunie pour le ZEVENT.',
        'Les temps forts de cette édition, et une mobilisation collective pour les associations.',
        'Merci à toutes les personnes qui ont participé !'][i],
      posted: '2026-09-08T12:00:00Z', archived: '2026-09-09T09:00:00Z',
      views: 12000, likes: 340, reposts: 42,
    }));
    const posts = { items, total: 3, page: 1, size: 20, pages: 1,
      authors: items.map(item => ({ author: item.author, count: 1 })),
      categories: [{ category: 'ZEVENT 2026' }, { category: 'Autres recherches' }] };
    await page.addInitScript(() => { document.addEventListener('DOMContentLoaded', () => {
      if(sessionStorage.getItem('test-paired'))document.documentElement.dataset.zeventPaired = '1';
    }); });
    await page.route('http://archive.test/**', async route => {
      const url = new URL(route.request().url()), pathname = url.pathname;
      if (pathname === '/api/posts') return route.fulfill(json(posts));
      if (pathname === '/api/jobs') return route.fulfill(json({ jobs: [], service_status: { session_present: true, account_active: true } }));
      if (pathname === '/api/paths') return route.fulfill(json({ data: destination + '\\data', posts: destination + '\\data\\posts', extension: destination + '\\chrome-extension' }));
      if (pathname === '/api/results/choose-folder') return route.fulfill(json({ destination: 'D:\\Nouveau dossier', cancelled: false }));
      if (pathname === '/api/archive') {
        submitted.push(route.request().postDataJSON()); return route.fulfill(json({ status: 'queued' }));
      }
      if (pathname === '/api/results') {
        if (route.request().method() === 'POST') {
          submitted.push(route.request().postDataJSON()); status = { status: 'running' };
        }
        return route.fulfill(json({ destination, include_automatic: false, export: status }));
      }
      const name = pathname === '/' ? 'index.html' : pathname.slice(1);
      if (!['index.html', 'ui.css', 'ui.js', 'strings.js'].includes(name)) return route.fulfill({ status: 404, body: '' });
      return route.fulfill({ contentType: name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html',
        body: fs.readFileSync(path.join(root, 'manual_ui', name), 'utf8') });
    });
    await page.goto('http://archive.test/?lang=fr');
    await page.waitForSelector('.job');
    // L'API peut répondre avant le script de l'extension : ne pas afficher
    // une fausse demande d'installation durant cette course au chargement.
    assert.equal(await page.locator('#setup-text').textContent(), 'Vérification…');
    assert.equal(await page.locator('#pair-extension').isVisible(), false);
    await page.evaluate(() => { document.documentElement.dataset.zeventPaired='pending'; });
    assert.equal(await page.locator('#setup-text').textContent(), 'Vérification…');
    await page.evaluate(() => { document.documentElement.dataset.zeventPaired='0'; });
    await page.waitForFunction(() => document.querySelector('#setup-text').textContent.includes('a échoué'));
    assert.equal(await page.locator('#pair-extension').isVisible(), true);
    await page.evaluate(() => {
      document.documentElement.dataset.zeventPaired='1';sessionStorage.setItem('test-paired','1');
    });
    await page.waitForFunction(() => document.querySelector('#setup-text').textContent.includes('Extension connectée'));
    assert.equal(await page.locator('#pair-extension').isVisible(), false);
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    assert.match(await page.locator('#setup-text').textContent(), /Extension connectée/);
    await page.waitForFunction(() => document.querySelector('#export-status').textContent.includes('introuvable'));
    assert.equal(await page.locator('#export-path').isVisible(), false);
    assert.equal(await page.locator('#include-automatic').isVisible(), false);
    assert.equal(await page.locator('#path-data').isVisible(), false);
    assert.doesNotMatch(await page.locator('#export-status').textContent(), /70 posts|D:\\/);
    assert.ok(await page.evaluate(() => document.querySelector('#jobs').getBoundingClientRect().top < document.querySelector('#add-card').getBoundingClientRect().top));
    await page.locator('.quick-actions a[href="#add-card"]').click();
    assert.equal(await page.evaluate(() => document.activeElement.id), 'urls');
    await page.locator('#add .options summary').click();
    await page.locator('#include-replies').check();
    await page.locator('#refresh').check();
    await page.locator('#urls').fill('https://x.com/i/status/123\nhttps://x.com/i/status/456');
    await page.locator('#add button[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('#feedback').textContent.startsWith('2 ajoutées'));
    assert.equal(submitted.length, 2);
    assert.ok(submitted.every(body => body.refresh && body.include_replies));
    await page.locator('#add .options summary').click();
    await page.locator('#choose-folder').click();
    await page.waitForFunction(() => document.querySelector('#result-folder').value === 'D:\\Nouveau dossier');
    await page.evaluate(() => updateResults());
    assert.match(await page.locator('#export-status').textContent(), /Dossier choisi/);
    await page.locator('#export-categories').selectOption('ZEVENT 2026');
    await page.locator('#export-button').click();
    await page.waitForFunction(() => document.querySelector('#export-status').textContent.includes('en cours'));
    assert.deepEqual(submitted[2], { destination: 'D:\\Nouveau dossier', include_automatic: false, categories: ['ZEVENT 2026'] });
    assert.equal(await page.locator('#export-button').isDisabled(), true);
    status = { ...completed, status: 'partial', missing_files: ['media/photo.jpg'] };
    await page.evaluate(() => updateResults());
    assert.match(await page.locator('#export-status').textContent(), /Export incomplet.*1 fichier manquant/s);
    status = completed;
    await page.evaluate(() => updateResults());
    assert.match(await page.locator('#export-status').textContent(), /Export terminé.*70 posts · 207 fichiers copiés/s);
    assert.equal(await page.locator('#export-button').isEnabled(), true);
    await page.locator('#export-location summary').click();
    assert.equal(await page.locator('#export-path').textContent(), completed.display_path);
    await page.locator('#export-location summary').click();

    await page.locator('#q').fill('communauté');
    await page.waitForURL(/q=/);
    await page.locator('#lang a[data-lang=en]').click();
    await page.waitForURL(/lang=en/);
    assert.equal(new URL(page.url()).searchParams.get('q'), 'communauté');
    await page.locator('#clear').click();
    assert.equal(new URL(page.url()).searchParams.get('lang'), 'en');
    await page.reload();
    assert.equal(await page.locator('html').getAttribute('lang'), 'en');
    await page.locator('#lang a[data-lang=fr]').click();
    await page.waitForURL(/lang=fr/);
    await page.waitForSelector('.job');
    await page.evaluate(() => window.scrollTo(0, 0));
    if (process.env.UI_SCREENSHOTS) {
      fs.mkdirSync(process.env.UI_SCREENSHOTS, { recursive: true });
      await page.screenshot({ path: path.join(process.env.UI_SCREENSHOTS, 'interface-desktop.png'), fullPage: true });
    }
    for (const width of [390, 320, 768]) {
      await page.setViewportSize({ width, height: 844 });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Débordement à ${width}px`);
      if (width === 390 && process.env.UI_SCREENSHOTS) await page.screenshot({ path: path.join(process.env.UI_SCREENSHOTS, 'interface-mobile.png'), fullPage: true });
    }
    assert.deepEqual(errors, []);
    console.log('PASS: navigation, ajout, options, dossier, export partiel/complet, langue persistante et écrans 320/390/768/1440px. Aucun appel à X.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
