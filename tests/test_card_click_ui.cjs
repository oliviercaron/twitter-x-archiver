// Ouverture du lecteur video depuis la carte d'archive.
// Les appels reseau sont interceptes : aucune donnee reelle n'est lue et
// aucune requete n'est envoyee a X.
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

const item = {
  id: '1234567890', author: 'YomiDenzel96', posted: '2026-09-01T12:00:00Z',
  archived: '2026-09-02T12:00:00Z', text: 'Un post de test avec une video.',
  views: 12000, likes: 300, reposts: 40, status: 'done',
  thumb: 'media/faux.jpg', video: 'media/faux.mp4', duration: 42,
};
const posts = {
  items: [{ ...item, author: null, thumb: null, video: null }], total: 1, page: 1, size: 20, pages: 1,
  authors: [{ author: 'YomiDenzel96', count: 1 }],
  span: { from: '2026-01-01', to: '2026-09-10' },
};
let jobs = [{ tweet_id: item.id, status: 'fetching', updated_at: '2026-09-10T10:00:00Z' }];
const json = body => ({ contentType: 'application/json', body: JSON.stringify(body) });

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: CHROME });
  try {
    const page = await browser.newPage();
    await page.route('**/api/posts*', r => r.fulfill(json(posts)));
    await page.route('**/api/jobs', r => r.fulfill(json({ jobs, service_status: { account_active: true } })));
    await page.route('**/api/results', r => r.fulfill(json({ destination: '', include_automatic: false, export: { status: 'idle' } })));
    // La vignette et la video n'existent pas : seule la bascule est testee.
    await page.route('**/media/**', r => r.fulfill({ status: 404, body: '' }));

    await page.goto('http://127.0.0.1:18765');
    await page.waitForSelector('.job');
    assert.equal(await page.locator('.job .play').count(), 0);
    // Le téléchargement finit après la création de la carte : le sondage seul
    // doit installer la vignette, le compte et les clics, sans navigation.
    posts.items = [item];
    jobs = [{ tweet_id: item.id, status: 'done', updated_at: '2026-09-10T10:00:01Z' }];
    await page.waitForSelector('.job[data-playable="1"]');
    assert.equal(await page.locator('.job a').textContent(), '@YomiDenzel96');
    const closed = () => page.$eval('#player', n => n.hidden);
    assert.equal(await closed(), true, 'le lecteur doit partir ferme');

    // Le clic porte sur toute la carte, pas seulement sur la vignette.
    await page.locator('.job .text').click();
    await page.waitForFunction(() => document.querySelector('#player').hidden === false);
    await page.locator('.job .metrics').click();
    await page.waitForFunction(() => document.querySelector('#player').hidden === true);

    // La vignette bascule une seule fois : le clic ne compte pas deux fois.
    await page.locator('.job .play').click();
    await page.waitForFunction(() => document.querySelector('#player').hidden === false);
    await page.locator('.job .play').click();
    await page.waitForFunction(() => document.querySelector('#player').hidden === true);

    // Le lien de l'auteur navigue, il n'ouvre pas le lecteur.
    const href = await page.$eval('.job a', a => a.href);
    assert.match(href, /x\.com\/i\/status\/1234567890/);
    await page.$eval('.job a', a => a.removeAttribute('target'));
    await page.evaluate(() => document.addEventListener('click', e => {
      if (e.target.closest('a')) e.preventDefault();
    }, true));
    await page.locator('.job a').click();
    await page.waitForTimeout(400);
    assert.equal(await closed(), true, 'le lien ne doit pas ouvrir le lecteur');

    // La croix de suppression s'arme sans ouvrir le lecteur.
    await page.$eval('.job .wipe', b => { b.style.display = 'inline-flex'; });
    await page.locator('.job .wipe').click();
    await page.waitForTimeout(400);
    assert.equal(await closed(), true, 'la croix ne doit pas ouvrir le lecteur');
    assert.equal(await page.$eval('.job .wipe', b => b.dataset.armed), '1');

    // Selectionner le texte d'une carte n'est pas un clic.
    await page.evaluate(() => {
      const node = document.querySelector('.job .text');
      const range = document.createRange(); range.selectNodeContents(node);
      const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range);
      node.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await page.waitForTimeout(300);
    assert.equal(await closed(), true, 'une selection ne doit pas ouvrir le lecteur');

    // Un autre archivage entièrement terminé entre deux sondages apparaît
    // aussi. Le lecteur ouvert et les champs saisis doivent rester intacts.
    await page.evaluate(() => getSelection().removeAllRanges());
    await page.locator('.job .play').click();
    await page.evaluate(() => { window.savedPlayer=document.querySelector('#player'); });
    await page.locator('#urls').fill('https://x.com/i/status/987');
    const second = { ...item, id: '2345678901' };
    posts.items = [second, item]; posts.total = 2;
    jobs.unshift({ tweet_id: second.id, status: 'done', updated_at: '2026-09-10T10:00:02Z' });
    await page.waitForSelector('.job[data-id="2345678901"]');
    assert.equal(await page.locator('#urls').inputValue(), 'https://x.com/i/status/987');
    assert.equal(await page.evaluate(() => savedPlayer===document.querySelector('#player')&&!savedPlayer.hidden), true);
    assert.equal(await page.locator('#player').getAttribute('src'), '/'+item.video);
    console.log('PASS: médias prêts après téléchargement, nouveaux archivages rapides détectés, lecteur et saisie préservés ; clics, lien et suppression intacts.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
