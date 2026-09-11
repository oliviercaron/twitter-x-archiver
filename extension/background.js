// Firefox et Safari exposent les API Promise via browser ; Chrome MV3 via chrome.
// Chrome charge le service worker seul ; les autres paquets peuvent déjà avoir
// chargé ce fichier avant background.js.
if(!globalThis.archiveMessage&&typeof importScripts==='function'){
 try{importScripts('language.js');}catch{}
}
const EXT=globalThis.browser||globalThis.chrome;
const BASE='http://127.0.0.1:18765';
// Le paquet Safari fournit son identifiant d’application via archive_config.js.
const HOST=globalThis.ARCHIVE_CONFIG?.nativeHost||'com.zevent.archive';
let uiLanguage='en';
const t=(key,...args)=>globalThis.archiveMessage
 ? globalThis.archiveMessage(EXT,uiLanguage,key,args)
 : EXT.i18n.getMessage(key,args.length?args:undefined)||key;
try{
 EXT.storage?.onChanged?.addListener((changes,area)=>{
  if(area==='local'&&changes.uiLanguage)uiLanguage=changes.uiLanguage.newValue==='fr'?'fr':'en';
 });
 EXT.storage?.local?.get('uiLanguage').then(value=>{uiLanguage=value?.uiLanguage==='fr'?'fr':'en';}).catch(()=>{});
}catch{}
let starting=null;
// Chrome interdit a une extension de lancer un programme ; l'hote natif est le
// seul pont autorise. Un seul demarrage a la fois, meme si plusieurs boutons
// tombent en meme temps sur un serveur eteint.
function startServer(){
 if(!starting)starting=EXT.runtime.sendNativeMessage(HOST,{type:'start'}).then(
  answer=>{if(answer?.ok)return answer;throw Error(t(answer?.error==='companion_required'?'errorCompanionRequired':answer?.error==='timeout'?'errorStillDown':'errorStartRefused'));},
  error=>{throw Error(t(/not found|introuvable/i.test(error?.message||'')?'errorNotInstalled':'errorUnreachable'));}
 ).finally(()=>{starting=null;});
 return starting;
}
function canonical(value){const u=new URL(value);if(!['https:','http:'].includes(u.protocol)||!['x.com','www.x.com','twitter.com','www.twitter.com','mobile.twitter.com'].includes(u.hostname)||u.username||u.password||u.port)throw Error(t('errorBadUrl'));const m=u.pathname.match(/^\/(?:[A-Za-z0-9_]{1,50}|i\/web)\/status\/(\d{1,20})(?:\/(?:photo|video)\/\d+)?\/?$/);if(!m||BigInt(m[1])<=0n||BigInt(m[1])>=2n**64n)throw Error(t('errorBadUrl'));return `https://x.com/i/status/${m[1]}`;}
async function api(path,body,retried){const {bridgeToken}=await EXT.storage.local.get('bridgeToken');if(!bridgeToken)throw Error(t('errorNotPaired'));let r;try{r=await fetch(BASE+path,{method:body?'POST':'GET',headers:{Authorization:`Bearer ${bridgeToken}`,...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{}),signal:AbortSignal.timeout(12000)});}catch{if(retried)throw Error(t('errorStillDown'));await startServer();return api(path,body,true);}if(r.status===403)throw Error(t('errorPairAgain'));if(!r.ok){
  let payload;try{payload=await r.json();}catch{}
  const code=payload?.error==='shared'?'shared':payload?.error==='storage_busy'?'storage_busy':'request_refused';
  const error=Error(t(code==='shared'?'tipDeleteShared':'errorRefused'));
  error.code=code;throw error;
 }return r.json();}
// Deux cookies nommes, sur x.com uniquement : aucune enumeration, aucun autre site.
const SESSION_COOKIES=['auth_token','ct0'];
async function readSession(){
 const values={};
 for(const domain of ['https://x.com','https://twitter.com']){
  for(const name of SESSION_COOKIES){
   if(values[name])continue;
   try{const c=await EXT.cookies.get({url:domain,name});if(c?.value)values[name]=c.value;}catch{}
  }
  if(SESSION_COOKIES.every(n=>values[n]))break;
 }
 const missing=SESSION_COOKIES.filter(n=>!values[n]);
 if(missing.length)throw Error(t('errorNoSession'));
 return {auth_token:values.auth_token,ct0:values.ct0};
}

// La categorie du dernier archivage sert de choix par defaut : le bouton
// sous un post reste un seul clic.
async function categoryOption(message={}){
 if(typeof message.category==='string'&&message.category.trim())return message.category.trim();
 return (await EXT.storage.local.get('category')).category||'';
}
// Les dernieres categories servies, la plus recente en tete. Six suffisent :
// le bandeau n'en montre que deux, le reste est de la marge.
async function rememberCategory(name){
 if(!name)return;
 const {recentCategories}=await EXT.storage.local.get('recentCategories');
 const list=[name,...(recentCategories||[]).filter(c=>c!==name)].slice(0,6);
 await EXT.storage.local.set({recentCategories:list,category:name});
}
async function repliesOption(message={}){if(typeof message.includeReplies==='boolean')return message.includeReplies;return (await EXT.storage.local.get('includeReplies')).includeReplies===true;}
const LOCAL=['http://127.0.0.1:18765','http://localhost:18765'];
function fromLocal(sender){try{return LOCAL.includes(new URL(sender.url).origin);}catch{return false;}}
function fromX(sender){try{return ['x.com','www.x.com','twitter.com','www.twitter.com'].includes(new URL(sender.url).hostname);}catch{return false;}}
EXT.runtime.onMessage.addListener((message,sender,reply)=>{
 (async()=>{
  if(message.type==='PAIR'){
   const origin=new URL(sender.url).origin;
   if(!LOCAL.includes(origin)||typeof message.token!=='string'||message.token.length<32)throw Error(t('errorPairRefused'));
   await EXT.storage.local.set({bridgeToken:message.token});await api('/api/jobs');return {paired:true};
  }
  const ownPage=sender.url?.startsWith(EXT.runtime.getURL(''));
  if(message.type==='SET_LANGUAGE'){
   if(!ownPage&&!fromLocal(sender))throw Error(t('errorOrigin'));
   const language=message.language==='fr'?'fr':'en';
   await EXT.storage.local.set({uiLanguage:language});
   return {language};
  }
  if(message.type==='SESSION'||message.type==='FORGET_SESSION'){
   if(!ownPage&&!fromLocal(sender))throw Error(t('errorOrigin'));
   if(message.type==='FORGET_SESSION')return api('/api/session/forget',{});
   // Les valeurs partent au serveur local et ne sont ni journalisees ni renvoyees.
   await api('/api/session',await readSession());
   return {session:true};
  }
  if(!ownPage&&!fromX(sender))throw Error(t('errorOrigin'));
  if(message.type==='ARCHIVE'){
   const category=await categoryOption(message);
   const job=await api('/api/archive',{url:canonical(message.url),mode:'manual_extension',note:message.note||'',
    refresh:message.refresh===true,include_replies:await repliesOption(message),category});
   // La categorie retenue est celle que le service a inscrite, pas celle qui
   // a ete demandee : un nom vide devient la categorie par defaut la-bas.
   await rememberCategory(job?.selection?.category||category);
   return job;
  }
  if(message.type==='STATUS'){
   if(!/^\d{1,20}$/.test(String(message.id)))throw Error(t('errorBadId'));return api('/api/jobs/'+message.id);
  }
  if(message.type==='JOBS')return api('/api/jobs');
  if(message.type==='STATES')return api('/api/states');
  if(message.type==='CATEGORIES')return api('/api/categories');
  if(message.type==='RECENT'){
   // Les categories deja servies d'abord, puis celles que l'archive
   // connait : le bandeau reste utile meme sur un navigateur neuf.
   const kept=await EXT.storage.local.get(['recentCategories','category']);
   let known=[],fallback='';
   try{const data=await api('/api/categories');known=(data.categories||[]).map(c=>c.category);fallback=data.default||'';}catch{}
   const current=message.category||kept.category||fallback;
   const seen=new Set([current]);
   const others=[...(kept.recentCategories||[]),...known]
    .filter(c=>c&&!seen.has(c)&&seen.add(c)).slice(0,2);
   return {current,others,known:[...new Set([current,...(kept.recentCategories||[]),...known].filter(Boolean))]};
  }
  if(message.type==='MOVE'){
   if(!/^\d{1,20}$/.test(String(message.id)))throw Error(t('errorBadId'));
   const moved=await api('/api/category',{tweet_id:String(message.id),category:String(message.category||'')});
   await rememberCategory(moved.category);
   return moved;
  }
  if(message.type==='DELETE'){
   if(!/^\d{1,20}$/.test(String(message.id)))throw Error(t('errorBadId'));
   return api('/api/delete',{tweet_id:String(message.id),preserve_shared:true});
  }
  throw Error(t('errorUnknown'));
 })().then(result=>reply({ok:true,result})).catch(error=>reply({ok:false,error:error.message,code:error.code}));
 return true;
});
// Firefox n’accepte pas le rappel Chrome sur son API Promise removeAll.
// Le menu est un raccourci facultatif : les boutons et la popup restent utilisables.
const menus=EXT.contextMenus||EXT.menus;
EXT.runtime.onInstalled.addListener(()=>{
 if(!menus)return;
 const create=()=>menus.create({id:'archive-post',title:t('contextArchive'),contexts:['link'],targetUrlPatterns:['https://x.com/*/status/*','https://twitter.com/*/status/*','https://www.x.com/*/status/*']});
 if(globalThis.browser)menus.removeAll().then(create).catch(()=>{});
 else menus.removeAll(create);
});
menus?.onClicked.addListener(async(info)=>{if(info.menuItemId!=='archive-post')return;try{await api('/api/archive',{url:canonical(info.linkUrl),mode:'manual_extension',include_replies:await repliesOption(),category:await categoryOption()});await EXT.action.setBadgeBackgroundColor({color:'#126b58'});await EXT.action.setBadgeText({text:'+'});}catch{await EXT.action.setBadgeText({text:'!'});await EXT.tabs.create({url:BASE});}});
