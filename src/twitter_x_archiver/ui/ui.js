const token=document.querySelector('meta[name="bridge-token"]').content;
const RETRYABLE=['partial','retry','unavailable'];
let retryingAll=false;
const badge_=status=>(BADGES[LANG][status]||status);
const BADGES={fr:{queued:'En attente',fetching:'Récupération…',done:'Archivé',partial:'Partiel',unavailable:'Indisponible sur X',retry:'À réessayer'},
             en:{queued:'Waiting',fetching:'Fetching…',done:'Archived',partial:'Partial',unavailable:'Unavailable on X',retry:'Try again'}};
async function api(path,body){
 const r=await fetch(path,{method:body?'POST':'GET',headers:{Authorization:`Bearer ${token}`,...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});
 if(!r.ok){
  // Sans le code ni le corps, un refus deliberé et une panne se ressemblent.
  const error=new Error('Le serveur n’a pas accepté la demande.');
  error.status=r.status;
  try{error.payload=await r.json();}catch{}
  throw error;
 }
 return r.json();
}
function el(tag,text,cls){const n=document.createElement(tag);if(text)n.textContent=text;if(cls)n.className=cls;return n;}
const LOCALE=LANG==='en'?'en-GB':'fr-FR';
const compact=new Intl.NumberFormat(LOCALE,{notation:'compact',maximumFractionDigits:1});
const moment=new Intl.DateTimeFormat(LOCALE,{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});
const momentYear=new Intl.DateTimeFormat(LOCALE,{day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'});
function number(v){return typeof v==='number'?compact.format(v):null;}
function when(v){
 const d=v?new Date(v):null;
 if(!d||isNaN(d))return null;
 return (d.getFullYear()===new Date().getFullYear()?moment:momentYear).format(d);
}
function length(s){if(typeof s!=='number')return null;const m=Math.floor(s/60),r=Math.round(s%60);return `${m}:${String(r).padStart(2,'0')}`;}

const FIELDS=['q','compte','categorie','type','statut','du','au','tri','page'];
const player=document.querySelector('#player');
let knownCategories=[];        // alimente le filtre et le rangement
let playing=null;                 // identifiant du post dont la video est ouverte
let lastTotal=null;
let cards=new Map();              // identifiant -> element, pour reconcilier

// L'etat vit dans l'URL : la page se recharge a l'identique et se partage.
function readState(){
 const p=new URLSearchParams(location.search);
 return {q:p.get('q')||'',compte:p.get('compte')||'',categorie:p.get('categorie')||'',type:p.get('type')||'',
         statut:p.get('statut')||'',du:p.get('du')||'',au:p.get('au')||'',
         tri:p.get('tri')||'recent',page:Number(p.get('page'))||1};
}
function writeState(state,push){
 const p=new URLSearchParams();
 p.set('lang',LANG);
 for(const k of FIELDS){const v=state[k];if(v&&!(k==='tri'&&v==='recent')&&!(k==='page'&&v===1))p.set(k,v);}
 const url=location.pathname+(p.toString()?'?'+p:'');
 // Chaque frappe empilerait une entree d'historique : seul un changement de
 // page merite que le bouton Precedent y revienne.
 history[push?'pushState':'replaceState'](null,'',url);
}
function applyState(state){
 document.querySelector('#q').value=state.q;
 document.querySelector('#compte').value=state.compte;
 document.querySelector('#categorie').value=state.categorie;
 document.querySelector('#tri').value=state.tri;
 document.querySelector('#du').value=state.du;
 document.querySelector('#au').value=state.au;
 for(const [name,value] of [['type',state.type],['statut',state.statut]]){
  const input=document.querySelector(`input[name="${name}"][value="${value}"]`);
  if(input)input.checked=true;
 }
 const filtered=state.q||state.compte||state.categorie||state.type||state.statut||state.du||state.au||state.tri!=='recent';
 document.querySelector('#clear').disabled=!filtered;
}

function closePlayer(){
 // Vider la source libere le decodeur et coupe le telechargement en cours.
 player.pause();player.removeAttribute('src');player.load();
 player.hidden=true;playing=null;
 document.querySelector('#jobs').prepend(player);
}
function openPlayer(item,card){
 closePlayer();
 playing=item.id;
 card.after(player);
 player.hidden=false;
 player.src='/'+item.video;
 player.setAttribute('aria-label',T('videoAria')(item.author||T('thisPost')));
 player.play().catch(()=>{});   // le navigateur peut refuser la lecture auto
 player.focus();
}
function togglePlayer(item,card){playing===item.id?closePlayer():openPlayer(item,card);}
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&playing)closePlayer();});

function buildCard(item){
 const card=el('article',null,'job');card.dataset.id=item.id;
 card._mediaSignature=JSON.stringify([item.thumb,item.video,item.duration,item.author]);
 const media=el('div',null,'media');
 if(item.thumb){
  const face=el('button',null,'play');face.type='button';
  const img=document.createElement('img');
  img.className='thumb';img.src='/'+item.thumb;img.alt='';img.loading='lazy';img.decoding='async';
  face.append(img);
  if(item.video){
   face.append(el('span','▶','glyph'));
   const d=length(item.duration);if(d)face.append(el('span',d,'dur'));
   face.setAttribute('aria-label',T('playAria')(item.author||T('thisPost')));
   face.onclick=()=>togglePlayer(item,card);
  }else{face.disabled=true;face.setAttribute('aria-hidden','true');}
  media.append(face);
 }else media.append(el('div','—','thumb vide'));
 const info=el('div',null,'info');
 const head=el('div',null,'head');
 const a=el('a',item.author?'@'+item.author:'Post '+item.id);
 a.href=`https://x.com/i/status/${item.id}`;a.target='_blank';a.rel='noopener noreferrer';
 head.append(a,el('span','','posted'));
 info.append(head,el('p','','text'),el('p','','metrics'));
 const side=el('div',null,'side');
 side.append(el('span','','badge'));
 const cat=el('button',null,'cat');cat.type='button';
 cat.onclick=()=>openCategoryPicker(cat,item.id);
 side.append(cat);
 const remove=el('button',null,'wipe');remove.type='button';
 side.append(remove);
 card.append(media,info,side);
 // La vignette est une cible etroite : toute la carte ouvre la video. Les
 // liens, les boutons et le lecteur gardent leur propre clic, et une
 // selection de texte en cours n'est pas prise pour un clic.
 if(item.video){
  card.dataset.playable='1';
  card.onclick=event=>{
   if(event.target.closest('a,button,select,video,.shared-note'))return;
   const picked=window.getSelection&&window.getSelection();
   if(picked&&!picked.isCollapsed&&card.contains(picked.anchorNode))return;
   togglePlayer(item,card);
  };
 }
 return card;
}

function fillCard(card,item){
 const posted=when(item.posted);
 card.querySelector('.posted').textContent=posted?T('published')(posted):'';
 card.querySelector('.text').textContent=item.text||'';
 const metrics=[number(item.views)&&T('views')(number(item.views)),
                number(item.likes)&&T('likes')(number(item.likes)),
                number(item.reposts)&&T('reposts')(number(item.reposts))].filter(Boolean);
 const line=card.querySelector('.metrics');
 line.replaceChildren(document.createTextNode(metrics.join(' · ')||T('noCounts')));
 // L'archivage est une trace de notre action, pas une propriete du post : il
 // ferme la ligne des chiffres au lieu de coiffer la carte.
 const archived=when(item.archived);
 if(archived)line.append(el('span',T('archivedAt')(archived),'archived'));
 const chip=card.querySelector('.cat');
 if(chip&&chip.dataset.editing!=='1'){chip.textContent=item.category||'';chip.title=T('catTip');
  chip.setAttribute('aria-label',T('catTip'));chip.hidden=!item.category;}
 const badge=card.querySelector('.badge');
 badge.textContent=badge_(item.status);
 badge.className='badge '+item.status;
 card.dataset.deletable=item.status==='done'?'1':'0';
 const wipe=card.querySelector('.wipe');
 if(wipe.dataset.armed!=='1'&&!wipe.disabled)resetWipe(wipe);
}

function resetWipe(button){
 clearTimeout(button._timer);
 button.dataset.armed='0';button.disabled=false;button.textContent='✕';
 button.title=T('wipeTip');
 button.setAttribute('aria-label',button.title);
}
function armWipe(button){
 button.dataset.armed='1';button.textContent=T('wipeConfirm');
 button.title=T('wipeConfirmTip');
 button.setAttribute('aria-label',button.title);
 button._timer=setTimeout(()=>resetWipe(button),4000);
}
async function runWipe(button,id){
 clearTimeout(button._timer);button.disabled=true;button.textContent=T('wipeRunning');
 // Le lecteur se ferme d'abord : un fichier encore lu peut resister a sa
 // suppression, et la carte va disparaitre de toute facon.
 if(playing===id)closePlayer();
 try{
  const answer=await api('/api/delete',{tweet_id:id});
  if(answer?.left?.length)throw Error(T('wipeLeft'));
  await loadPosts();
 }catch(error){
  const shared=error.status===409&&error.payload?.error==='shared';
  button.textContent=shared?T('wipeShared'):T('wipeFailed');
  button.disabled=false;
  if(shared)explainShared(button,id,error.payload);
  else setTimeout(()=>resetWipe(button),4000);
 }
}

// Un refus se comprend mieux avec le voisin concerne et la decision sure a
// prendre qu'avec un simple message generique.
function explainShared(button,id,payload){
 const card=button.closest('.job');
 if(!card)return;
 const others=Number.isInteger(payload.others)?payload.others:(payload.with||[]).length;
 // Le refus a deja eu lieu : le bouton principal redevient une action de
 // consultation et ne doit pas rester arme pendant que l'explication est lue.
 button.dataset.armed='0';button.disabled=false;
 button.title=T('wipeTip');button.setAttribute('aria-label',button.title);
 let note=card.querySelector('.shared-note');
 if(!note){note=el('div','','shared-note');card.querySelector('.info').append(note);}
 note.replaceChildren(document.createTextNode(T('sharedNote')(others)));
 const related=Array.isArray(payload.related_posts)?payload.related_posts:[];
 if(related.length){
  note.append(el('strong',T('linkedPosts'),'linked-title'));
  for(const item of related.slice(0,5)){
   const line=el('div',null,'linked-post');
   const meta=el('span',null,'linked-post-meta');
   const label=item.author?`@${item.author}`:`Post ${item.tweet_id}`;
   const link=el('a',label,'linked-post-link');
   link.href=item.url||`https://x.com/i/status/${item.tweet_id}`;
   link.target='_blank';link.rel='noopener noreferrer';
   meta.append(link,el('span',T('linkedRelation')(item.relation),'linked-relation'));
   line.append(meta);
   if(item.text)line.append(el('span',`« ${item.text} »`,'linked-text'));
   note.append(line);
  }
 }
 const actions=el('div',null,'shared-actions');
 const keep=el('button',T('wipeKeepShared'),'link');keep.type='button';
 keep.onclick=async()=>{
  keep.disabled=true;keep.textContent=T('wipeRunning');
  try{
   if(playing===id)closePlayer();
   // Supprimer la ligne choisie, en conservant les fichiers encore references
   // par les posts lies. Chaque post reste ainsi supprimable independamment.
   await api('/api/delete',{tweet_id:id,preserve_shared:true});
   await loadPosts();
  }catch{keep.disabled=false;keep.textContent=T('wipeFailed');}
 };
 const cancel=el('button',T('wipeCancel'),'link cancel');cancel.type='button';
 cancel.onclick=()=>{note.remove();resetWipe(button);};
 actions.append(keep,cancel);
 note.append(actions);
}

function renderPager(data){
 const pager=document.querySelector('#pager');pager.replaceChildren();
 if(data.pages<2)return;
 const go=(n,label,current)=>{
  const b=el('button',label||String(n),'page');b.type='button';
  if(current){b.setAttribute('aria-current','page');b.classList.add('current');}
  b.onclick=()=>{const s=readState();s.page=n;writeState(s,true);loadPosts();window.scrollTo({top:document.querySelector('#jobs').offsetTop-80,behavior:'smooth'});};
  return b;
 };
 const p=data.page,last=data.pages;
 if(p>1)pager.append(go(p-1,'Précédent'));
 const seen=new Set();
 for(const n of [1,p-1,p,p+1,last]){
  if(n<1||n>last||seen.has(n))continue;
  if(seen.size&&n>Math.max(...seen)+1)pager.append(el('span','…','gap'));
  seen.add(n);pager.append(go(n,null,n===p));
 }
 if(p<last)pager.append(go(p+1,'Suivant'));
}

let postsRequest=0;
async function loadPosts(){
 const request=++postsRequest;
 const state=readState();
 const p=new URLSearchParams();
 for(const k of FIELDS){if(state[k])p.set(k,state[k]);}
 let data;
 try{data=await api('/api/posts?'+p);}
 catch{if(request===postsRequest)document.querySelector('#counts').textContent=T('unreachable');return false;}
 if(request!==postsRequest)return false;
 applyState(state);
 const list=document.querySelector('#accounts');
 if(list.childElementCount!==data.authors.length){
  list.replaceChildren(...data.authors.map(a=>{const o=document.createElement('option');o.value=a.author;o.label=`${a.author} (${a.count})`;return o;}));
 }
  knownCategories=(data.categories||[]).map(c=>c.category);
  fillCategorySelect(document.querySelector('#categorie'),T('filterCategory'),readState().categorie);
  fillCategorySelect(document.querySelector('#export-categories'),T('exportScopeAll'),
                     document.querySelector('#export-categories').value);
 if(data.span&&data.span.from){
  for(const id of ['#du','#au']){
   const input=document.querySelector(id);
   input.min=data.span.from;input.max=data.span.to;
  }
 }
 const first=data.total?(data.page-1)*data.size+1:0;
 document.querySelector('#counts').textContent=data.total
  ?T('range')(first,Math.min(first+data.size-1,data.total),data.total):T('noResult');
 // La region live n'annonce que le total, sinon un lecteur d'ecran reciterait
 // le compteur a chaque rafraichissement.
 if(data.total!==lastTotal){lastTotal=data.total;document.querySelector('#live').textContent=T('resultCount')(data.total);}

 const root=document.querySelector('#jobs');
 for(const stale of root.querySelectorAll('.empty'))stale.remove();
 const next=new Map();
 let previous=null;
 for(const item of data.items){
  let card=cards.get(item.id);
  // Les médias arrivent après la première ligne de métadonnées. Recréer
  // seulement cette carte actualise aussi ses gestionnaires de clic.
  if(card&&card._mediaSignature!==JSON.stringify([item.thumb,item.video,item.duration,item.author])){
   const old=card;card=null;
   clearTimeout(old.querySelector('.wipe')._timer);
   old.remove();
  }
  if(!card){card=buildCard(item);
   card.querySelector('.wipe').onclick=()=>{const b=card.querySelector('.wipe');
    b.dataset.armed==='1'?runWipe(b,item.id):armWipe(b);};
  }
  fillCard(card,item);
  // Reconciliation par identifiant : remplacer tout le contenu detruirait le
  // lecteur ouvert et ferait remonter le defilement.
  if(previous)previous.after(card);else root.prepend(card);
  previous=playing===item.id&&player.parentNode===root?player:card;
  next.set(item.id,card);
 }
 for(const [id,card] of cards)if(!next.has(id)){if(playing===id)closePlayer();card.remove();}
 cards=next;
 if(!data.total)root.append(el('div',T('empty'),'empty'));
 renderPager(data);
 return true;
}

function onFilterChange(){
 const state=readState();
 state.q=document.querySelector('#q').value.trim();
 state.compte=document.querySelector('#compte').value.trim();
 state.categorie=document.querySelector('#categorie').value;
 state.tri=document.querySelector('#tri').value;
 state.type=document.querySelector('input[name="type"]:checked').value;
 state.statut=document.querySelector('input[name="statut"]:checked').value;
 state.du=document.querySelector('#du').value;
 state.au=document.querySelector('#au').value;
 state.page=1;
 writeState(state,false);loadPosts();
}
let typing;
document.querySelector('#q').addEventListener('input',()=>{clearTimeout(typing);typing=setTimeout(onFilterChange,220);});
document.querySelector('#compte').addEventListener('change',onFilterChange);
for(const id of ['#du','#au'])document.querySelector(id).addEventListener('change',onFilterChange);
document.querySelector('#categorie').addEventListener('change',onFilterChange);
document.querySelector('#tri').addEventListener('change',onFilterChange);
for(const input of document.querySelectorAll('input[name="type"],input[name="statut"]'))input.addEventListener('change',onFilterChange);
document.querySelector('#clear').addEventListener('click',()=>{
 // Tout repart de zero, le tri compris : un tri oublie explique autant de
 // « je ne retrouve plus mes posts » qu'un filtre oublie.
 history.replaceState(null,'',location.pathname+'?lang='+LANG);
 for(const id of ['#q','#compte','#du','#au'])document.querySelector(id).value='';
 document.querySelector('#categorie').value='';
 document.querySelector('#tri').value='recent';
 document.querySelector('#t-').checked=true;document.querySelector('#s-').checked=true;
 loadPosts();
});
document.querySelector('#filters').addEventListener('submit',e=>e.preventDefault());
window.addEventListener('popstate',()=>loadPosts());

let setupService;
let setupGrace=true;
function setup(service){
 if(service)setupService=service;
 service=setupService;
 const strip=document.querySelector('#setup'),text=document.querySelector('#setup-text');
 const pair=document.querySelector('#pair-extension'),session=document.querySelector('#use-session');
 if(!strip)return;
 const pairingState=document.documentElement.dataset.zeventPaired;
 const paired=pairingState==='1';
 // Une page neuve attend la confirmation de l'extension ; l'absence
 // momentanée de son attribut n'est pas une preuve de déconnexion.
 if(!paired&&(pairingState==='pending'||(pairingState===undefined&&setupGrace))){
  strip.classList.remove('warn');
  pair.hidden=setupGrace;session.hidden=true;
  text.textContent=T(setupGrace?'setupChecking':'setupCheckingSlow');
  return;
 }
 const hasSession=service?.session_present!==false;
 const parts=[];
 if(paired)parts.push(T('setupPaired'));
 if(service?.session_present===true)parts.push(T('setupSessionLive'));
 else if(service?.account_active)parts.push(T('setupSessionStored'));
 strip.classList.toggle('warn',!paired||!hasSession);
 if(pair)pair.hidden=paired;
 if(session)session.hidden=hasSession;
 text.textContent=!paired?T(pairingState==='0'?'setupPairFailed':'setupLoadExtension')
  :!hasSession?T('setupNoSession')
  :parts.join(' · ')+'.';
}
new MutationObserver(()=>setup()).observe(document.documentElement,
 {attributes:true,attributeFilter:['data-zevent-paired']});
setTimeout(()=>{setupGrace=false;setup();},8000);
setup();

let busy=false;
let jobsSignature=null;
let updating=false;
async function update(){
 if(updating)return;
 updating=true;
 try{
  const {jobs,service_status:service}=await api('/api/jobs');
  setup(service);
  const notice=document.querySelector('#service-status');
  if(notice)notice.textContent=service?.quota_wait
   ?T('quotaWait')(new Date(service.quota_reset_at).toLocaleTimeString(LOCALE))
   :service?.account_active===false?T('sessionStale'):'';
  const stuck=jobs.filter(j=>RETRYABLE.includes(j.status));
  const all=document.querySelector('#retry-all');
  if(all&&!retryingAll){all.disabled=stuck.length===0;
   all.textContent=stuck.length?T('retryAllCount')(stuck.length):T('retryAll');
   all.title=stuck.length?T('retryAllTip')(stuck.length):T('retryAllNone');}
  // Un archivage peut commencer et finir entre deux sondages, notamment
  // quand l'onglet était masqué. L'identité et la date des tâches le révèlent.
  const working=jobs.some(j=>['queued','fetching'].includes(j.status));
  const signature=JSON.stringify(jobs.map(j=>[j.tweet_id,j.status,j.updated_at]));
  if(working||busy!==working||signature!==jobsSignature){
   if(await loadPosts()){busy=working;jobsSignature=signature;}
  }
 }catch{}finally{updating=false;}
}

document.querySelector('#retry-all').addEventListener('click',async()=>{
 const button=document.querySelector('#retry-all'),status=document.querySelector('#retry-status');
 retryingAll=true;button.disabled=true;
 try{
  const {jobs}=await api('/api/jobs');
  const targets=jobs.filter(j=>RETRYABLE.includes(j.status));
  if(!targets.length){status.textContent=T('retryAllNone');return;}
  let ok=0,bad=0;
  for(const [i,j] of targets.entries()){
   status.textContent=T('retryProgress')(i+1,targets.length);
   try{await api('/api/archive',{url:j.url,mode:j.selection.mode,note:j.selection.note,refresh:false,include_replies:j.include_replies===true});ok++;}
   catch{bad++;}
  }
  status.textContent=T('retryDone')(ok,bad);
 }catch{status.textContent=T('retryUnreachable');}
 finally{retryingAll=false;button.disabled=false;await update();await loadPosts();}
});

// Les chemins sont ceux de la machine qui execute le service, pas ceux d'un
// poste en particulier.
function updatePaths(){return api('/api/paths').then(paths=>{
 for(const [id,value] of [['path-extension',paths.extension],['path-data',paths.data],['path-posts',paths.posts]]){
  const node=document.querySelector('#'+id);if(node&&value)node.textContent=value;
 }
}).catch(()=>{});}
updatePaths();

loadPosts();update();
// Le sondage se met en veille quand l'onglet n'est pas regarde.
setInterval(()=>{if(!document.hidden)update();},3000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)update();});

document.querySelector('#add').addEventListener('submit',async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;const feedback=document.querySelector('#feedback');let ok=0,bad=0;const failed=[];for(const value of document.querySelector('#urls').value.split(/\r?\n/)){const url=value.trim();if(!url||url.startsWith('#'))continue;try{await api('/api/archive',{url,note:document.querySelector('#note').value,mode:'manual_url',refresh:document.querySelector('#refresh').checked,include_replies:document.querySelector('#include-replies').checked});ok++;}catch{bad++;failed.push(url);}}feedback.textContent=T('addResult')(ok,bad);document.querySelector('#urls').value=failed.join('\n');b.disabled=false;await update();});
document.querySelector('#pair-extension').addEventListener('click',()=>{const status=document.querySelector('#pair-status');status.textContent=T('pairing');setTimeout(()=>{const state=document.documentElement.dataset.zeventPaired;if(status.textContent===T('pairing')&&state!=='1'&&state!=='pending')status.textContent=T('pairMissing');},8000);});


let resultSettingsLoaded=false;
let resultRequest=0;
let lastExportState=null;
function exportMessage(message,state=''){
 const output=document.querySelector('#export-status');
 output.textContent=message;output.dataset.state=state;
}
function renderExport(status){
 // Un sondage identique ne doit pas effacer le retour du choix de dossier.
 const key=JSON.stringify(status);
 if(key===lastExportState)return;
 lastExportState=key;
 const location=document.querySelector('#export-location');
 const path=status.display_path||'';
 location.hidden=!path||!['done','partial'].includes(status.status);
 document.querySelector('#export-path').textContent=path;
 if(status.status==='running')return exportMessage(T('exportRunning'),'running');
 if(['done','partial'].includes(status.status)){
  if(status.folder_missing)return exportMessage(T('exportGone'),'notice');
  const missing=(status.missing_files||[]).length;
  const partial=status.status==='partial'||missing>0;
  exportMessage('',partial?'notice':'success');
  document.querySelector('#export-status').append(
   el('strong',T(partial?'exportPartial':'exportReady')),
   el('span',T('exportDone')(status.tweets,status.copied_files)),
   ...(missing?[el('span',T('exportMissing')(missing))]:[]));
 }else exportMessage(status.message||'',status.message?'notice':'');
}
async function updateResults(){
 const request=++resultRequest;
 try{
  const data=await api('/api/results');
  if(request!==resultRequest)return;
  if(!resultSettingsLoaded){document.querySelector('#result-folder').value=data.destination;
  document.querySelector('#include-automatic').checked=data.include_automatic;
  const scope=document.querySelector('#export-categories');
  const kept=(data.categories||[])[0]||'';
  if(kept&&!Array.from(scope.options).some(o=>o.value===kept))scope.append(new Option(kept,kept));
  scope.value=kept;resultSettingsLoaded=true;}
  const status=data.export;
  document.querySelector('#export-button').disabled=status.status==='running';
  renderExport(status);
 }catch{lastExportState=null;exportMessage(T('exportUnreachable'),'notice');}
}
document.querySelector('#choose-folder').addEventListener('click',async()=>{
 const button=document.querySelector('#choose-folder');button.disabled=true;
 exportMessage(T('exportPickHint'));
 try{const data=await api('/api/results/choose-folder',{});if(!data.cancelled)document.querySelector('#result-folder').value=data.destination;exportMessage(data.cancelled?T('exportPickCancelled'):T('exportPickDone'));}
 catch{exportMessage(T('exportPickFailed'),'notice');}
 finally{button.disabled=false;}
});
document.querySelector('#export-results').addEventListener('submit',async event=>{
 event.preventDefault();document.querySelector('#export-button').disabled=true;
 try{const scope=document.querySelector('#export-categories').value;
  await api('/api/results',{destination:document.querySelector('#result-folder').value,include_automatic:document.querySelector('#include-automatic').checked,categories:scope?[scope]:null});lastExportState=null;await updateResults();}
 catch{exportMessage(T('exportRefused'),'notice');document.querySelector('#export-button').disabled=false;}
});
updateResults();setInterval(updateResults,3000);

// La langue vit dans l'adresse : un rechargement la garde, un lien la transmet.
function updateLanguageLinks(){
 for(const link of document.querySelectorAll('#lang a')){
  const p=new URLSearchParams(location.search);
  p.set('lang',link.dataset.lang);
  link.href=location.pathname+'?'+p+location.hash;
  if(link.dataset.lang===LANG)link.setAttribute('aria-current','true');
  else link.removeAttribute('aria-current');
 }
}
document.querySelector('#lang').addEventListener('pointerdown',updateLanguageLinks);
document.querySelector('#lang').addEventListener('focusin',updateLanguageLinks);
updateLanguageLinks();
// Les raccourcis déplacent aussi le focus pour la navigation au clavier.
for(const link of document.querySelectorAll('a[href="#add-card"],a[href="#results-card"]')){
 link.addEventListener('click',event=>{
  event.preventDefault();
  history.replaceState(null,'',link.hash);
  document.querySelector(link.hash).scrollIntoView();
  document.querySelector(link.hash==='#add-card'?'#urls':'#result-folder').focus({preventScroll:true});
 });
}
applyStatic();
setup();


// --- Categories -------------------------------------------------------------
// Une seule categorie par post : la pastille dit ou il est range, et le menu
// le deplace sans rien redemander a X.
function fillCategorySelect(node,firstLabel,keep){
 if(!node)return;
 const wanted=[''].concat(knownCategories);
 const current=Array.from(node.options).map(o=>o.value);
 if(current.join('\u0000')===wanted.join('\u0000')){node.options[0].textContent=firstLabel;node.value=keep||'';return;}
 node.replaceChildren();
 node.append(new Option(firstLabel,''));
 for(const c of knownCategories)node.append(new Option(c,c));
 node.value=wanted.includes(keep)?keep:'';
}

function openCategoryPicker(chip,id){
 if(chip.dataset.editing==='1')return;
 const previous=chip.textContent;
 const picker=document.createElement('select');
 picker.className='cat-picker';
 picker.setAttribute('aria-label',T('catTip'));
 for(const c of knownCategories)picker.append(new Option(c,c));
 picker.append(new Option(T('catNew'),'\u0000new'));
 picker.value=previous;
 chip.dataset.editing='1';chip.replaceWith(picker);picker.focus();
 const restore=text=>{chip.textContent=text;chip.dataset.editing='0';picker.replaceWith(chip);};
 picker.onchange=async()=>{
  let wanted=picker.value;
  if(wanted==='\u0000new'){
   wanted=(window.prompt(T('catPrompt'),previous)||'').trim();
   if(!wanted)return restore(previous);
  }
  if(wanted===previous)return restore(previous);
  picker.disabled=true;
  try{
   const answer=await api('/api/category',{tweet_id:id,category:wanted});
   restore(answer.category);
   await loadPosts();
  }catch{restore(previous);chip.title=T('catFailed');}
 };
 picker.onblur=()=>{if(chip.dataset.editing==='1')restore(previous);};
}

// Le dossier de travail se change explicitement, après lecture du récapitulatif.
// Une coupure pendant l’activation garde la progression et reprend au sondage suivant.
const STORAGE_WORKING=['pending','copying','verifying','switching'];
let storageState=null,storageLoading=false,storageRequest=0,storageChoosing=false,storageSubmitting=false;
const storageNode=id=>document.querySelector('#storage-'+id);
function storageMessage(message,state=''){
 const output=storageNode('status');output.textContent=message;output.dataset.state=state;
}
function storageControls(){
 const busy=storageSubmitting||STORAGE_WORKING.includes(storageState?.status);
 storageNode('open').disabled=!storageState?.path||busy;
 storageNode('edit').disabled=!storageState?.path||busy;
 for(const id of ['destination','choose','cancel'])storageNode(id).disabled=busy||storageChoosing;
 storageNode('confirm').disabled=busy||storageChoosing||!storageNode('destination').value.trim();
}
async function updateStorage(){
 if(storageLoading)return;
 storageLoading=true;const request=++storageRequest;
 try{
  const data=await api('/api/storage');
  if(request!==storageRequest)return;
  const previous=storageState;
  const changedPath=Boolean(previous?.path&&data.path&&previous.path!==data.path);
  storageState=data;
  storageNode('path').textContent=data.path||'';
  const busy=STORAGE_WORKING.includes(data.status),progress=storageNode('progress');
  progress.hidden=!busy;
  if(busy&&Number(data.files_total)>0){progress.max=Number(data.files_total);progress.value=Number(data.files_done)||0;}
  else progress.removeAttribute('value');
  if(busy){
   const label={pending:'storagePending',copying:'storageCopying',verifying:'storageVerifying',switching:'storageSwitching'}[data.status];
   storageMessage(T(label)+(data.files_total>0?' '+T('storageCount')(data.files_done||0,data.files_total):''),'running');
  }else if(data.status==='error')storageMessage(T('storageError')+(data.message?' '+data.message:''),'notice');
  else if(changedPath||(data.status==='done'&&previous?.status!==data.status)){
   storageMessage(T('storageDone'),'success');storageNode('move').hidden=true;
  }else if(storageNode('status').dataset.state==='reconnecting')storageMessage('');
  storageControls();
  if(changedPath){
   jobsSignature=null;await Promise.all([updatePaths(),loadPosts(),update()]);
  }
 }catch{
  if(request===storageRequest)storageMessage(T('storageUnavailable'),'reconnecting');
 }finally{storageLoading=false;}
}
storageNode('edit').addEventListener('click',()=>{
 storageNode('move').hidden=false;storageNode('destination').focus();storageControls();
});
storageNode('cancel').addEventListener('click',()=>{storageNode('move').hidden=true;storageNode('edit').focus();});
storageNode('destination').addEventListener('input',storageControls);
storageNode('open').addEventListener('click',async()=>{
 const button=storageNode('open');button.disabled=true;
 try{await api('/api/storage/open',{});}catch{storageMessage(T('storageOpenError'),'notice');}
 finally{storageControls();}
});
storageNode('choose').addEventListener('click',async()=>{
 storageChoosing=true;storageControls();storageMessage(T('exportPickHint'));
 try{
  const result=await api('/api/storage/choose-folder',{});
  if(!result.cancelled&&result.destination)storageNode('destination').value=result.destination;
  storageMessage(T(result.cancelled?'exportPickCancelled':'storagePickDone'));
 }catch{storageMessage(T('storagePickFailed'),'notice');}
 finally{storageChoosing=false;storageControls();}
});
storageNode('move').addEventListener('submit',async event=>{
 event.preventDefault();const destination=storageNode('destination').value.trim();
 if(!destination||storageSubmitting||STORAGE_WORKING.includes(storageState?.status))return;
 storageSubmitting=true;++storageRequest;storageControls();storageMessage(T('storagePending'),'running');
 try{
  await api('/api/storage/move',{destination});
  storageState={...storageState,status:'pending',target:destination};
 }catch(error){storageMessage(T('storageError')+(error.payload?.message?' '+error.payload.message:''),'notice');}
 finally{storageSubmitting=false;storageControls();await updateStorage();}
});
updateStorage();
setInterval(()=>{if(!document.hidden)updateStorage();},3000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)updateStorage();});
