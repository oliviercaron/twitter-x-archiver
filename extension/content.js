(() => {
  const EXT=globalThis.browser||globalThis.chrome;
  if(globalThis.__zeventArchiveInstalled)return;
  globalThis.__zeventArchiveInstalled=true;
  const states=new Map();
  const bindings=new WeakMap();
  // Recharger l'extension invalide le contexte des onglets deja ouverts :
  // EXT.i18n et EXT.runtime y jettent au lieu de repondre. Les textes
  // deja lus servent alors de secours, et le script s'arrete proprement.
  let uiLanguage='en';
  const t=(key,...args)=>globalThis.archiveMessage
    ? globalThis.archiveMessage(EXT,uiLanguage,key,args)
    : (()=>{try{return EXT.i18n.getMessage(key,args.length?args:undefined)||key;}catch{return key;}})();
  const perime=()=>{try{return !EXT.runtime?.id;}catch{return true;}};
  let arrete=false;
  let labels={queued:t('stateQueued'),fetching:t('stateFetching'),done:t('stateDone'),
                partial:t('statePartial'),retry:t('stateRetry'),unavailable:t('stateUnavailable')};
  // Une forme par etat : la couleur seule ne suffit pas, et un daltonien ne la lit pas.
  const icons={
    idle:'<svg viewBox="0 0 24 24" width="19" height="19" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M12 3v11m-4-4 4 4 4-4M5 14v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5"/></svg>',
    done:'<svg viewBox="0 0 24 24" width="19" height="19" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M20 6.5 9.5 17 4 11.5"/></svg>',
    warn:'<svg viewBox="0 0 24 24" width="19" height="19" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M12 8v5m0 3.5v.01M10.3 3.9 2.6 17.4A2 2 0 0 0 4.3 20.4h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/></svg>',
    busy:'<svg viewBox="0 0 24 24" width="19" height="19" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" d="M12 6v6l3.5 2"/><circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="1.8" opacity=".45"/></svg>'};
  const SHAPES={done:'done',partial:'warn',retry:'warn',unavailable:'warn',queued:'busy',fetching:'busy'};
  const CROSS='<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" d="M6 6l12 12M18 6 6 18"/></svg>';
  let RECHARGER=t('reloadTab');
  const CONFIRM_DELAY=4000;
  const OFFER_DELAY=7000;

  function parse(link){try{const u=new URL(link,location.href);if(!['x.com','www.x.com','twitter.com','www.twitter.com'].includes(u.hostname))return null;const m=u.pathname.match(/^\/(?:[A-Za-z0-9_]+|i\/web)\/status\/(\d+)/);return m?{id:m[1],url:`https://x.com/i/status/${m[1]}`}:null;}catch{return null;}}

  function ownLink(article){
    const header=[...article.querySelectorAll('[data-testid="User-Name"]')].find(n=>n.closest('article')===article);
    if(header){for(const time of header.querySelectorAll('time')){const a=time.closest('a[href]');if(a&&parse(a.href))return parse(a.href);}}
    const current=parse(location.href);
    if(current){for(const a of article.querySelectorAll('a[href]')){const p=parse(a.href);if(p?.id===current.id&&a.querySelector('time')&&a.closest('article')===article)return p;}}
    for(const time of article.querySelectorAll('time')){const a=time.closest('a[href]');if(!a||a.closest('article')!==article||a.closest('div[role="link"]'))continue;const p=parse(a.href);if(p)return p;}
    return null;
  }

  function paint(button,state){
    const status=state?.status;
    const text=status?labels[status]||status:t('buttonArchive');
    const aria=state?.error?t('tipStateError',text,state.error)
      :status==='done'?t('tipAlreadyArchived')
      :!status?t('tipArchive'):t('tipState',text);
    button.title=aria;button.setAttribute('aria-label',aria);
    button.dataset.state=status||'idle';
    button.innerHTML=icons[SHAPES[status]||'idle']+'<span></span>';
    button.querySelector('span').textContent=text;
    button.disabled=['queued','fetching'].includes(status);
  }

  function repaint(){for(const button of document.querySelectorAll('.zevent-archive-button'))paint(button,states.get(button.dataset.tweetId));
    for(const wrap of document.querySelectorAll('.zevent-archive-wrap'))paintDelete(wrap);}

  function paintDelete(wrap){
    const remove=wrap.querySelector('.zevent-delete-button');
    if(!remove)return;
    const archived=states.get(wrap.dataset.tweetId)?.status==='done';
    wrap.dataset.deletable=archived?'1':'0';
    if(!archived)resetDelete(remove);
  }

  function resetDelete(remove){
    clearTimeout(remove._timer);
    remove.dataset.armed='0';remove.disabled=false;
    remove.innerHTML=CROSS+'<span></span>';
    remove.title=t('tipDelete');
    remove.setAttribute('aria-label',remove.title);
  }

  function armDelete(remove){
    remove.dataset.armed='1';
    remove.innerHTML=CROSS+'<span>'+t('confirmDelete')+'</span>';
    remove.title=t('tipConfirmDelete');
    remove.setAttribute('aria-label',remove.title);
    // La confirmation retombe seule : un bouton arme oublie est un piege.
    remove._timer=setTimeout(()=>resetDelete(remove),CONFIRM_DELAY);
  }

  async function runDelete(remove,id){
    clearTimeout(remove._timer);
    remove.disabled=true;
    remove.innerHTML=CROSS+'<span>'+t('deleting')+'</span>';
    try{
      const answer=await send({type:'DELETE',id});
      if(answer?.left?.length)throw Error(t('leftBehind'));
      states.delete(id);
      repaint();
    }catch(error){
      const shared=error.code==='shared'||/partag|shared/i.test(error.message);
      remove.innerHTML=CROSS+'<span>'+(shared?t('deleteShared'):t('deleteFailed'))+'</span>';
      remove.title=shared?t('tipDeleteShared'):error.message;
      remove.setAttribute('aria-label',remove.title);
      setTimeout(()=>resetDelete(remove),4000);
    }finally{remove.disabled=false;}
  }

  async function send(message){
    if(perime()){stop();throw Error(RECHARGER);}
    let answer;
    try{answer=await EXT.runtime.sendMessage(message);}
    catch(error){stop();throw Error(RECHARGER);}
    if(!answer?.ok){const error=Error(answer?.error||t('errorNoExtension'));error.code=answer?.code;throw error;}
    return answer.result;
  }

  // Plus rien ne repond : les boutons le disent au lieu de rester muets,
  // et le script cesse de sonder pour ne pas remplir la console d'erreurs.
  function stop(){
    if(arrete)return;
    arrete=true;
    try{observer.disconnect();}catch{}
    try{clearInterval(sondage);}catch{}
    for(const vieux of document.querySelectorAll('.zevent-cat-bar'))vieux.remove();
    for(const wrap of document.querySelectorAll('.zevent-archive-wrap')){
      const bouton=wrap.querySelector('.zevent-archive-button');
      if(bouton){
        bouton.disabled=true;bouton.dataset.state='stale';
        bouton.title=RECHARGER;bouton.setAttribute('aria-label',RECHARGER);
        const libelle=bouton.querySelector('span');
        if(libelle)libelle.textContent=RECHARGER;
      }
      const croix=wrap.querySelector('.zevent-delete-button');
      if(croix){croix.disabled=true;croix.dataset.armed='0';}
      wrap.dataset.deletable='0';
    }
  }

  // Sans cet appel, un post deja archive s'affichait comme neuf a chaque
  // rechargement de page : l'etat ne vivait que dans l'onglet courant.
  async function loadStates(){
    try{
      const known=await send({type:'STATES'});
      for(const [id,status] of Object.entries(known||{})){
        const current=states.get(id);
        if(!current||current.status!==status)states.set(id,{status});
      }
      repaint();
    }catch{}                       // serveur eteint : les boutons restent neutres
  }

  // La barre d'actions de X repartit ses enfants avec space-between. Ajouter le
  // bouton en dernier le pousse donc a l'extremite droite, detache des icones,
  // ce qui saute aux yeux sur la page d'un post seul, plus large. Il s'insere
  // apres une icone connue pour rester dans la serie.
  // Le bouton se place juste apres le j'aime, et nulle part ailleurs.
  //
  // Le signet servait d'abord de repere, mais il manque sur certains posts : le
  // bouton sautait alors au repere suivant et changeait de place d'un post a
  // l'autre. Le j'aime, lui, est present partout, et `scan` ne retient de toute
  // facon que les barres qui en contiennent un.
  const ANCHOR='[data-testid="like"],[data-testid="unlike"]';
  function place(group,wrap){
    const button=group.querySelector(ANCHOR);
    let node=button;
    while(node&&node.parentNode&&node.parentNode!==group)node=node.parentNode;
    if(node&&node.parentNode===group)node.after(wrap);
    else group.append(wrap);          // repere disparu : mieux vaut mal place qu absent
  }

  function scan(){if(perime())return stop();
   for(const article of document.querySelectorAll('article[data-testid="tweet"]')){
    const post=ownLink(article);const previous=bindings.get(article);
    if(previous&&(previous.id!==post?.id||!article.contains(previous.button))){previous.button.remove();bindings.delete(article);}
    if(!post||bindings.has(article))continue;
    const group=[...article.querySelectorAll('[role="group"]')].find(g=>g.closest('article')===article&&g.querySelector('[data-testid="like"],[data-testid="unlike"]')&&g.querySelector('[data-testid="retweet"],[data-testid="unretweet"]'));
    if(!group)continue;
    const wrap=document.createElement('div');wrap.className='zevent-archive-wrap';wrap.dataset.tweetId=post.id;
    const button=document.createElement('button');button.type='button';button.className='zevent-archive-button';button.dataset.tweetId=post.id;
    button.addEventListener('click',async event=>{event.preventDefault();event.stopPropagation();const state=states.get(post.id);if(state?.status==='done' && (!(await EXT.storage.local.get('includeReplies')).includeReplies || state.result?.discussion)){window.open('http://127.0.0.1:18765','_blank','noopener');return;}button.disabled=true;button.title=t('queuing');try{const job=await send({type:'ARCHIVE',url:post.url});states.set(post.id,job);paint(button,job);offer(wrap,post.id,job.selection?.category);}catch(error){const failed={status:'retry',error:error.message};states.set(post.id,failed);paint(button,failed);}});
    const remove=document.createElement('button');remove.type='button';remove.className='zevent-delete-button';
    remove.addEventListener('click',async event=>{event.preventDefault();event.stopPropagation();
      if(remove.dataset.armed==='1')return runDelete(remove,post.id);
      armDelete(remove);});
    wrap.addEventListener('mouseleave',()=>{if(remove.dataset.armed==='1')resetDelete(remove);});
    resetDelete(remove);
    paint(button,states.get(post.id));
    wrap.append(button,remove);paintDelete(wrap);
    place(group,wrap);bindings.set(article,{id:post.id,button:wrap});
  }}

  // Un post archive part dans la categorie courante. Le bandeau laisse sept
  // secondes pour le ranger ailleurs : les deux categories recentes, et de quoi
  // rechercher une catégorie existante ou en créer une sur place.
  //
  // Il vit dans le body, en position fixe, et non dans l'article. X empile ses
  // cellules de fil dans leurs propres contextes, et decoupe ce qui depasse :
  // une bulle posee dans l'article passait sous le post suivant, quel que soit
  // son z-index. Sortir du fil est la seule facon d'etre vu.
  let offerVersion=0,dismissOffer=()=>{};
  async function offer(wrap,id,category){
    const version=++offerVersion;dismissOffer();
    let recent;
    try{recent=await send({type:'RECENT',category});}
    catch{recent={current:category,others:[],known:category?[category]:[]};}
    if(version!==offerVersion)return;
    const bar=document.createElement('div');
    bar.className='zevent-cat-bar';bar.dataset.tweetId=id;
    const label=document.createElement('span');label.className='zevent-cat-label';
    label.textContent=category||recent.current?t('catCurrent',category||recent.current):t('catUnknown');
    label.setAttribute('role','status');
    const controls=document.createElement('div');controls.className='zevent-cat-controls';
    const prompt=document.createElement('span');prompt.textContent=t('catMoveInstead');
    controls.append(prompt);bar.append(label,controls);
    // Un clic dans le bandeau ne doit pas ouvrir le post sous-jacent.
    bar.addEventListener('click',event=>event.stopPropagation());

    function situer(){
      const zone=wrap.getBoundingClientRect();
      if(!zone.width&&!zone.height)return fermer();     // le post a quitte le fil
      // Le post est sorti de l'ecran : suivre une ancre invisible laisserait
      // un bandeau flottant sans rapport avec ce qu'on regarde.
      if(zone.bottom<0||zone.top>innerHeight)return fermer();
      const large=bar.offsetWidth||220,haut=bar.offsetHeight||34;
      const gauche=Math.min(Math.max(6,zone.left),Math.max(6,innerWidth-large-6));
      let sommet=zone.bottom+6;
      if(sommet+haut>innerHeight-6)sommet=Math.max(6,zone.top-haut-6);
      bar.style.left=gauche+'px';bar.style.top=sommet+'px';
    }
    function fermer(){
      removeEventListener('scroll',situer,true);
      removeEventListener('resize',situer);
      clearTimeout(timer);bar.remove();
    }

    let timer=null,fini=false;
    dismissOffer=fermer;
    const compte=delai=>{clearTimeout(timer);timer=setTimeout(fermer,delai);};
    async function ranger(name){
      clearTimeout(timer);
      for(const node of bar.querySelectorAll('button,input'))node.disabled=true;
      try{
        const moved=await send({type:'MOVE',id,category:name});
        label.textContent=t('catMoved',moved.category);
        delete label.dataset.state;
        controls.hidden=true;
      }catch{
        label.textContent=t('catMoveFailed');
        label.dataset.state='error';
        for(const node of bar.querySelectorAll('button,input'))node.disabled=false;
        situer();return;
      }
      // Une fois le rangement fait, le bandeau part quoi qu'il arrive :
      // desactiver des elements sous le curseur relance des survols, et le
      // compte a rebours ne se rearmerait jamais.
      fini=true;compte(1800);situer();
    }

    for(const name of (recent?.others||[]).filter(name=>name!==(category||recent.current)).slice(0,2)){
      const choice=document.createElement('button');
      choice.type='button';choice.className='zevent-cat-choice';
      choice.textContent=name;choice.title=t('catMoveTip',name);
      choice.addEventListener('click',event=>{
        event.preventDefault();event.stopPropagation();
        choice.dataset.chosen='1';ranger(name);
      });
      controls.append(choice);
    }

    const creer=document.createElement('button');
    creer.type='button';creer.className='zevent-cat-choice zevent-cat-new';
    creer.textContent=t('catNewShort');creer.title=t('catNewTip');
    creer.addEventListener('click',event=>{
      event.preventDefault();event.stopPropagation();
      clearTimeout(timer);                 // le champ ne doit pas disparaitre en cours de frappe
      const search=document.createElement('div');search.className='zevent-cat-search';
      const champ=document.createElement('input');
      champ.type='text';champ.className='zevent-cat-input';champ.autocomplete='off';
      champ.placeholder=t('catPromptShort');
      champ.setAttribute('aria-label',t('catNewTip'));
      champ.setAttribute('role','combobox');champ.setAttribute('aria-autocomplete','list');
      const list=document.createElement('div');list.className='zevent-cat-suggestions';
      list.id='archive-categories-'+id;list.setAttribute('role','listbox');
      list.setAttribute('aria-label',t('catSuggestions'));champ.setAttribute('aria-controls',list.id);
      search.append(champ,list);creer.replaceWith(search);
      const fold=name=>name.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase();
      const known=[...new Set(recent.known||recent.others||[])];
      let choices=[],active=-1;
      function highlight(){
        [...list.children].forEach((node,i)=>node.setAttribute('aria-selected',String(i===active)));
        if(active>=0)champ.setAttribute('aria-activedescendant',list.children[active].id);
        else champ.removeAttribute('aria-activedescendant');
      }
      function suggest(){
        const query=champ.value.trim(),key=fold(query);
        choices=known.filter(name=>fold(name).includes(key)).slice(0,5).map(name=>({name,label:name}));
        if(query&&!known.some(name=>fold(name)===key))choices.push({name:query,label:t('catCreate',query)});
        list.replaceChildren();active=-1;
        choices.forEach((item,i)=>{
          const option=document.createElement('button');option.type='button';option.tabIndex=-1;
          option.id=list.id+'-'+i;option.setAttribute('role','option');option.textContent=item.label;
          option.addEventListener('mousedown',event=>event.preventDefault());
          option.addEventListener('click',()=>ranger(item.name));list.append(option);
        });
        list.hidden=!choices.length;champ.setAttribute('aria-expanded',String(choices.length>0));
        highlight();situer();
      }
      champ.addEventListener('input',suggest);
      champ.addEventListener('keydown',key=>{
        key.stopPropagation();
        if(key.key==='ArrowDown'||key.key==='ArrowUp'){
          key.preventDefault();
          if(choices.length){active=(active+(key.key==='ArrowDown'?1:active<0?0:-1)+choices.length)%choices.length;highlight();}
        }else if(key.key==='Enter'){
          key.preventDefault();
          const typed=champ.value.trim();
          const name=active>=0?choices[active].name:known.find(name=>fold(name)===fold(typed))||typed;
          if(name)ranger(name);
        }else if(key.key==='Escape'){
          key.preventDefault();search.replaceWith(creer);creer.focus();situer();
        }
      });
      champ.addEventListener('keyup',key=>key.stopPropagation());
      champ.addEventListener('keypress',key=>key.stopPropagation());
      champ.focus();suggest();
    });
    controls.append(creer);
    bar.addEventListener('focusin',()=>{if(!fini)clearTimeout(timer);});
    bar.addEventListener('focusout',()=>{
      if(!fini&&!bar.querySelector('.zevent-cat-input'))compte(OFFER_DELAY);
    });

    document.body.append(bar);
    situer();
    addEventListener('scroll',situer,true);
    addEventListener('resize',situer);
    compte(OFFER_DELAY);
    // Le compte a rebours s'arrete tant que la souris est dessus, et tant qu'un
    // nom est en cours de saisie : personne ne doit courir apres un bandeau.
    bar.addEventListener('mouseenter',()=>{if(!fini)clearTimeout(timer);});
    bar.addEventListener('mouseleave',()=>{
      if(!fini&&!bar.querySelector('.zevent-cat-input')&&!bar.contains(document.activeElement))compte(2000);
    });
  }

  let scheduled=false;
  const observer=new MutationObserver(()=>{if(arrete||scheduled)return;scheduled=true;setTimeout(()=>{scheduled=false;scan();},120);});
  observer.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['href']});

  function applyLanguage(language){
    uiLanguage=language==='fr'?'fr':'en';
    labels={queued:t('stateQueued'),fetching:t('stateFetching'),done:t('stateDone'),
      partial:t('statePartial'),retry:t('stateRetry'),unavailable:t('stateUnavailable')};
    RECHARGER=t('reloadTab');
    repaint();
  }

  // The archive page stores the explicit FR/EN choice in extension storage.
  // New installations have no value yet and therefore stay in English.
  try{
    EXT.storage?.onChanged?.addListener((changes,area)=>{
      if(area==='local'&&changes.uiLanguage)applyLanguage(changes.uiLanguage.newValue);
    });
    EXT.storage?.local?.get('uiLanguage').then(value=>applyLanguage(value?.uiLanguage)).catch(()=>{});
  }catch{}
  scan();loadStates();

  const sondage=setInterval(async()=>{
    if(perime())return stop();
    for(const [id,state] of states){if(!['queued','fetching'].includes(state.status))continue;try{states.set(id,await send({type:'STATUS',id}));}catch(error){states.set(id,{status:'retry',error:error.message});}}
    repaint();
  },3000);
  // Les archivages faits ailleurs, page locale ou autre onglet, remontent ici.
  setInterval(loadStates,30000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)loadStates();});
})();
