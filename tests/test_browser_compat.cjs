// Promise-only Firefox/Safari API mocks: no browser profile, network or real data.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const extension=path.join(__dirname,'..','extension');
const source=name=>fs.readFileSync(path.join(extension,name),'utf8');
const settle=()=>new Promise(resolve=>setImmediate(resolve));

async function background(namespace,nativeHost='com.zevent.archive',withMenus=true,companionReady=true){
 let handler,installed,networkDown=false,rejection=null;
 const calls={menus:[],native:[],cookies:[],requests:[]};
 const kept={bridgeToken:'fixture-only-bridge-token-00000000000000',category:'Recherche'};
 const api={
  i18n:{getMessage:key=>key},
  runtime:{id:'fixture',getURL:()=>`${namespace==='browser'?'moz-extension':'chrome-extension'}://fixture/`,
   onMessage:{addListener:fn=>handler=fn},onInstalled:{addListener:fn=>installed=fn},
   sendNativeMessage:async(host,message)=>{calls.native.push({host,message});networkDown=false;return companionReady?{ok:true}:{ok:false,error:'companion_required'};}},
  storage:{local:{get:async keys=>Object.fromEntries((Array.isArray(keys)?keys:[keys]).map(key=>[key,kept[key]])),set:async value=>Object.assign(kept,value)}},
  cookies:{get:async info=>{calls.cookies.push(info);return {value:`fixture-${info.name}`};}},
  action:{setBadgeBackgroundColor:async()=>{},setBadgeText:async()=>{}},tabs:{create:async()=>{}},
 };
 if(withMenus)api.contextMenus={
  removeAll:namespace==='browser'?function(){assert.equal(arguments.length,0);calls.menus.push('remove');return Promise.resolve();}:function(callback){calls.menus.push('remove');callback();},
  create:()=>calls.menus.push('create'),onClicked:{addListener(){}},
 };
 const context={URL,AbortSignal,[namespace]:api,ARCHIVE_CONFIG:{nativeHost},
  fetch:async(url,options)=>{if(networkDown)throw Error('offline');calls.requests.push({url,options});return rejection?{ok:false,status:409,json:async()=>rejection}:{ok:true,status:200,json:async()=>({jobs:[]})};}};
 // When both exist, prefer browser: Firefox's chrome aliases need callbacks.
 if(namespace==='browser')context.chrome=new Proxy({},{get(){throw Error('Wrong API namespace');}});
 vm.runInNewContext(source('background.js'),context);
 const send=(message,url=api.runtime.getURL('')+'popup.html')=>new Promise(resolve=>{
  assert.equal(handler(message,{url},resolve),true,'asynchronous sendResponse remains open');
 });
 installed();await settle();
 assert.deepEqual(calls.menus,withMenus?['remove','create']:[]);
 assert.equal((await send({type:'JOBS'})).ok,true);
 networkDown=true;
 const restarted=await send({type:'JOBS'});
 assert.equal(restarted.ok,companionReady,'native start retries only when companion is available');
 if(!companionReady)assert.equal(restarted.error,'errorCompanionRequired');
 assert.deepEqual(JSON.parse(JSON.stringify(calls.native)),[{host:nativeHost,message:{type:'start'}}]);
 assert.equal((await send({type:'SESSION'})).ok,true);
 assert.deepEqual(JSON.parse(JSON.stringify(calls.cookies)),[{url:'https://x.com',name:'auth_token'},{url:'https://x.com',name:'ct0'}]);
 assert.equal((await send({type:'SESSION'},'https://x.com/home')).ok,false,'session transfer only from local or extension pages');
 assert.equal((await send({type:'JOBS'},'https://example.org/')).ok,false);
 assert.equal((await send({type:'PAIR',token:kept.bridgeToken},'http://127.0.0.1:18765/')).ok,true);
 assert.equal((await send({type:'DELETE',id:'123'},'https://x.com/home')).ok,true);
 assert.deepEqual(JSON.parse(calls.requests.at(-1).options.body),{tweet_id:'123',preserve_shared:true});
 rejection={error:'shared'};
 const shared=await send({type:'DELETE',id:'123'},'https://x.com/home');
 assert.equal(shared.ok,false);
 assert.equal(shared.code,'shared');
 assert.equal(shared.error,'tipDeleteShared','older companions still explain a shared-file refusal');
 rejection={error:'unexpected-internal-detail',message:'private-detail-must-not-be-forwarded'};
 const failed=await send({type:'DELETE',id:'123'},'https://x.com/home');
 assert.equal(failed.code,'request_refused');
 assert.equal(failed.error,'errorRefused');
}

async function pair(namespace){
 const sent=[],html={dataset:{}};
 const window={};window.top=window;
 const document={documentElement:html,querySelector:selector=>selector==='meta[name="bridge-token"]'?{content:'fixture-token'}:null};
 const api={i18n:{getMessage:key=>key},runtime:{sendMessage:async message=>{sent.push(message);return {ok:true};}}};
 vm.runInNewContext(source('pair.js'),{[namespace]:api,window,document,location:{origin:'http://127.0.0.1:18765'}});
 await settle();
 assert.equal(html.dataset.zeventPaired,'1');
 assert.deepEqual(sent.map(message=>message.type),['PAIR','SESSION']);
}

(async()=>{
 for(const name of ['background.js','content.js','pair.js','popup.js'])new vm.Script(source(name),{filename:name});
 await background('chrome');
 await background('browser');
 await background('browser','org.archivex.app',false);
 await background('browser','org.archivex.app',true,false);
 await pair('chrome');await pair('browser');
 console.log('Browser compatibility: Chrome callbacks, Firefox/Safari Promises, native target, session boundaries and pairing passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
