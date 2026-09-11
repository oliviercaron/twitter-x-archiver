// Real MV3 extension in isolated Chromium; synthetic posts, no X requests or cookies.
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
function loadPlaywright() {
  for (const candidate of [process.env.PLAYWRIGHT_MODULE, process.env.PLAYWRIGHT_PATH, 'playwright'].filter(Boolean)) {
    try { return require(candidate); } catch {}
  }
  throw Error('Playwright introuvable. npm i -D playwright, ou definissez PLAYWRIGHT_PATH.');
}
const { chromium } = loadPlaywright();
const root = path.resolve(__dirname, '..');
const token = 'test-only-local-bridge-token-000000000000';
const jobs = new Map();
const requests = [];
const deletions = [];
const moves = [];
let known = [{category:'ZEVENT 2026',count:12},{category:'Moments clippables',count:3},
             {category:'Hors corpus',count:1}];
const server = http.createServer(async (req, res) => {
  res.setHeader('Content-Type', 'application/json');
  if (req.url === '/') {
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    return res.end(`<meta name="bridge-token" content="${token}"><button id="pair-extension">Connecter</button><button id="use-session">Session</button><p id="pair-status"></p>`);
  }
  if (req.headers.authorization !== `Bearer ${token}`) {res.statusCode=403;return res.end('{}');}
  if (req.url === '/api/jobs') return res.end(JSON.stringify({jobs:[...jobs.values()]}));
  if (req.url === '/api/categories') return res.end(JSON.stringify({categories:known,default:'ZEVENT 2026'}));
  if (req.method === 'POST' && req.url === '/api/category') {
    let body='';for await (const part of req) body+=part;
    const data=JSON.parse(body);moves.push(data);
    return res.end(JSON.stringify({tweet_id:data.tweet_id,category:data.category.trim()}));
  }
  if (req.url === '/api/states') return res.end(JSON.stringify(Object.fromEntries([...jobs].map(([id,j])=>[id,j.status]))));
  if (req.method === 'POST' && req.url === '/api/session') return res.end('{"session_present":true}');
  if (req.method === 'POST' && req.url === '/api/delete') {
    let body='';for await (const part of req) body+=part;
    const id=JSON.parse(body).tweet_id;
    deletions.push(id);jobs.delete(id);
    return res.end(JSON.stringify({deleted:id,left:[]}));
  }
  if (req.method === 'POST' && req.url === '/api/archive') {
    let body='';for await (const part of req) body+=part;
    const data=JSON.parse(body);requests.push(data);
    const id=data.url.match(/status\/(\d+)/)[1];
    const job={tweet_id:id,url:data.url,status:'queued',
               selection:{mode:data.mode,note:data.note,category:(data.category||'').trim()||'ZEVENT 2026'}};
    jobs.set(id,job);res.statusCode=202;return res.end(JSON.stringify(job));
  }
  const job=jobs.get(req.url.split('/').pop());
  if(job)return res.end(JSON.stringify({...job,status:'done'}));
  res.statusCode=404;res.end('{}');
});
function detail(id) {
 // Barre d'un post seul : chaque action dans son propre conteneur, repartis
 // par space-between, avec un bouton de partage en dernier.
 return `<article data-testid="tweet" id="post-${id}"><div data-testid="User-Name">Auteur <a href="https://x.com/demo/status/${id}"><time>2 min</time></a></div>`
  +`<p>Post seul, données fictives.</p>`
  +`<div role="group" style="display:flex;justify-content:space-between;align-items:stretch;width:600px;min-height:48px">`
  +`<div><button data-testid="reply">Répondre</button></div>`
  +`<div><button data-testid="retweet">Republier</button></div>`
  +`<div><button data-testid="like">J’aime</button></div>`
  +`<div><button data-testid="bookmark">Signet</button></div>`
  +`<div><button data-testid="share">Partager</button></div>`
  +`</div></article>`;
}

function detailSansSignet(id) {
 return `<article data-testid="tweet" id="post-${id}"><div data-testid="User-Name">Auteur <a href="https://x.com/demo/status/${id}"><time>2 min</time></a></div>`
  +`<p>Post sans signet, données fictives.</p>`
  +`<div role="group" style="display:flex;justify-content:space-between;width:600px;min-height:48px">`
  +`<div><button data-testid="reply">Répondre</button></div>`
  +`<div><button data-testid="retweet">Republier</button></div>`
  +`<div><button data-testid="like">J’aime</button></div>`
  +`<div><button data-testid="share">Partager</button></div>`
  +`</div></article>`;
}

function article(id, quote=false) {
 return `<article data-testid="tweet" id="post-${id}"><div data-testid="User-Name">Auteur de démonstration <a href="https://x.com/demo/status/${id}"><time>2 min</time></a></div><p>Démonstration — données fictives. Ce post est choisi manuellement.</p>${quote?'<div role="link"><div data-testid="User-Name">Citation <a href="https://x.com/quote/status/999"><time>Hier</time></a></div></div>':''}<div role="group"><button data-testid="reply">Répondre</button><button data-testid="retweet">Republier</button><button data-testid="like">J’aime</button></div></article>`;
}
(async()=>{
 await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(18765,'127.0.0.1',resolve);});
 let context;
 try {
  const extension=path.join(root,'extension');
  const profile=path.join(root,'work','extension-test-profile-v1_1');
  context=await chromium.launchPersistentContext(profile,{channel:'chromium',headless:true,
   ...((process.env.CHROMIUM_EXECUTABLE||process.argv[2])?{executablePath:process.env.CHROMIUM_EXECUTABLE||process.argv[2]}:{}),
   args:[`--disable-extensions-except=${extension}`,`--load-extension=${extension}`]});
  let worker=context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
  const extensionId=new URL(worker.url()).host;
  await worker.evaluate(()=>chrome.storage.local.set({includeReplies:false}));
  await context.route('https://x.com/**',route=>route.fulfill({contentType:'text/html',body:`<!doctype html><html lang="fr"><body><h1>Test d’archivage — données fictives</h1>${article('123',true)}</body></html>`}));
  const pair=await context.newPage();await pair.goto('http://127.0.0.1:18765');
  // Plus aucun clic : l'appairage se declare par un attribut du DOM.
  await pair.waitForFunction(()=>document.documentElement.dataset.zeventPaired==='1');
  const feed=await context.newPage();await feed.goto('https://x.com/home');
  const button=feed.locator('.zevent-archive-button');await button.waitFor();
  assert.equal(await button.count(),1);assert.equal(await button.getAttribute('data-tweet-id'),'123');
  await button.click();await feed.locator('.zevent-archive-button[data-state="done"]').waitFor();
  assert.equal(requests[0].url,'https://x.com/i/status/123');
  assert.equal(requests[0].mode,'manual_extension');
  // A virtualized timeline can recycle an article without removing its element.
  await feed.locator('#post-123 [data-testid="User-Name"] a').first().evaluate(a=>a.href='https://x.com/demo/status/456');
  await feed.locator('.zevent-archive-button[data-tweet-id="456"]').waitFor();
  assert.equal(await button.count(),1);await button.click();
  await feed.locator('.zevent-archive-button[data-state="done"]').waitFor();
  assert.equal(requests[1].url,'https://x.com/i/status/456');
  await feed.evaluate(html=>document.body.insertAdjacentHTML('beforeend',html),article('789'));
  await feed.locator('.zevent-archive-button[data-tweet-id="789"]').waitFor();
  assert.equal(await button.count(),2);
  const popup=await context.newPage();await popup.goto(`chrome-extension://${extensionId}/popup.html`);
  await popup.locator('#url').fill('https://x.com/demo/status/321?s=20');
  await popup.locator('#note').fill('Sélection explicite de test');
  await popup.locator('#include-replies').check();
  await popup.locator('#archive').click();
  await popup.waitForFunction(()=>document.querySelector('#status').textContent.trim().length>0);
  assert.equal(requests[2].url,'https://x.com/i/status/321');
  assert.equal(requests[2].note,'Sélection explicite de test');
  assert.equal(requests[2].include_replies,true);
  assert.equal(requests[0].include_replies,false);
  await feed.locator('.zevent-archive-button[data-tweet-id="789"]').click();
  await feed.locator('.zevent-archive-button[data-tweet-id="789"][data-state="done"]').waitFor();
  assert.equal(requests[3].include_replies,true);
  await feed.locator('.zevent-archive-button[data-tweet-id="456"]').click();
  await feed.locator('.zevent-archive-button[data-tweet-id="456"][data-state="queued"]').waitFor();
  await feed.locator('.zevent-archive-button[data-tweet-id="456"][data-state="done"]').waitFor();
  assert.equal(requests[4].include_replies,true);
  assert.equal(requests.some(r=>r.url.endsWith('/999')),false);

  // Un onglet neuf doit reconnaitre un post deja archive, sans aucun clic.
  const avant=requests.length;
  const revenu=await context.newPage();await revenu.goto('https://x.com/home');
  const connu=revenu.locator('.zevent-archive-button[data-tweet-id="123"]');
  await connu.waitFor();
  await revenu.locator('.zevent-archive-button[data-tweet-id="123"][data-state="done"]').waitFor();
  const titreArchive=await connu.getAttribute('title');
  const titreNeuf=await revenu.evaluate(()=>chrome?.i18n?.getMessage('tipArchive'));
  assert.ok(titreArchive&&titreArchive!==titreNeuf,'un post archive porte une infobulle distincte');
  assert.equal(requests.length,avant,'la reconnaissance ne doit rien archiver');

  // Un post inconnu du serveur reste neutre dans le meme onglet.
  await revenu.evaluate(html=>document.body.insertAdjacentHTML('beforeend',html),article('555'));
  const neuf=revenu.locator('.zevent-archive-button[data-tweet-id="555"]');
  await neuf.waitFor();
  assert.equal(await neuf.getAttribute('data-state'),'idle');

  // Le bandeau de rangement : deux categories recentes, apres le clic seulement.
  await feed.evaluate(html=>document.body.insertAdjacentHTML('beforeend',html),article('654'));
  const range=feed.locator('.zevent-archive-wrap[data-tweet-id="654"]');
  await range.waitFor();
  assert.equal(await feed.locator('.zevent-cat-bar[data-tweet-id="654"]').count(),0,
    'rien ne doit s afficher avant le clic');
  await feed.locator('.zevent-archive-button[data-tweet-id="654"]').click();
  const bandeau=feed.locator('.zevent-cat-bar[data-tweet-id="654"]');
  await bandeau.waitFor();
  const bandeauChoix=bandeau.locator('.zevent-cat-choice:not(.zevent-cat-new)');
  // Le bandeau vit dans le body, pas dans l'article : X decoupe ses cellules.
  assert.equal(await bandeau.evaluate(node=>node.parentElement===document.body),true,
    'le bandeau doit etre pose sur le body pour passer au-dessus du fil');
  const propositions=await bandeauChoix.allTextContents();
  assert.equal(await feed.locator('.zevent-cat-bar .zevent-cat-new').count(),1,
    'creer une categorie doit rester possible depuis X');
  assert.equal(propositions.length,2,'deux propositions, pas davantage');
  assert.ok(!propositions.includes('ZEVENT 2026'),
    'la categorie qui vient de servir n est pas reproposee');

  // Un clic range le post, sans repasser par X.
  const appels=requests.length;
  await bandeauChoix.first().click();
  await feed.waitForFunction(()=>/Rang|Moved/.test(
    document.querySelector('.zevent-cat-bar[data-tweet-id="654"] .zevent-cat-label')?.textContent||''));
  assert.equal(requests.length,appels,'ranger ne doit rien redemander a X');
  assert.deepEqual(moves,[{tweet_id:'654',category:propositions[0]}]);
  await bandeau.waitFor({state:'detached'});

  // Le dernier rangement devient la categorie du post suivant.
  await feed.evaluate(html=>document.body.insertAdjacentHTML('beforeend',html),article('987'));
  await feed.locator('.zevent-archive-button[data-tweet-id="987"]').waitFor();
  await feed.locator('.zevent-archive-button[data-tweet-id="987"]').click();
  await feed.locator('.zevent-archive-button[data-tweet-id="987"][data-state="done"]').waitFor();
  assert.equal(requests.at(-1).category,propositions[0]);

  // La croix n'existe que pour une archive, et jamais pour un post neuf.
  const wrapNeuf=revenu.locator('.zevent-archive-wrap[data-tweet-id="555"]');
  assert.equal(await wrapNeuf.getAttribute('data-deletable'),'0');
  await wrapNeuf.hover();
  assert.equal(await wrapNeuf.locator('.zevent-delete-button').isVisible(),false,
    'un post non archive ne doit pas proposer de suppression');

  // Sur une archive : survol, puis deux clics distincts.
  const wrapConnu=revenu.locator('.zevent-archive-wrap[data-tweet-id="123"]');
  assert.equal(await wrapConnu.getAttribute('data-deletable'),'1');
  await wrapConnu.hover();
  const croix=wrapConnu.locator('.zevent-delete-button');
  await croix.waitFor({state:'visible'});
  assert.equal(await croix.getAttribute('data-armed'),'0');

  await croix.click();                       // premier clic : armement seulement
  await revenu.waitForFunction(()=>document.querySelector('.zevent-archive-wrap[data-tweet-id="123"] .zevent-delete-button')?.dataset.armed==='1');
  assert.equal(deletions.length,0,'le premier clic ne doit rien supprimer');
  assert.match(await croix.textContent(),/^Supprimer\s*\?$/);

  await croix.click();                       // second clic : suppression
  await revenu.locator('.zevent-archive-button[data-tweet-id="123"][data-state="idle"]').waitFor();
  assert.deepEqual(deletions,['123']);
  assert.equal(await wrapConnu.getAttribute('data-deletable'),'0');

  // L'armement retombe quand le curseur s'en va, sans rien supprimer.
  const wrap789=feed.locator('.zevent-archive-wrap[data-tweet-id="789"]');
  await wrap789.hover();
  const croix789=wrap789.locator('.zevent-delete-button');
  await croix789.waitFor({state:'visible'});
  await croix789.click();
  await feed.waitForFunction(()=>document.querySelector('.zevent-archive-wrap[data-tweet-id="789"] .zevent-delete-button')?.dataset.armed==='1');
  await feed.locator('h1').hover();
  await feed.waitForFunction(()=>document.querySelector('.zevent-archive-wrap[data-tweet-id="789"] .zevent-delete-button')?.dataset.armed==='0');
  assert.deepEqual(deletions,['123'],'quitter le bouton ne supprime rien');

  // Sur la page d'un post seul, le bouton doit rester dans la serie d'icones,
  // pas etre repousse a l'extremite par la repartition de la barre.
  const seul=await context.newPage();
  await context.route('https://x.com/demo/status/777',route=>route.fulfill({contentType:'text/html',
    body:`<!doctype html><html lang="fr"><body><h1>Post seul</h1>${detail('777')}</body></html>`}));
  await seul.goto('https://x.com/demo/status/777');
  const wrap=seul.locator('.zevent-archive-wrap');
  await wrap.waitFor();
  const position=await seul.evaluate(()=>{
   const group=document.querySelector('[role="group"]');
   const kids=[...group.children];
   const mine=kids.indexOf(document.querySelector('.zevent-archive-wrap'));
   const share=kids.findIndex(k=>k.querySelector('[data-testid="share"]'));
   const like=kids.findIndex(k=>k.querySelector('[data-testid="like"]'));
   const box=document.querySelector('.zevent-archive-wrap').getBoundingClientRect();
   const gbox=group.getBoundingClientRect();
   return {mine,share,like,total:kids.length,
           hauteurBouton:Math.round(box.height),hauteurBarre:Math.round(gbox.height),
           centreBouton:Math.round(box.top+box.height/2),centreBarre:Math.round(gbox.top+gbox.height/2)};
  });
  assert.equal(position.mine,position.like+1,'le bouton suit le j’aime');
  assert.ok(position.mine<position.share,'le bouton reste avant le partage');
  assert.ok(position.hauteurBouton<position.hauteurBarre,'le bouton ne doit pas s etirer sur toute la barre');
  assert.ok(Math.abs(position.centreBouton-position.centreBarre)<=2,'le bouton est centre verticalement');
  await seul.close();

  // Sans signet, la place ne doit pas bouger : toujours juste apres le j'aime.
  const sansSignet=await context.newPage();
  await context.route('https://x.com/demo/status/888',route=>route.fulfill({contentType:'text/html',
    body:`<!doctype html><html lang="fr"><body><h1>Sans signet</h1>${detailSansSignet('888')}</body></html>`}));
  await sansSignet.goto('https://x.com/demo/status/888');
  await sansSignet.locator('.zevent-archive-wrap').waitFor();
  const rang=await sansSignet.evaluate(()=>{
   const kids=[...document.querySelector('[role="group"]').children];
   return {mine:kids.indexOf(document.querySelector('.zevent-archive-wrap')),
           like:kids.findIndex(k=>k.querySelector('[data-testid="like"]'))};
  });
  assert.equal(rang.mine,rang.like+1,'sans signet, la place reste la meme');
  await sansSignet.close();

  // Extension rechargee, onglet X laisse ouvert. Le contexte du script de
  // contenu devient invalide : il doit le dire et se taire, plutot que de
  // remonter « Extension context invalidated » a chaque geste. Ce bloc vient
  // en dernier : apres lui, plus rien ne repond dans cet onglet.
  const erreurs=[];
  feed.on('pageerror',error=>erreurs.push(String(error)));
  worker=context.serviceWorkers()[0]||worker;
  await worker.evaluate(()=>chrome.runtime.reload());
  await feed.waitForFunction(()=>
    document.querySelector('.zevent-archive-button')?.dataset.state==='stale',{timeout:15000});
  const perime=feed.locator('.zevent-archive-button').first();
  assert.match(await perime.textContent(),/Recharg|Reload/);
  assert.equal(await perime.isDisabled(),true);
  await feed.waitForTimeout(3500);
  assert.deepEqual(erreurs,[],'un onglet perime ne doit plus rien jeter');

  console.log('PASS: MV3 pairing, outer post selection, queue/status, recycled and added articles, popup note, persistent discussion option, upgrade of an archived post, recognition of already archived posts on a fresh tab, two-step deletion that disarms on mouse leave, a placement that stays put whether or not the bookmark icon exists, the category bar offered right after archiving, and a tab that says so once the extension is reloaded.');
 } finally {if(context)await context.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
