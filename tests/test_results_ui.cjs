// Smoke test of the actual local dashboard; the native dialog is mocked.
// The export uses existing archived data and never requests X.
const assert=require('node:assert/strict');
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
if(process.env.ALLOW_ARCHIVE_EXPORT_TEST!=='1'||!process.env.RESULTS_TEST_DIR)throw Error('Manual integration test: set ALLOW_ARCHIVE_EXPORT_TEST=1 and RESULTS_TEST_DIR to an explicit export folder.');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:CHROME});
 try{
  const page=await browser.newPage();
  const chosen=process.env.RESULTS_TEST_DIR;
  await page.route('**/api/results/choose-folder',r=>r.fulfill({contentType:'application/json',body:JSON.stringify({destination:chosen,cancelled:false})}));
  await page.goto('http://127.0.0.1:18765');
  await page.locator('#choose-folder').click();
  await page.waitForFunction(value=>document.querySelector('#result-folder').value===value,chosen);
  assert.equal(await page.locator('#include-automatic').isChecked(),false);
  const previous=await page.evaluate(async()=> (await api('/api/results')).export.path);
  const submitted=page.waitForResponse(r=>r.url().endsWith('/api/results')&&r.request().method()==='POST');
  await page.locator('#export-button').click();
  await submitted;
  await page.waitForFunction(async prior=>{const value=(await api('/api/results')).export;return value.path!==prior&&value.status==='done'&&value.csv_verified===true;},previous,{timeout:180000});
  await page.evaluate(()=>updateResults());
  await page.waitForFunction(()=>/fichiers copiés/.test(document.querySelector('#export-status').textContent));
  const text=await page.locator('#export-status').textContent();
  assert.match(text,/\d+ tweets/);
  assert.doesNotMatch(text,/ATTENTION/);
  await page.reload();
  await page.waitForFunction(value=>document.querySelector('#result-folder').value===value,chosen);
  console.log('PASS: folder choice response, persisted destination, default scope and completed real CSV/media export.');
  console.log(text);
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
