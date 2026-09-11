// Exercise the real feed buttons with synthetic posts and a mocked companion.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'playwright');
const extension = path.resolve(__dirname, '../extension');
const messages = JSON.parse(fs.readFileSync(path.join(extension, '_locales/fr/messages.json'), 'utf8'));
const article = (id, quote = '') => `<article data-testid="tweet" id="post-${id}">
  <div data-testid="User-Name"><a href="https://x.com/demo/status/${id}"><time>Today</time></a></div>
  <p>Synthetic post</p>${quote}
  <div role="group" style="display:flex;gap:10px"><button data-testid="reply">Reply</button>
  <button data-testid="retweet">Repost</button><button data-testid="like">Like</button></div></article>`;

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => route.fulfill({ contentType: 'text/html', body:
      article('111', '<div role="link"><div data-testid="User-Name"><a href="https://x.com/demo/status/222"><time>Yesterday</time></a></div>Quoted post</div>') + article('222') }));
    await page.addInitScript(({ messages }) => {
      window.deleted = [];
      window.rejectDeletion = false;
      const states = { '111': 'done', '222': 'done' };
      window.chrome = {
        i18n: { getMessage: key => messages[key]?.message || key },
        storage: { local: { get: async () => ({}) } },
        runtime: { id: 'synthetic-extension', sendMessage: async message => {
          if (message.type === 'STATES') return { ok: true, result: states };
          if (message.type === 'DELETE') {
            if (window.rejectDeletion) return { ok: false, code: 'shared', error: 'Legacy companion refusal' };
            window.deleted.push(message.id);
            delete states[message.id];
            return { ok: true, result: { deleted: message.id, left: [], preserved_shared: 3 } };
          }
          throw Error('Unexpected message: ' + message.type);
        } },
      };
    }, { messages });
    await page.goto('https://x.com/home');
    await page.addStyleTag({ path: path.join(extension, 'content.css') });
    await page.addScriptTag({ path: path.join(extension, 'content.js') });
    const selected = page.locator('#post-111 .zevent-delete-button');
    await page.locator('#post-111 .zevent-archive-button[data-state="done"]').waitFor();
    await page.locator('#post-111 .zevent-archive-wrap').hover();
    await selected.click();
    assert.deepEqual(await page.evaluate(() => window.deleted), [], 'first click only asks for confirmation');
    await selected.click();
    await page.locator('#post-111 .zevent-archive-button[data-state="idle"]').waitFor();
    assert.deepEqual(await page.evaluate(() => window.deleted), ['111'], 'the quoted post is never the deletion target');
    assert.equal(await page.locator('#post-222 .zevent-archive-button').getAttribute('data-state'), 'done');
    await page.evaluate(() => { window.rejectDeletion = true; });
    const other = page.locator('#post-222 .zevent-delete-button');
    await page.locator('#post-222 .zevent-archive-wrap').hover();
    await other.click();
    await other.click();
    await page.waitForFunction(() => document.querySelector('#post-222 .zevent-delete-button').textContent.includes('Média partagé'));
    assert.equal(await page.locator('#post-222 .zevent-archive-button').getAttribute('data-state'), 'done');
    assert.deepEqual(errors, []);
    console.log('Feed deletion: confirmation, quoted-post targeting, retained shared files and legacy error display passed.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
