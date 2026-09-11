// Capture the real interface with synthetic data. No server or X account required.
// APP_ROOT=/path/to/source PLAYWRIGHT_PATH=playwright CHROME_PATH=/path/to/chrome
// node scripts/generate_demo.cjs /path/to/output
// Then: ffmpeg -y -f concat -safe 0 -i frames/frames.txt -filter_complex
// "[0:v]split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a"
// -loop 0 docs/demo.gif
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'playwright');
const root = path.resolve(process.env.APP_ROOT || path.join(__dirname, '..'));
const out = path.resolve(process.argv[2] || path.join(__dirname, '..'));
const frames = path.join(out, 'frames');
const docs = path.join(out, 'docs');
const json = body => ({contentType:'application/json',body:JSON.stringify(body)});
fs.mkdirSync(frames,{recursive:true}); fs.mkdirSync(docs,{recursive:true});

const wrap = body => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 250">${body}</svg>`;
const media = [
  wrap(`<rect width="420" height="250" fill="#173f38"/><g stroke="#3e635a" stroke-width="1"><path d="M40 40H390M40 90H390M40 140H390M40 190H390"/></g><path d="M40 186L94 171L150 157L205 122L259 134L312 79L380 44" fill="none" stroke="#a3d49e" stroke-width="9" stroke-linejoin="round"/><g fill="#dff1ce"><circle cx="40" cy="186" r="7"/><circle cx="150" cy="157" r="7"/><circle cx="259" cy="134" r="7"/><circle cx="380" cy="44" r="7"/></g><text x="40" y="227" fill="#daeadc" font-size="17" font-family="sans-serif">A week of conversations</text>`),
  wrap(`<rect width="420" height="250" fill="#e9dbbd"/><g stroke="#b3a480" stroke-width="3"><path d="M91 74L203 116L323 56M203 116L115 208M203 116L327 194M323 56L327 194"/></g><g fill="#5d8068"><circle cx="203" cy="116" r="34"/><circle cx="91" cy="74" r="22"/><circle cx="323" cy="56" r="21"/><circle cx="115" cy="208" r="20"/><circle cx="327" cy="194" r="25"/></g><g fill="#f5f1dc"><circle cx="203" cy="116" r="13"/><circle cx="91" cy="74" r="8"/><circle cx="323" cy="56" r="8"/></g>`),
  wrap(`<rect width="420" height="250" fill="#dbe7e9"/><circle cx="320" cy="62" r="29" fill="#efc477"/><path d="M0 188L115 76L215 175L299 116L420 200V250H0Z" fill="#809b88"/><path d="M0 217L112 164L222 218L345 157L420 197V250H0Z" fill="#365f50"/><path d="M0 241Q125 206 225 242T420 223V250H0Z" fill="#21483e"/>`),
];
const items = [
  {id:'1000000000000000001',author:'demo_research',category:'Research',status:'done',text:'What happens when a story spreads? A few notes from our latest study on online conversations.',views:12800,likes:284,reposts:61,thumb:'media/sample-0.svg'},
  {id:'1000000000000000002',author:'demo_lab',category:'Research',status:'done',text:'The replies are part of the story too. Mapping a discussion helps us see how people respond to one another.',views:6420,likes:173,reposts:28,thumb:'media/sample-1.svg'},
  {id:'1000000000000000003',author:'demo_community',category:'Community',status:'done',text:'A good day outside, a new walking route, and plenty of ideas to bring back. Keeping this one for later.',views:3100,likes:92,reposts:12,thumb:'media/sample-2.svg'},
].map((item,i)=>({...item,posted:`2026-09-${String(10-i).padStart(2,'0')}T09:30:00Z`,archived:'2026-09-11T10:15:00Z'}));
const categories = ['Research','Community'].map(category=>({category}));

(async()=>{
 const browser = await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH,args:['--lang=en-GB','--accept-lang=en-GB']});
 const page = await browser.newPage({viewport:{width:1100,height:920},locale:'en-GB',timezoneId:'UTC',deviceScaleFactor:1});
 const errors = [], calls = [];
 page.on('pageerror',e=>errors.push(e.message));
 let exportState = {status:'idle'};
 await page.addInitScript(()=>document.addEventListener('DOMContentLoaded',()=>{document.documentElement.dataset.zeventPaired='1';}));
 await page.route('**/*',async route=>{
  const url=new URL(route.request().url());
  if(url.origin!=='http://archive.test')throw Error('Unexpected external request: '+url.origin);
  const pathname=url.pathname;calls.push(pathname);
  if(pathname==='/api/posts'){
   const chosen=url.searchParams.get('categorie');
   const q=url.searchParams.get('q')||'';
   const visible=items.filter(item=>(!chosen||item.category===chosen)&&(!q||item.text.toLowerCase().includes(q.toLowerCase())));
   return route.fulfill(json({items:visible,total:visible.length,page:1,size:20,pages:1,authors:items.map(item=>({author:item.author,count:1})),categories}));
  }
  if(pathname==='/api/jobs')return route.fulfill(json({jobs:[],service_status:{session_present:true,account_active:true}}));
  if(pathname==='/api/paths')return route.fulfill(json({data:'Personal folder / Archivage X / archives',posts:'archives / posts',extension:'extension / chrome'}));
  if(pathname==='/api/storage')return route.fulfill(json({path:'Personal folder / Archivage X / archives',status:'idle'}));
  if(pathname==='/api/results'){
   if(route.request().method()==='POST')exportState={status:'running'};
   return route.fulfill(json({destination:'Documents / My research',include_automatic:false,export:exportState}));
  }
  if(pathname.startsWith('/media/sample-'))return route.fulfill({contentType:'image/svg+xml',body:media[Number(pathname.match(/sample-(\d)/)[1])]});
  const filename=pathname==='/'?'index.html':pathname.slice(1);
  if(!['index.html','ui.js','ui.css','strings.js'].includes(filename))return route.fulfill({status:404,body:''});
  return route.fulfill({contentType:filename.endsWith('.js')?'text/javascript':filename.endsWith('.css')?'text/css':'text/html',body:fs.readFileSync(path.join(root,'src','twitter_x_archiver','ui',filename),'utf8')});
 });
 try{
  await page.goto('http://archive.test/?lang=en');
  await page.waitForSelector('.job');
  await page.waitForFunction(()=>document.querySelector('#setup-text').textContent.includes('Extension connected'));
  await page.evaluate(async()=>{await document.fonts.ready;await Promise.all([...document.images].map(img=>img.decode().catch(()=>{})));});
  // Only the small demo badge and subtitles are additions. Application styles,
  // controls and all interactions below use the production UI unchanged.
  await page.addStyleTag({content:`
    #demo-badge{position:fixed;top:13px;right:18px;color:#7d9086;font:10px Segoe UI,sans-serif;letter-spacing:1.2px;text-transform:uppercase;z-index:10000;pointer-events:none}
    #demo-caption{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);padding:11px 20px;border-radius:99px;background:#173f38;color:#fff;font:600 15px Segoe UI,sans-serif;box-shadow:0 4px 22px #14352f20;z-index:10000;white-space:nowrap;pointer-events:none}
    #demo-cursor{position:fixed;width:21px;height:27px;left:1040px;top:740px;z-index:10001;pointer-events:none;filter:drop-shadow(1px 2px 2px #14352f55)}
  `});
  await page.evaluate(()=>{
   const badge=document.createElement('span');badge.id='demo-badge';badge.textContent='Sample data';document.body.append(badge);
   const caption=document.createElement('div');caption.id='demo-caption';caption.textContent='Your saved posts, in one place';document.body.append(caption);
   const cursor=document.createElement('div');cursor.id='demo-cursor';cursor.innerHTML='<svg viewBox="0 0 24 30" xmlns="http://www.w3.org/2000/svg"><path d="M2 2v23l6-6 5 9 5-3-5-8h8z" fill="#fff" stroke="#14352f" stroke-width="1.6" stroke-linejoin="round"/></svg>';document.body.append(cursor);
  });
  const sequence=[];let index=0;
  async function frame(seconds=.1){
   const name=`frame-${String(index++).padStart(4,'0')}.png`;
   await page.screenshot({path:path.join(frames,name)});
   sequence.push({name,seconds});
  }
  async function caption(text){await page.locator('#demo-caption').evaluate((el,text)=>el.textContent=text,text);}
  let pointer={x:1040,y:740};
  async function move(selector){
   const box=await page.locator(selector).boundingBox();
   const target={x:box.x+box.width/2,y:box.y+box.height/2};
   const start={...pointer};
   for(let step=1;step<=7;step++){
    const t=step/7,e=t*t*(3-2*t);pointer={x:start.x+(target.x-start.x)*e,y:start.y+(target.y-start.y)*e};
    await page.mouse.move(pointer.x,pointer.y);
    await page.locator('#demo-cursor').evaluate((el,p)=>{el.style.left=p.x+'px';el.style.top=p.y+'px';},pointer);
    await frame(.07);
   }
  }
  async function scrollTo(y){
   const start=await page.evaluate(()=>scrollY);
   for(let step=1;step<=10;step++){
    const t=step/10,e=t*t*(3-2*t);await page.evaluate(y=>scrollTo(0,y),start+(y-start)*e);await frame(.065);
   }
  }
  await page.locator('#demo-caption').evaluate(el=>el.hidden=true);
  await page.locator('#demo-cursor').evaluate(el=>el.hidden=true);
  await page.screenshot({path:path.join(docs,'screenshot.png')});
  await page.locator('#demo-caption').evaluate(el=>el.hidden=false);
  await page.locator('#demo-cursor').evaluate(el=>el.hidden=false);
  await frame(2.7);
  await caption('Find what you need with categories');
  await move('#categorie');await frame(.35);
  await page.locator('#categorie').selectOption('Research');
  await page.waitForFunction(()=>document.querySelectorAll('.job').length===2);
  await frame(2.6);
  await move('#clear');await page.locator('#clear').click();
  await page.waitForFunction(()=>document.querySelectorAll('.job').length===3);
  await caption('Keep the posts. Take your data with you.');
  await move('.quick-actions a[href="#results-card"]');
  const exportTop=await page.locator('#results-card').evaluate(el=>el.getBoundingClientRect().top+scrollY);
  await scrollTo(exportTop-140);
  await frame(1.7);
  await caption('Export CSV files and the saved media');
  await move('#export-categories');
  await page.locator('#export-categories').selectOption('Research');await frame(.7);
  await move('#export-button');await page.locator('#export-button').click();
  await page.waitForFunction(()=>document.querySelector('#export-status').textContent.includes('Export running'));
  await frame(.8);
  exportState={status:'done',tweets:2,copied_files:8,missing_files:[],display_path:'Documents / My research / archive_X'};
  await page.evaluate(()=>updateResults());await frame(2.8);
  await caption('Saved locally. Ready when you need it.');
  await scrollTo(0);
  await page.mouse.move(1040,740);pointer={x:1040,y:740};
  await page.locator('#demo-cursor').evaluate(el=>el.hidden=true);
  await frame(2.4);
  if(errors.length)throw Error(errors.join('\n'));
  fs.writeFileSync(path.join(frames,'frames.txt'),sequence.map(({name,seconds})=>`file '${name}'\nduration ${seconds}`).join('\n')+`\nfile '${sequence.at(-1).name}'\n`);
  fs.writeFileSync(path.join(docs,'demo-capture.json'),JSON.stringify({width:1100,height:920,duration:sequence.reduce((n,f)=>n+f.seconds,0),frames:index,synthetic:true,externalRequests:0,uiFiles:['index.html','ui.js','ui.css','strings.js'],scenes:['Archive library','Filter Research category','Export Research to CSV and media','Return to library']},null,2)+'\n');
  console.log(JSON.stringify({frames:index,duration:sequence.reduce((n,f)=>n+f.seconds,0),errors,calls:[...new Set(calls)]},null,2));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
