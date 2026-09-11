// Synthetic dashboard only. No filesystem changes, native dialog or live service.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'playwright');
const root=path.join(__dirname,'..','manual_ui');
const json=body=>({contentType:'application/json',body:JSON.stringify(body)});
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH});
 try{
  for(const lang of ['fr','en']){
   const page=await browser.newPage({viewport:{width:390,height:800}}),errors=[],moves=[];
   let state={path:'C:\\Archives',status:'idle'},openCount=0,pathReads=0,postReads=0,offline=false;
   page.on('pageerror',error=>errors.push(error.message));
   await page.route('http://archive.test/**',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(url.pathname==='/api/storage')return offline?route.abort():route.fulfill(json(state));
    if(url.pathname==='/api/storage/open'){openCount++;return route.fulfill(json({ok:true}));}
    if(url.pathname==='/api/storage/choose-folder')return route.fulfill(json({destination:'D:\\Archives recherche',cancelled:false}));
    if(url.pathname==='/api/storage/move'){moves.push(request.postDataJSON());state={...state,status:'copying',files_done:1,files_total:4};return route.fulfill({...json(state),status:202});}
    if(url.pathname==='/api/posts'){postReads++;return route.fulfill(json({items:[],total:0,page:1,size:20,pages:0,authors:[],categories:[]}));}
    if(url.pathname==='/api/jobs')return route.fulfill(json({jobs:[],service_status:{}}));
    if(url.pathname==='/api/paths'){pathReads++;return route.fulfill(json({data:state.path,posts:state.path+'\\posts',extension:'extension'}));}
    if(url.pathname==='/api/results')return route.fulfill(json({destination:'D:\\Exports',include_automatic:false,export:{status:'idle'}}));
    const filename=url.pathname==='/'?'index.html':url.pathname.slice(1);
    if(!['index.html','ui.js','ui.css','strings.js'].includes(filename))return route.fulfill({status:404,body:''});
    return route.fulfill({contentType:filename.endsWith('.js')?'text/javascript':filename.endsWith('.css')?'text/css':'text/html',body:fs.readFileSync(path.join(root,filename),'utf8')});
   });
   await page.goto('http://archive.test/?lang='+lang);
   await page.waitForFunction(()=>document.querySelector('#storage-path').textContent==='C:\\Archives');
   assert.equal(await page.locator('#storage-settings').getAttribute('open'),null);
   await page.locator('#storage-settings>summary').click();
   await page.locator('#storage-open').click();assert.equal(openCount,1);
   await page.locator('#storage-edit').click();
   assert.equal(await page.locator('#storage-confirm').isEnabled(),false);
   await page.locator('#storage-choose').click();
   assert.equal(await page.locator('#storage-destination').inputValue(),'D:\\Archives recherche');
   assert.equal(moves.length,0,'choosing a folder never starts a move');
   assert.match(await page.locator('.storage-review').textContent(),lang==='fr'?/précédent est conservé/:/previous folder is kept/);
   await page.locator('#storage-confirm').click();
   await page.waitForFunction(()=>document.querySelector('#storage-status').textContent.includes('1 / 4'));
   assert.deepEqual(moves,[{destination:'D:\\Archives recherche'}]);
   assert.equal(await page.locator('#storage-confirm').isEnabled(),false);
   assert.equal(await page.locator('#storage-destination').isEnabled(),false);
   offline=true;await page.evaluate(()=>updateStorage());
   assert.equal(await page.locator('#storage-status').getAttribute('data-state'),'reconnecting');
   assert.equal(await page.locator('#storage-path').textContent(),'C:\\Archives');
   offline=false;state={...state,status:'verifying',files_done:4};await page.evaluate(()=>updateStorage());
   assert.match(await page.locator('#storage-status').textContent(),lang==='fr'?/Vérification/:/Checking/);
   const oldReads={pathReads,postReads};
   state={path:'D:\\Archives recherche',status:lang==='fr'?'done':'idle',source_retained:true};await page.evaluate(()=>updateStorage());
   assert.equal(await page.locator('#storage-path').textContent(),state.path);
   assert.equal(await page.locator('#path-data').textContent(),state.path);
   assert.equal(await page.locator('#storage-move').isVisible(),false);
   assert(pathReads>oldReads.pathReads&&postReads>oldReads.postReads);
   assert.equal(await page.locator('#storage-status').getAttribute('data-state'),'success');
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
   assert.deepEqual(errors,[]);
   await page.close();
  }
  console.log('PASS storage UI FR/EN: explicit review, mocked open/picker/move, progress, reconnect, path and library refresh, compact mobile.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
