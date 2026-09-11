// Bouton « Tout réessayer » du tableau de bord local.
// Les appels reseau sont interceptes : aucun post n'est reellement rearchive
// et aucune requete n'est envoyee a X.
const assert = require('node:assert/strict');
// Playwright est resolu depuis node_modules, ou via PLAYWRIGHT_PATH si le
// module vit ailleurs. Chrome est designe par argv[2] ou CHROME_PATH.
function loadPlaywright() {
  for (const candidate of [process.env.PLAYWRIGHT_PATH, 'playwright'].filter(Boolean)) {
    try { return require(candidate); } catch {}
  }
  throw Error('Playwright introuvable. npm i -D playwright, ou definissez PLAYWRIGHT_PATH.');
}
const { chromium } = loadPlaywright();
const CHROME = process.argv[2] || process.env.CHROME_PATH;

const job = (id, status) => ({
  tweet_id: id, url: `https://x.com/i/status/${id}`, status,
  attempts: 1, include_replies: false, error: null, result: null,
  selection: { mode: 'manual_extension', note: '', selected_at: '2026-09-09T00:00:00Z',
               url: `https://x.com/i/status/${id}` },
});

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: CHROME });
  try {
    const page = await browser.newPage();
    const archived = [];
    let jobs = [job('1', 'retry'), job('2', 'partial'), job('3', 'done'),
                job('4', 'unavailable'), job('5', 'queued')];

    await page.route('**/api/jobs', r => r.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ jobs, service_status: { account_active: true, quota_wait: false } }),
    }));
    await page.route('**/api/archive', r => {
      const body = JSON.parse(r.request().postData());
      archived.push(body.url);
      // Le serveur remet le post en file : le statut change en cours de route.
      jobs = jobs.map(j => (j.url === body.url ? { ...j, status: 'queued' } : j));
      return r.fulfill({ contentType: 'application/json', body: JSON.stringify({ status: 'queued' }) });
    });
    await page.route('**/api/results', r => r.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ destination: '', include_automatic: false, export: { status: 'idle' } }),
    }));

    await page.goto('http://127.0.0.1:18765');

    // Seuls les posts en echec sont comptes : ni « done », ni « queued ».
    await page.waitForFunction(() =>
      document.querySelector('#retry-all')?.textContent === 'Tout réessayer (3)');
    assert.equal(await page.locator('#retry-all').isVisible(), true);

    await page.locator('#retry-all').click();
    await page.waitForFunction(() =>
      /remis en file/.test(document.querySelector('#retry-status').textContent));

    assert.deepEqual(archived.sort(), [
      'https://x.com/i/status/1', 'https://x.com/i/status/2', 'https://x.com/i/status/4',
    ]);
    const status = await page.locator('#retry-status').textContent();
    assert.match(status, /^3 posts remis en file\.$/);

    // Plus rien a reessayer : le bouton reste visible mais devient inerte.
    await page.waitForFunction(() => document.querySelector('#retry-all').disabled === true);
    assert.equal(await page.locator('#retry-all').isVisible(), true);
    assert.equal(await page.locator('#retry-all').textContent(), 'Tout réessayer');
    assert.equal(await page.locator('#retry-all').getAttribute('title'), 'Aucun post en échec');

    // Un serveur qui refuse ne doit pas laisser le bouton bloque.
    jobs = [job('9', 'retry')];
    await page.waitForFunction(() => document.querySelector('#retry-all').disabled === false);
    await page.unroute('**/api/archive');
    await page.route('**/api/archive', r => r.fulfill({ status: 500, body: 'non' }));
    await page.waitForFunction(() =>
      document.querySelector('#retry-all')?.textContent === 'Tout réessayer (1)');
    await page.locator('#retry-all').click();
    await page.waitForFunction(() =>
      /refusé/.test(document.querySelector('#retry-status').textContent));
    assert.equal(await page.locator('#retry-all').isDisabled(), false);

    console.log('PASS: le bouton ne cible que les posts en echec, affiche sa progression, '
                + 'reste visible mais inerte quand la file est saine, et se debloque apres un refus.');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
