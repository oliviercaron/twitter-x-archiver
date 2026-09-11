// Firefox et Safari exposent les API Promise via browser ; Chrome MV3 via chrome.
const EXT=globalThis.browser||globalThis.chrome;
const t=(key)=>EXT.i18n.getMessage(key)||key;
// Le balisage ne porte que des cles : la langue vient du navigateur.
for(const node of document.querySelectorAll('[data-i18n]'))node.textContent=t(node.dataset.i18n);
for(const node of document.querySelectorAll('[data-i18n-placeholder]'))node.placeholder=t(node.dataset.i18nPlaceholder);
document.documentElement.lang=EXT.i18n.getUILanguage().slice(0,2);
const $=s=>document.querySelector(s);
EXT.tabs.query({active:true,currentWindow:true}).then(([tab])=>{if(tab?.url&&/\/(?:[^/]+|i\/web)\/status\/\d+/.test(new URL(tab.url).pathname))$('#url').value=tab.url;}).catch(()=>{});
EXT.storage.local.get('includeReplies').then(v=>$('#include-replies').checked=v.includeReplies===true);
$('#include-replies').addEventListener('change',()=>EXT.storage.local.set({includeReplies:$('#include-replies').checked}));
// La liste vient du service local, avec le dernier choix en tete.
const NEW='\u0000new';
async function loadCategories(){
 let known=[],fallback='';
 try{const r=await EXT.runtime.sendMessage({type:'CATEGORIES'});
  if(r?.ok){known=(r.result.categories||[]).map(c=>c.category);fallback=r.result.default||'';}
 }catch{}
 const {category}=await EXT.storage.local.get('category');
 const chosen=category||fallback||known[0]||'';
 if(chosen&&!known.includes(chosen))known.unshift(chosen);
 const select=$('#category');
 select.replaceChildren();
 for(const c of known)select.append(new Option(c,c));
 select.append(new Option(t('popupCategoryNew'),NEW));
 select.value=known.includes(chosen)?chosen:(known[0]||NEW);
 toggleNew();
}
function toggleNew(){
 const custom=$('#category').value===NEW;
 $('#new-category').hidden=!custom;
 if(custom)$('#new-category').focus();
}
function chosenCategory(){
 return $('#category').value===NEW?$('#new-category').value.trim():$('#category').value;
}
$('#category').addEventListener('change',toggleNew);
loadCategories();

$('#archive').addEventListener('click',async()=>{$('#archive').disabled=true;try{const r=await EXT.runtime.sendMessage({type:'ARCHIVE',url:$('#url').value,note:$('#note').value,refresh:$('#refresh').checked,includeReplies:$('#include-replies').checked,category:chosenCategory()});if(!r?.ok)throw Error(r?.error||t('popupFailed'));$('#status').textContent=t(r.result.status==='done'?'popupAlready':'popupQueued');}catch(e){$('#status').textContent=e.message;}finally{$('#archive').disabled=false;}});
