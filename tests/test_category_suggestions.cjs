// Données synthétiques : aucun service réel, cookie ou appel à X.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'playwright');
const root=path.resolve(__dirname,'..');
const apiNamespace=process.env.EXTENSION_API==='browser'?'browser':'chrome';
const names=['Économie','Santé',...Array.from({length:15},(_,i)=>'Recherche '+(i+1))];
(async()=>{
 // Le service worker doit distinguer le rangement du post du dernier choix global.
 let listener;
 const chrome={i18n:{getMessage:k=>k},runtime:{onMessage:{addListener:f=>listener=f},onInstalled:{addListener(){}},getURL:()=> 'chrome-extension://test/'},
 storage:{local:{get:async()=>({bridgeToken:'test',category:'Santé',recentCategories:['Santé','Économie']}),set:async()=>{}}},
 contextMenus:{onClicked:{addListener(){}}}};
 vm.runInNewContext(fs.readFileSync(path.join(root,'extension/background.js'),'utf8'),{[apiNamespace]:chrome,URL,AbortSignal,fetch:async()=>({ok:true,status:200,json:async()=>({default:'Sans catégorie',categories:names.map(category=>({category}))})})});
 const answer=await new Promise(resolve=>listener({type:'RECENT',category:'Économie'},{url:'https://x.com/home'},resolve));
 assert.equal(answer.result.current,'Économie');assert.equal(answer.result.others.length,2);
 assert.equal(answer.result.known.length,17);
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH});
 try{
 const page=await browser.newPage({viewport:{width:900,height:700}});const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/*',route=>route.fulfill({contentType:'text/html',body:`<html><body><article data-testid="tweet"><div data-testid="User-Name"><a href="https://x.com/demo/status/123"><time>Maintenant</time></a></div><p>Post de démonstration</p><div role="group"><button data-testid="like">J’aime</button><button data-testid="retweet">Republier</button></div></article></body></html>`}));
 await page.goto('https://x.com/home');
 const messages=JSON.parse(fs.readFileSync(path.join(root,'extension/_locales/fr/messages.json'),'utf8'));
 await page.evaluate(({messages,names,apiNamespace})=>{
   window.moves=[];window.failMove=false;
   window[apiNamespace]={i18n:{getMessage:(key,args=[])=>{const entry=messages[key];let text=entry?.message||key;for(const [name,value] of Object.entries(entry?.placeholders||{}))text=text.replace('$'+name+'$',args[Number(value.content.slice(1))-1]);return text;}},
    storage:{local:{get:async()=>({includeReplies:false})}},runtime:{id:'test',sendMessage:async message=>{
     let result={};
     if(message.type==='ARCHIVE')result={status:'queued',selection:{category:'Économie'}};
     if(message.type==='RECENT')result={current:'Santé',others:names,known:names};
     if(message.type==='MOVE'){if(window.failMove)return {ok:false,error:'test'};window.moves.push(message.category);result={category:message.category};}
     if(message.type==='STATUS')result={status:'done'};
     return {ok:true,result};
    }}};
 },{messages,names,apiNamespace});
 await page.addStyleTag({path:path.join(root,'extension/content.css')});
 await page.addScriptTag({path:path.join(root,'extension/content.js')});
 await page.locator('.zevent-archive-button').click();
 const bar=page.locator('.zevent-cat-bar');await bar.waitFor();
 assert.equal(await bar.locator('.zevent-cat-label').textContent(),'Catégorie : Économie');
 assert.equal(await bar.locator('.zevent-cat-choice:not(.zevent-cat-new)').count(),2);
 await bar.locator('.zevent-cat-new').click();
 const input=bar.locator('input');
 assert.equal(await bar.locator('[role=option]').count(),5);
 await input.fill('econ');
 assert.deepEqual(await bar.locator('[role=option]').allTextContents(),['Économie','Créer « econ »']);
 if(process.env.UI_SCREENSHOTS){fs.mkdirSync(process.env.UI_SCREENSHOTS,{recursive:true});await bar.screenshot({path:path.join(process.env.UI_SCREENSHOTS,'categories-recherche.png')});}
 await input.press('ArrowDown');await input.press('Enter');
 await page.waitForFunction(()=>window.moves.length===1);
 assert.deepEqual(await page.evaluate(()=>window.moves),['Économie']);
 assert.match(await bar.locator('.zevent-cat-label').textContent(),/Rangé dans Économie/);
 // Nouvelle archive : saisie sans accent, exact existant et reprise après échec.
 await page.locator('time').evaluate(n=>n.parentElement.href='https://x.com/demo/status/456');
 await page.locator('.zevent-archive-button[data-tweet-id="456"]').click();await bar.locator('.zevent-cat-new').click();
 await input.fill('ECONOMIE');assert.equal(await bar.locator('[role=option]').count(),1);
 await page.evaluate(()=>window.failMove=true);await input.press('Enter');
 await page.waitForFunction(()=>document.querySelector('.zevent-cat-label').textContent==='Rangement impossible');
 assert.equal(await input.isEnabled(),true);
 await page.evaluate(()=>window.failMove=false);
 await input.fill('Projet neuf');assert.deepEqual(await bar.locator('[role=option]').allTextContents(),['Créer « Projet neuf »']);
 await bar.locator('[role=option]').click();await page.waitForFunction(()=>window.moves.length===2);
 assert.deepEqual(await page.evaluate(()=>window.moves),['Économie','Projet neuf']);
 await page.locator('time').evaluate(n=>n.parentElement.href='https://x.com/demo/status/789');
 await page.locator('.zevent-archive-button[data-tweet-id="789"]').click();await bar.locator('.zevent-cat-new').click();
 await input.fill('Recherche');assert.equal(await bar.locator('[role=option]').count(),6);
 await page.setViewportSize({width:390,height:700});
 assert.ok(await bar.evaluate(n=>{const b=n.getBoundingClientRect();return b.left>=0&&b.right<=innerWidth;}));
 await input.press('Escape');assert.equal(await bar.locator('input').count(),0);
 assert.deepEqual(errors,[]);
 console.log('PASS: catégorie réelle, 17 catégories / 2 raccourcis / 5 suggestions, recherche sans accents, clavier, création, échec récupérable et mobile.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
