(() => {
const EXT=globalThis.browser||globalThis.chrome;
 const requestedLanguage=String(location.search||'').match(/[?&]lang=(fr|en)(?:&|$)/)?.[1]||'';
 const uiLanguage=requestedLanguage==='fr'?'fr':'en';
 const t=(key,...args)=>globalThis.archiveMessage
  ? globalThis.archiveMessage(EXT,uiLanguage,key,args)
  : EXT.i18n.getMessage(key,args.length?args:undefined)||key;
 // Ce script tourne dans le monde isole de l'extension : il ne partage aucune
 // variable avec la page. Le DOM est le seul canal commun, d'ou l'attribut.
 if(!['http://127.0.0.1:18765','http://localhost:18765'].includes(location.origin)||window.top!==window)return;
 const meta=document.querySelector('meta[name="bridge-token"]');
 if(!meta)return;
 const status=()=>document.querySelector('#pair-status');
 const pairButton=document.querySelector('#pair-extension');
 const sessionButton=document.querySelector('#use-session');

 function announce(paired){document.documentElement.dataset.zeventPaired=paired?'1':'0';}

 async function pair(){
  document.documentElement.dataset.zeventPaired='pending';
  try{
   const answer=await EXT.runtime.sendMessage({type:'PAIR',token:meta.content});
   if(!answer?.ok)throw Error(answer?.error||t('errorPairFailed'));
   announce(true);
  }catch(error){announce(false);throw error;}
 }

 async function syncLanguage(){
  const language=requestedLanguage;
  if(language!=='fr'&&language!=='en')return;
  const answer=await EXT.runtime.sendMessage({type:'SET_LANGUAGE',language});
  if(!answer?.ok)throw Error(answer?.error||t('errorPairFailed'));
 }

 async function linkSession(){
  const answer=await EXT.runtime.sendMessage({type:'SESSION'});
  if(!answer?.ok)throw Error(answer?.error||t('errorNoSessionApi'));
 }

 // Appairage au chargement : l'utilisateur n'a pas a cliquer pour une operation
 // qui ne lui demande aucune decision.
 (async()=>{
  try{await pair();await syncLanguage();}
  catch(error){announce(false);const s=status();if(s)s.textContent=error.message;return;}
  try{await linkSession();}
  catch{}                       // sans session X, le bandeau le dira de lui-meme
 })();

 if(pairButton)pairButton.addEventListener('click',async event=>{
  if(!event.isTrusted)return;
  const s=status();
  try{await pair();await syncLanguage();await linkSession();if(s)s.textContent='';}
  catch(error){if(s)s.textContent=error.message;}
 });

 if(sessionButton)sessionButton.addEventListener('click',async event=>{
  if(!event.isTrusted)return;
  const s=status();sessionButton.disabled=true;
  if(s)s.textContent=t('pairStatusReading');
  try{await linkSession();if(s)s.textContent='';}
  catch(error){if(s)s.textContent=error.message;}
  finally{sessionButton.disabled=false;}
 });
})();
