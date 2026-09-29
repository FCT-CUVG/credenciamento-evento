const page=document.body.dataset.page;
const $=id=>document.getElementById(id);
const statusNames={registered:'Inscrito',prechecked:'Aguardando busca',searching:'Em busca',ready:'Pronto no guichê',completed:'Credenciado'};
const el=(tag,className,text)=>{const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node};
let session=null,items=[],pending=null,lookupToken=null,queueDesk='';
let eventName='Evento',eventRegistrationHints={};
async function loadEventConfig(){
  try{
    const event=await api('/api/event');
    eventName=event.name;
    eventRegistrationHints=event.registration_hints||{};
    for(const img of document.querySelectorAll('.site-brand img, .summary-logo')){img.src=event.logo;img.alt=event.name}
    for(const link of document.querySelectorAll('.site-brand'))link.setAttribute('aria-label',event.name+' — início do credenciamento');
  }catch(err){console.error('Configuração visual:',err)}
}
async function api(path,data){if(location.protocol==='file:')throw new Error('Abra o sistema pelo servidor em http://127.0.0.1:8000, não diretamente pelo arquivo.');const options={credentials:'same-origin'};if(data!==undefined){options.method='POST';options.headers={'Content-Type':'application/json'};if(session?.csrf)options.headers['X-CSRF-Token']=session.csrf;options.body=JSON.stringify(data)}let response;try{response=await fetch(path,options)}catch{throw new Error('Não foi possível conectar ao servidor. A página tentará novamente.')}const body=await response.json();if(!response.ok){const error=new Error(body.error||'Não foi possível concluir. Tente novamente.');error.status=response.status;throw error}return body}
function message(id,text){const node=$(id);if(!node)return;node.textContent=text;node.classList.toggle('hidden',!text)}
function show(id){for(const key of ['public-search','public-confirm','public-done','public-pending'])$(key).classList.toggle('hidden',key!==id)}
const publicTranslations={
  en:{brand:'Check-in',heroTitle:'HAVE YOU ARRIVED?',heroIntro:"Let the team know you've arrived. Then collect your badge and kit at the check-in desk.",searchTitle:'FIND YOUR REGISTRATION',searchIntro:'Use the CPF from your registration.',cpfLabel:'CPF',cpfPlaceholder:'000.000.000-00',searchButton:'Find registration',foundEyebrow:'REGISTRATION FOUND',confirmIntro:'Check your details. Confirm your arrival so the team can prepare your badge and kit.',confirmButton:'Confirm my arrival',noAffiliation:'No affiliation provided',retryButton:'Change search',doneEyebrow:'PRE-CHECK-IN COMPLETE',doneTitle:'ARRIVAL CONFIRMED',doneIntro:'Collect your badge and kit at this desk. Ask a volunteer if you need directions.',deskLabel:'Go to desk',doneRetry:'Find another registration',helpTitle:"Can't find your registration?",helpText:'Check the CPF and try again. If you need help, ask at the check-in desk or speak to a volunteer.',pendingEyebrow:'ARRIVAL RECORDED',pendingTitle:'PLEASE FIND A VOLUNTEER',pendingIntro:'Your arrival has been recorded. Please find a volunteer for guidance before collecting your badge and kit.'},
  'pt-BR':{brand:'Credenciamento',heroTitle:'VOCÊ CHEGOU?',heroIntro:'Avise à equipe que você chegou. Depois, retire seu crachá e kit no atendimento.',searchTitle:'LOCALIZE SUA INSCRIÇÃO',searchIntro:'Use o CPF informado na sua inscrição.',cpfLabel:'CPF',cpfPlaceholder:'000.000.000-00',searchButton:'Buscar inscrição',foundEyebrow:'INSCRIÇÃO LOCALIZADA',confirmIntro:'Confira seus dados. Ao confirmar, a equipe saberá que você chegou e poderá preparar seu crachá e kit.',confirmButton:'Confirmar minha chegada',noAffiliation:'Afiliação não informada',retryButton:'Corrigir a busca',doneEyebrow:'PRÉ-CHECK-IN CONCLUÍDO',doneTitle:'PRESENÇA CONFIRMADA',doneIntro:'Retire seu crachá e kit neste guichê. Se não souber para onde ir, peça orientação a um voluntário.',deskLabel:'Dirija-se ao guichê',doneRetry:'Consultar outra inscrição',helpTitle:'Não encontrou sua inscrição?',helpText:'Confira o CPF e tente novamente. Se precisar de ajuda, procure o atendimento ou um voluntário.',pendingEyebrow:'CHEGADA REGISTRADA',pendingTitle:'PROCURE UM VOLUNTÁRIO',pendingIntro:'Sua chegada foi registrada. Procure um voluntário para receber orientações antes de retirar seu crachá e kit.'}
};
// O resultado do pré-check-in fica no navegador da pessoa para sobreviver a um F5.
const publicStore={
  get(key){try{return JSON.parse(localStorage.getItem('checkin:'+key))}catch{return null}},
  set(key,value){try{localStorage.setItem('checkin:'+key,JSON.stringify(value))}catch{}},
  remove(key){try{localStorage.removeItem('checkin:'+key)}catch{}}
};
const PUBLIC_RESULT_TTL=12*60*60*1000;
let publicAffiliation=null;
let publicLanguage=['en','pt-BR'].includes(publicStore.get('language'))?publicStore.get('language'):'en',publicErrorState=null,publicDesk=null;
function renderPublicDesk(){
  if(!publicDesk)return;
  const pt=publicLanguage==='pt-BR';
  const ranges=(publicDesk.ranges||[]).map(({from,to})=>from===to?from:`${from}–${to}`);
  $('done-desk-number').textContent=publicDesk.guiche;
  $('done-desk-range').textContent=publicDesk.priority?(pt?'Atendimento prioritário':'Priority service'):ranges.length?(pt?(ranges.length>1||ranges[0].length>1?'Letras ':'Letra '):(ranges.length>1||ranges[0].length>1?'Letters ':'Letter '))+ranges.join(', '):'';
  $('done-desk-range').classList.toggle('hidden',!publicDesk.priority&&!ranges.length);
}
function publicError(err,action){
  if(publicLanguage==='pt-BR')return err.message;
  if(err.status===429)return 'Too many attempts. Please wait a minute and try again.';
  if(action==='lookup'&&err.status===400)return 'Enter a valid CPF with 11 digits.';
  if(action==='lookup'&&err.status===404)return 'Registration not found. Check the CPF or ask the team for help.';
  if(action==='precheck'&&err.status===400)return 'This search has expired. Please try again.';
  if(action==='precheck'&&err.status===404)return 'Registration not found. Please search again.';
  return 'Something went wrong. Please try again.';
}
function setPublicLanguage(language){
  publicLanguage=language;
  document.documentElement.lang=language;
  document.title=(language==='en'?'Check-in | ':'Pré-check-in | ')+eventName;
  document.querySelector('.site-brand').setAttribute('aria-label',eventName+(language==='en'?' — check-in home':' — início do credenciamento'));
  for(const node of document.querySelectorAll('[data-i18n]')){
    const key=node.dataset.i18n;
    node.textContent=key==='searchIntro'&&eventRegistrationHints[language]
      ? eventRegistrationHints[language] : publicTranslations[language][key];
  }
  for(const node of document.querySelectorAll('[data-i18n-placeholder]'))node.placeholder=publicTranslations[language][node.dataset.i18nPlaceholder];
  $('language-switch').textContent=language==='en'?'Português (BR)':'English';
  if(publicErrorState)message(publicErrorState.id,publicError(publicErrorState.err,publicErrorState.action));
  renderPublicDesk();
  renderPublicAffiliation();
}
function renderPublicAffiliation(){
  if(publicAffiliation===null)return;
  const node=$('found-affiliation');
  node.textContent=publicAffiliation||publicTranslations[publicLanguage].noAffiliation;
  node.classList.toggle('affiliation-missing',!publicAffiliation);
}
function setPublicError(id,err,action){publicErrorState={id,err,action};message(id,publicError(err,action))}
function showPublicResult(result){
  if(result.needs_guidance){publicDesk=null;$('pending-name').textContent=result.name;show('public-pending');return}
  $('done-name').textContent=result.name;publicDesk={guiche:result.guiche,ranges:result.guiche_ranges,priority:result.guiche_priority};renderPublicDesk();show('public-done');
}
function initPublic(){
  const form=$('lookup-form');
  const cpfInput=form.elements.namedItem('cpf'),nameInput=form.elements.namedItem('name'),emailInput=form.elements.namedItem('email');
  const alternativeButton=$('lookup-alternative'),alternativeFields=$('lookup-alternative-fields');
  const renderAlternative=()=>{
    const portuguese=publicLanguage==='pt-BR';
    alternativeButton.textContent=portuguese?'ou use seu nome completo e e-mail':'or use your full name and e-mail';
    $('lookup-name-label').textContent=portuguese?'Nome completo':'Full name';
    $('lookup-email-label').textContent=portuguese?'E-mail':'E-mail';
  };
  const resetLookupMethod=()=>{alternativeFields.classList.add('hidden');cpfInput.required=true;nameInput.required=false;emailInput.required=false;};
  alternativeButton.addEventListener('click',()=>{alternativeFields.classList.remove('hidden');cpfInput.required=false;nameInput.required=true;emailInput.required=true;cpfInput.value='';nameInput.focus()});
  $('language-switch').addEventListener('click',()=>{setPublicLanguage(publicLanguage==='en'?'pt-BR':'en');publicStore.set('language',publicLanguage);renderAlternative()});
  renderAlternative();
  form.addEventListener('submit',async ev=>{
    ev.preventDefault();publicErrorState=null;message('lookup-error','');
    const button=form.querySelector('button[type="submit"]');button.disabled=true;
    try{
      const data=await api('/api/lookup',{cpf:cpfInput.value.trim(),name:nameInput.value.trim(),email:emailInput.value.trim()});
      lookupToken=data.token||null;
      $('found-name').textContent=data.name;
      publicAffiliation=data.affiliation||'';renderPublicAffiliation();
      show('public-confirm');
    }catch(err){setPublicError('lookup-error',err,'lookup')}finally{button.disabled=false}
  });
  $('precheck-button').addEventListener('click',async()=>{
    const button=$('precheck-button');button.disabled=true;publicErrorState=null;message('confirm-error','');
    try{const data=await api('/api/precheck',{token:lookupToken});
      const result={name:data.name,needs_guidance:data.needs_guidance,guiche:data.guiche,guiche_ranges:data.guiche_ranges,guiche_priority:data.guiche_priority,saved_at:Date.now()};
      publicStore.set('result',result);showPublicResult(result);
    }
    catch(err){setPublicError('confirm-error',err,'precheck')}finally{button.disabled=false}
  });
  const retry=()=>{publicStore.remove('result');form.reset();resetLookupMethod();lookupToken=null;publicDesk=null;publicErrorState=null;message('lookup-error','');message('confirm-error','');show('public-search');cpfInput.focus()};
  $('retry-button').addEventListener('click',retry);$('done-retry').addEventListener('click',retry);$('pending-retry').addEventListener('click',retry);
  const saved=publicStore.get('result');
  if(saved&&typeof saved.name==='string'&&Date.now()-saved.saved_at<PUBLIC_RESULT_TTL)showPublicResult(saved);
  else publicStore.remove('result');
}
async function initLogin(){const form=$('login-form');form.addEventListener('submit',async ev=>{ev.preventDefault();message('login-error','');const button=form.querySelector('button');button.disabled=true;try{const data=await api('/api/login',{username:form.elements.namedItem('username').value,password:form.elements.namedItem('password').value});location.href=data.role==='volunteer'?'/busca':data.role==='attendant'?'/fila':'/painel'}catch(err){message('login-error',err.message)}finally{button.disabled=false}})}
async function renderTeamNav(){
  const nav=$('team-links');
  if(!nav)return;
  nav.replaceChildren();
  const link=(label,href)=>{const a=el('a','',label);a.href=href;if(location.pathname===href)a.setAttribute('aria-current','page');return a};
  nav.append(link('Separação','/busca'));
  const menu=el('details','guiche-menu');
  menu.append(el('summary','','Guichês'));
  const choices=el('div','guiche-options');
  choices.append(link('Todos os guichês','/fila?guiche=all'));
  try{
    const data=await api('/api/guiches');
    for(const desk of data.guiches){
      const range=desk.priority?(/priorid/i.test(desk.id)?'':' · Prioridade'):desk.ranges.length?' · '+desk.ranges.join(', '):'';
      choices.append(link('Guichê '+desk.id+range,'/fila?guiche='+encodeURIComponent(desk.id)));
    }
  }catch(err){message('page-error',err.message)}
  menu.append(choices);
  nav.append(menu,link('Painel resumido','/painel/resumo'));
  if(session.user.role==='admin')nav.append(link('Painel detalhado','/painel'));
}
async function requireSession(roles){session=await api('/api/me');if(!session.user){location.href='/login';return false}if(!roles.includes(session.user.role)){location.href=session.user.role==='volunteer'?'/busca':session.user.role==='attendant'?'/fila':'/painel';return false}const label=$('user-label');if(label)label.textContent=session.user.username;$('logout')?.addEventListener('click',async()=>{await api('/api/logout',{});location.href='/login'});await renderTeamNav();return true}
function ask(action, item) {
  const titles = {
    ready: 'Material no guichê?', complete: 'Confirmar retirada?',
    undo_ready: 'Voltar para a busca?', undo_complete: 'Desfazer retirada?'
  };
  const descriptions = {
    ready: `Confirme que o crachá e o kit de ${item.name} já estão no guichê ${item.guiche}.`,
    complete: `Confirme que ${item.name} recebeu o crachá e o kit no guichê ${item.guiche}.`,
    undo_ready: `${item.name} sairá dos prontos no guichê ${item.guiche} e voltará para ${item.claimed_by ? 'Em busca' : 'Aguardando busca'}.`,
    undo_complete: `A retirada de ${item.name} será desfeita. O nome voltará para Prontos para retirada no guichê ${item.guiche}.`
  };
  pending = { action, item };
  $('dialog-title').textContent = titles[action];
  $('dialog-description').textContent = descriptions[action];
  $('dialog-confirm').textContent = action.startsWith('undo_') ? 'Voltar' : action === 'complete' ? 'Confirmar retirada' : 'Confirmar';
  $('confirm-dialog').showModal();
}

function initDialog(refresh) {
  $('dialog-cancel').addEventListener('click', () => $('confirm-dialog').close());
  $('dialog-confirm').addEventListener('click', async () => {
    if (!pending) return;
    const button = $('dialog-confirm');
    button.disabled = true;
    try {
      await api('/api/action/' + pending.action, { id: pending.item.id });
      $('confirm-dialog').close();
      await refresh();
    } catch (err) {
      $('confirm-dialog').close();
      await refresh();
      message('page-error', err.message);
    } finally {
      button.disabled = false;
      pending = null;
    }
  });
}

async function runAction(action, item, button, refresh) {
  button.disabled = true;
  try {
    await api('/api/action/' + action, { id: item.id });
    await refresh();
  } catch (err) {
    await refresh();
    message('page-error', err.message);
  } finally {
    button.disabled = false;
  }
}

function queueCard(item, mode, refresh) {
  const volunteer = mode === 'volunteer';
  const attendantReady = mode === 'attendant' && item.status === 'ready';
  const split = volunteer || attendantReady;
  const card = el('article', 'queue-card' + (volunteer ? ' volunteer-card' : attendantReady ? ' attendant-card' : item.status === 'completed' ? ' completed-card' : ''));
  const content = split ? el('div', 'queue-card-main') : card;
  if (split) card.append(content);
  const name = el('div', 'name', item.name);
  if (item.priority) name.append(el('span', 'pill priority', 'Prioridade'));
  content.append(name);
  // Na tela de um guichê específico, o número do guichê é redundante: mostra o início do CPF.
  const showCpf = mode === 'attendant' && queueDesk;
  const meta = el('div', 'meta');
  // Depois da retirada, o CPF não é mais necessário; em "Todos os guichês" o guichê continua visível.
  if (!split && !showCpf) meta.append(el('span', 'guiche', 'Guichê ' + item.guiche));
  if (item.claimed_by && item.status !== 'completed') meta.append(el('span', 'muted', item.claimed_by));
  if (item.status === 'completed' && item.completed_at) {
    const time = new Date(item.completed_at).toLocaleTimeString('pt-BR', {hour:'2-digit', minute:'2-digit'});
    meta.append(el('span', 'muted', 'Retirado às ' + time));
  }
  if (meta.childElementCount) content.append(meta);

  if (split) {
    const guiche = el('div', 'queue-guiche');
    // Nomes longos de guichê (ex.: "Prioridade") usam fonte menor para caber sem quebrar a palavra.
    const sized = value => el('strong', 'queue-guiche-value' + (value.length > 4 ? ' long' : value.length > 2 ? ' medium' : ''), value);
    if (showCpf) guiche.append(el('span', 'queue-guiche-label', item.cpf_prefix ? 'Início do CPF' : 'Sem CPF'), sized(item.cpf_prefix || '—'));
    else guiche.append(el('span', 'queue-guiche-label', 'Guichê'), sized(item.guiche));
    card.append(guiche);
  }

  if (mode === 'volunteer' && ['volunteer','admin'].includes(session.user.role) && item.status === 'prechecked') {
    const actions = el('div', 'queue-actions');
    const claim = el('button', 'button', 'Assumir busca');
    claim.addEventListener('click', () => runAction('claim', item, claim, refresh));
    actions.append(claim);
    content.append(actions);
  }
  if (mode === 'volunteer' && ['volunteer','admin'].includes(session.user.role) && item.status === 'searching' &&
      (item.claimed_by === session.user.username || session.user.role === 'admin')) {
    const actions = el('div', 'queue-actions');
    const release = el('button', 'button secondary small', 'Liberar busca');
    release.addEventListener('click', () => runAction('release', item, release, refresh));
    const ready = el('button', 'button', 'Pronto no guichê');
    ready.addEventListener('click', () => ask('ready', item));
    actions.append(release, ready);
    content.append(actions);
  }
  const canManageDesk = ['volunteer', 'admin'].includes(session.user.role) ||
    (session.user.role === 'attendant' && session.user.guiche === item.guiche);
  if (item.status === 'ready' && canManageDesk) {
    const actions = el('div', 'queue-actions');
    const back = el('button', 'button secondary small', 'Voltar para busca');
    back.addEventListener('click', () => ask('undo_ready', item));
    actions.append(back);
    if (mode === 'attendant') {
      const complete = el('button', 'button', 'Confirmar retirada');
      complete.addEventListener('click', () => ask('complete', item));
      actions.append(complete);
    }
    content.append(actions);
  }
  if (mode === 'attendant' && item.status === 'completed' && canManageDesk) {
    const back = el('button', 'button secondary small', 'Voltar para prontos');
    back.addEventListener('click', () => ask('undo_complete', item));
    content.append(back);
  }
  return card;
}

function renderQueue(mode, refresh) {
  const filter = ($('queue-filter').value || '').toLocaleLowerCase();
  const statuses = mode === 'attendant' ? ['ready', 'completed'] : ['prechecked', 'searching', 'ready'];
  for (const status of statuses) {
    const list = $('lane-' + status);
    list.replaceChildren();
    const rows = items.filter(item => item.status === status &&
      (`${item.name} ${item.guiche}`).toLocaleLowerCase().includes(filter));
    $('count-' + status).textContent = `(${rows.length})`;
    if (!rows.length) list.append(el('div', 'empty', status === 'ready' ? 'Ninguém aguardando retirada.' : status === 'completed' ? 'Nenhuma retirada confirmada.' : 'Nenhum participante nesta etapa.'));
    else rows.forEach(item => list.append(queueCard(item, mode, refresh)));
  }
}

async function initQueue(mode) {
  if (!await requireSession(['volunteer', 'attendant', 'admin'])) return;
  let guiche = '';
  if (mode === 'attendant') {
    const requested = new URLSearchParams(location.search).get('guiche') || '';
    guiche = requested === 'all' ? '' : requested || (session.user.role === 'attendant' ? session.user.guiche : '');
    queueDesk = guiche;
    $('guiche-title').textContent = guiche ? 'GUICHÊ ' + guiche : 'TODOS OS GUICHÊS';
    $('guiche-title').closest('.attendant-heading').classList.toggle('all-desks', !guiche);
    if (guiche) {
      try {
        const desks = (await api('/api/guiches')).guiches;
        const desk = desks.find(candidate => candidate.id === guiche);
        const ranges = desk?.ranges || [];
        $('guiche-range').textContent = desk?.priority ? 'Atendimento prioritário' : ranges.length
          ? 'Faixa de letras: ' + ranges.map(range => range.replace('–', ' a ')).join(' · ')
          : '';
      } catch (err) {
        $('guiche-range').textContent = 'Faixa de letras indisponível';
      }
    } else {
      $('guiche-range').textContent = 'Visualização de todos os guichês';
    }
  }
  async function refresh() {
    try {
      const query=mode==='attendant'
        ? '?view=attendant&guiche='+encodeURIComponent(guiche||'all')
        : guiche?'?guiche='+encodeURIComponent(guiche):'';
      const data = await api('/api/queue' + query);
      items = data.items;
      renderQueue(mode, refresh);
      $('last-update').textContent = 'Atualizado ' + new Date().toLocaleTimeString('pt-BR', {
        hour: '2-digit', minute: '2-digit', second: '2-digit'
      });
      message('page-error', '');
    } catch (err) {
      message('page-error', err.message);
    }
  }
  $('queue-filter').addEventListener('input', () => renderQueue(mode, refresh));
  initDialog(refresh);
  await refresh();
  setInterval(refresh, 5000);
}

const searchKey=value=>String(value||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase();
const statusTime=item=>item[{registered:'updated_at',prechecked:'prechecked_at',searching:'claimed_at',ready:'ready_at',completed:'completed_at'}[item.status]]||item.updated_at;
function movementTime(value){
  if(!value)return {label:'—',title:''};
  const date=new Date(value);
  if(Number.isNaN(date.getTime()))return {label:'—',title:''};
  const minutes=Math.max(0,Math.floor((Date.now()-date.getTime())/60000));
  const title=new Intl.DateTimeFormat('pt-BR',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(date).replace(',', ' às');
  if(minutes<60)return {label:`há ${minutes} ${minutes===1?'minuto':'minutos'}`,title};
  const hours=Math.floor(minutes/60);
  if(hours<24)return {label:`há ${hours} ${hours===1?'hora':'horas'}`,title};
  return {label:new Intl.DateTimeFormat('pt-BR',{day:'2-digit',month:'2-digit',year:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(date).replace(',', ' às'),title};
}
const hasPending=item=>!['registered','completed'].includes(item.status)&&(!item.paid||!item.affiliation.trim());
const statusKey=item=>item.status==='prechecked'&&hasPending(item)?'guidance':item.status;
const statusLabel=item=>statusKey(item)==='guidance'?'Orientação pendente':statusNames[item.status];
const statusSteps=[['registered','IN'],['prechecked','AB'],['searching','EB'],['ready','PR'],['completed','CR']];
function statusStepper(item){
  const wrap=el('div','stepper'),steps=el('ol','stepper-steps'),key=statusKey(item);
  const current=statusSteps.findIndex(([status])=>status===item.status),blocked=hasPending(item);
  wrap.setAttribute('role','group');wrap.setAttribute('aria-label','Situação de '+item.name);
  statusSteps.forEach(([status,short],index)=>{
    // Na segunda posição, OP (orientação pendente) substitui AB para quem chegou com pendência.
    const text=index===current&&key==='guidance'?'OP':short;
    const li=el('li',index<current?'done':index===current?'current':''),step=el('button','step'+(index===current?' status-'+key:''),text);
    const label=statusNames[status];
    step.type='button';step.dataset.participantId=item.id;step.dataset.status=status;
    step.disabled=blocked&&['searching','ready','completed'].includes(status);
    step.title=step.disabled?`${label} — resolva as pendências antes`:index===current?`${statusLabel(item)} (atual)`:`Mudar para ${label}`;
    step.setAttribute('aria-label',index===current?`${statusLabel(item)}, situação atual`:`Mudar para ${label}`);
    if(index===current)step.setAttribute('aria-current','step');
    li.append(step);steps.append(li);
  });
  wrap.append(steps,el('span','stepper-label status-'+key,statusLabel(item)));
  return wrap;
}
let dashboardPage=1,affiliationEditing=null;
const formatCpf=cpf=>/^\d{11}$/.test(cpf||'')?cpf.replace(/(\d{3})(\d{3})(\d{3})(\d{2})/,'$1.$2.$3-$4'):cpf||'—';
// Afiliação: texto fixo; quando falta, fica destacada e pode ser preenchida na própria linha.
function affiliationCell(item){
  const td=el('td','affiliation-cell');
  if(affiliationEditing?.id===item.id){
    const form=el('form','affiliation-form'),input=el('input','affiliation-input');
    input.name='affiliation';input.maxLength=200;input.value=affiliationEditing.value;input.placeholder='Afiliação';
    input.dataset.participantId=item.id;input.setAttribute('aria-label','Afiliação de '+item.name);
    const save=el('button','button small','Salvar'),cancel=el('button','button secondary small affiliation-cancel','Cancelar');
    save.type='submit';cancel.type='button';form.dataset.participantId=item.id;
    form.append(input,save,cancel);td.append(form);return td;
  }
  if(item.affiliation.trim())td.append(el('span','',item.affiliation));
  else td.append(el('span','pill warning','Não informada'));
  const edit=el('button','affiliation-edit',item.affiliation.trim()?'Editar':'Preencher');
  edit.type='button';edit.dataset.participantId=item.id;edit.setAttribute('aria-label',`${edit.textContent} afiliação de ${item.name}`);
  td.append(edit);return td;
}
function fillDeskFilter(desks){
  const select=$('desk-filter'),current=select.value;
  select.replaceChildren(el('option','','Todos'));select.firstChild.value='';
  for(const desk of desks){const option=el('option','','Guichê '+desk.id);option.value=desk.id;select.append(option)}
  select.value=desks.some(desk=>desk.id===current)?current:'';
}
function renderDashboard(){
  const text=searchKey($('dashboard-filter').value),status=$('status-filter').value;
  const priority=$('priority-filter').value,paid=$('payment-filter').value,desk=$('desk-filter').value;
  const body=$('dashboard-body');body.replaceChildren();
  const matches=items.filter(item=>(!status||statusKey(item)===status)&&
    (!priority||item.priority===(priority==='1'))&&(!paid||item.paid===(paid==='1'))&&(!desk||item.guiche===desk)&&
    searchKey(`${item.name} ${item.badge_name} ${item.email} ${item.cpf} ${item.affiliation} ${item.guiche}`).includes(text));
  $('list-count').textContent=`${matches.length} ${matches.length===1?'pessoa':'pessoas'}`;
  const size=Number($('page-size').value),pages=Math.max(1,Math.ceil(matches.length/size));
  dashboardPage=Math.min(Math.max(1,dashboardPage),pages);
  const first=(dashboardPage-1)*size,rows=matches.slice(first,first+size);
  $('page-range').textContent=matches.length?`Mostrando ${first+1}–${first+rows.length} de ${matches.length}`:'';
  $('page-label').textContent=`Página ${dashboardPage} de ${pages}`;
  $('page-prev').disabled=dashboardPage===1;$('page-next').disabled=dashboardPage===pages;
  if(!rows.length){const row=el('tr'),cell=el('td','muted','Nenhum participante encontrado.');cell.colSpan=8;row.append(cell);body.append(row);return}
  for(const item of rows){
    const tr=el('tr'),person=el('td','person-cell'),details=el('dl','person-details');
    for(const [label,value] of [['CPF',formatCpf(item.cpf)],['E-mail',item.email]])details.append(el('dt','',label),el('dd','',value));
    person.append(el('div','participant-name',item.name),details);
    tr.append(person,el('td','',item.badge_name),affiliationCell(item));
    const payment=el('td'),paymentSelect=el('select','payment-select '+(item.paid?'is-paid':'is-unpaid'));
    paymentSelect.setAttribute('aria-label','Alterar pagamento de '+item.name);paymentSelect.dataset.participantId=item.id;
    for(const [value,label] of [['0','Não pago'],['1','Pago']]){const option=el('option','',label);option.value=value;option.selected=(value==='1')===item.paid;paymentSelect.append(option)}
    const priority=el('td'),toggle=el('button','switch priority-switch');
    toggle.type='button';toggle.setAttribute('role','switch');toggle.setAttribute('aria-checked',String(item.priority));
    toggle.setAttribute('aria-label','Prioridade de '+item.name);toggle.dataset.participantId=item.id;
    toggle.append(el('span','switch-track'),el('span','',item.priority?'Sim':'Não'));
    priority.append(toggle);
    payment.append(paymentSelect);tr.append(payment,priority,el('td','',item.guiche));
    const td=el('td');td.append(statusStepper(item));
    const lastMovement=movementTime(statusTime(item)),movement=el('time','',lastMovement.label);
    if(lastMovement.title){movement.dateTime=new Date(statusTime(item)).toISOString();movement.title=lastMovement.title}
    const movementCell=el('td');movementCell.append(movement);
    tr.append(td,movementCell);body.append(tr);
  }
}
function toolStatus(id,value,error=false){const node=$(id);node.textContent=value;node.classList.toggle('error',error)}
async function copyText(value){
  if(navigator.clipboard&&window.isSecureContext)return navigator.clipboard.writeText(value);
  const field=document.createElement('textarea');field.value=value;field.setAttribute('readonly','');field.style.position='fixed';field.style.opacity='0';
  document.body.append(field);field.select();const copied=document.execCommand('copy');field.remove();
  if(!copied)throw new Error('Não foi possível copiar o Apps Script.');
}
function renderRangeRows(ranges){
  const host=$('range-rows');host.replaceChildren();
  ranges.forEach((range,index)=>{
    const row=el('div','range-row');
    for(const [key,label] of [['from','De'],['to','Até'],['guiche','Guichê']]){
      const field=el('label','field',label),input=el('input');
      input.name=key;input.value=range[key]||'';input.maxLength=key==='guiche'?24:1;
      input.setAttribute('aria-label',`${label}, faixa ${index+1}`);
      field.append(input);row.append(field);
    }
    const remove=el('button','button secondary small','Remover');remove.type='button';
    remove.setAttribute('aria-label',`Remover faixa ${index+1}`);
    remove.addEventListener('click',()=>{row.remove();toolStatus('ranges-status','')});
    row.append(remove);host.append(row);
  });
}
function initDashboardTabs(){
  const tabs=[...document.querySelectorAll('.dashboard-tabs [role="tab"]')];
  const select=(tab,focus=false)=>{
    for(const other of tabs){
      const selected=other===tab;
      other.setAttribute('aria-selected',String(selected));other.tabIndex=selected?0:-1;
      $(other.getAttribute('aria-controls')).hidden=!selected;
    }
    if(focus)tab.focus();
  };
  tabs.forEach((tab,index)=>{
    tab.addEventListener('click',()=>select(tab));
    tab.addEventListener('keydown',event=>{
      const step={ArrowRight:1,ArrowLeft:-1}[event.key];
      if(step){event.preventDefault();select(tabs[(index+step+tabs.length)%tabs.length],true)}
    });
  });
  // Os atalhos do topo abrem a aba correspondente.
  for(const link of document.querySelectorAll('.dashboard-shortcuts a[href^="#"]')){
    const tab=tabs.find(candidate=>'#'+candidate.id===link.getAttribute('href'));
    if(tab)link.addEventListener('click',()=>select(tab));
  }
  const initial=tabs.find(tab=>'#'+tab.id===location.hash);
  if(initial)select(initial);
}
function renderDeskStats(desks,pending){
  const list=$('desk-stats-list');list.replaceChildren();
  for(const desk of desks){
    const item=el('li','desk-stat'+(desk.priority?' priority':''));
    const detail=desk.priority?'Prioridade':desk.ranges.join(', ');
    item.append(el('strong','desk-stat-total',desk.total),el('span','desk-stat-name','Guichê '+desk.id));
    if(detail&&!(desk.priority&&/priorid/i.test(desk.id)))item.append(el('span','desk-stat-range',detail));
    list.append(item);
  }
  // Pendências não pertencem a um guichê: são atendidas com orientação, então ficam num card à parte.
  const item=el('li','desk-stat pending');
  item.append(el('strong','desk-stat-total',pending),el('span','desk-stat-name','Com pendência'),el('span','desk-stat-range','Pagamento ou afiliação'));
  list.append(item);
}
// Redesenha a tabela devolvendo o foco ao mesmo controle; só espera se um seletor estiver em uso.
function renderDashboardKeepingFocus(){
  const active=document.activeElement,body=$('dashboard-body');
  if(!body.contains(active)){renderDashboard();return}
  // Não interrompe quem está escolhendo num seletor ou digitando a afiliação.
  if(active instanceof HTMLSelectElement||active instanceof HTMLInputElement)return;
  const id=active.dataset.participantId,status=active.dataset.status,kind=active.classList[0];
  renderDashboard();
  const selector=`.${kind}[data-participant-id="${id}"]`+(status?`[data-status="${status}"]`:'');
  body.querySelector(selector)?.focus({preventScroll:true});
}
async function initDashboard(){
  if(!await requireSession(['admin']))return;
  initDashboardTabs();
  async function refresh(){
    try{
      const data=await api('/api/dashboard');items=data.items;
      $('stat-total').textContent=data.total;
      $('stat-prechecked').textContent=data.counts.prechecked+data.counts.searching+data.counts.ready+data.counts.completed;
      $('stat-searching').textContent=data.counts.searching;
      $('stat-ready').textContent=data.counts.ready;
      $('stat-completed').textContent=data.counts.completed;
      $('pending-shortcut-count').textContent=data.guidance_pending;
      renderDeskStats(data.desks,data.registration_pending);fillDeskFilter(data.desks);
      const updated='Atualizado às '+new Date().toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
      $('sync-status').textContent=updated+' · '+(!data.sheet_configured?'Google Sheets ainda não configurado; as alterações estão guardadas localmente.':data.sheet_pending?`${data.sheet_pending} alterações aguardando sincronização com Google Sheets.`:'Cópia local atualizada; sem alterações pendentes para Google Sheets.');
      renderDashboardKeepingFocus();
      message('page-error','');
    }catch(err){message('page-error',err.message)}
  }
  // Qualquer mudança de busca ou filtro volta para a primeira página.
  const firstPage=()=>{dashboardPage=1;renderDashboard()};
  $('dashboard-filter').addEventListener('input',firstPage);
  for(const id of ['status-filter','priority-filter','payment-filter','desk-filter','page-size'])$(id).addEventListener('change',firstPage);
  $('clear-filters').addEventListener('click',()=>{
    $('dashboard-filter').value='';
    for(const id of ['status-filter','priority-filter','payment-filter','desk-filter'])$(id).value='';
    firstPage();
  });
  // O atalho de pendências filtra a própria tabela.
  $('pending-shortcut').addEventListener('click',event=>{
    event.preventDefault();
    $('dashboard-filter').value='';
    for(const id of ['priority-filter','payment-filter','desk-filter'])$(id).value='';
    $('status-filter').value='guidance';firstPage();
    document.querySelector('.toolbar').scrollIntoView({block:'start',behavior:'smooth'});
  });
  const turnPage=step=>{dashboardPage+=step;renderDashboard();document.querySelector('.table-wrap').scrollIntoView({block:'start',behavior:'smooth'})};
  $('page-prev').addEventListener('click',()=>turnPage(-1));
  $('page-next').addEventListener('click',()=>turnPage(1));
  $('dashboard-body').addEventListener('change',async event=>{
    const select=event.target;
    if(!(select instanceof HTMLSelectElement)||!select.matches('.payment-select'))return;
    const item=items.find(candidate=>candidate.id===select.dataset.participantId);
    if(!item)return;
    const paid=select.value==='1';
    // Tira o foco do seletor para a atualização automática voltar a redesenhar a tabela.
    if(!confirm(`Marcar ${item.name} como ${paid?'pago':'não pago'}?`)){select.value=item.paid?'1':'0';select.blur();return}
    select.blur();
    try{await api('/api/participants/payment',{id:item.id,paid:paid?1:0});await refresh()}
    catch(err){select.value=item.paid?'1':'0';message('page-error',err.message)}
  });
  $('dashboard-body').addEventListener('input',event=>{if(event.target.matches('.affiliation-input')&&affiliationEditing)affiliationEditing.value=event.target.value});
  $('dashboard-body').addEventListener('submit',async event=>{
    event.preventDefault();
    const form=event.target,item=items.find(candidate=>candidate.id===form.dataset.participantId);
    if(!item)return;
    const button=form.querySelector('button[type="submit"]');button.disabled=true;
    try{await api('/api/participants/affiliation',{id:item.id,affiliation:form.elements.namedItem('affiliation').value});affiliationEditing=null;await refresh()}
    catch(err){message('page-error',err.message);button.disabled=false}
  });
  $('dashboard-body').addEventListener('keydown',event=>{if(event.key==='Escape'&&event.target.matches('.affiliation-input')){affiliationEditing=null;renderDashboard()}});
  $('dashboard-body').addEventListener('click',async event=>{
    const edit=event.target.closest('.affiliation-edit');
    if(edit){
      const item=items.find(candidate=>candidate.id===edit.dataset.participantId);
      affiliationEditing={id:item.id,value:item.affiliation};renderDashboard();
      document.querySelector(`.affiliation-input[data-participant-id="${item.id}"]`)?.focus();
      return;
    }
    if(event.target.closest('.affiliation-cancel')){affiliationEditing=null;renderDashboard();return}
    const toggle=event.target.closest('.priority-switch'),step=event.target.closest('.step');
    const item=items.find(candidate=>candidate.id===(toggle||step)?.dataset.participantId);
    if(!item)return;
    if(step){
      const status=step.dataset.status;
      if(status===item.status)return;
      if(!confirm(`Alterar a situação de ${item.name} para “${statusNames[status]}”?`))return;
      step.disabled=true;
      try{await api('/api/participants/status',{id:item.id,status});await refresh()}
      catch(err){message('page-error',err.message);step.disabled=false}
      return;
    }
    const priority=!item.priority;
    if(!confirm(priority?`Marcar ${item.name} como prioridade? A pessoa passa para o guichê de prioridade.`:`Remover a prioridade de ${item.name}? Se estiver no guichê de prioridade, volta para o guichê da sua letra.`))return;
    toggle.disabled=true;
    try{await api('/api/participants/priority',{id:item.id,priority:priority?1:0});await refresh()}
    catch(err){message('page-error',err.message)}finally{toggle.disabled=false}
  });
  $('participants-import-form').addEventListener('submit',async event=>{
    event.preventDefault();
    const form=event.currentTarget,file=$('participants-file').files[0],button=form.querySelector('button');
    if(!file)return;
    if(file.size>1_500_000){toolStatus('participants-import-status','Arquivo muito grande. O limite é 1,5 MB.',true);return}
    button.disabled=true;toolStatus('participants-import-status','Importando...');
    try{
      const result=await api('/api/participants/import',{filename:file.name,content:await file.text()});
      toolStatus('participants-import-status',`${result.read} ${result.read===1?'registro lido':'registros lidos'}; ${result.changed} ${result.changed===1?'criado ou atualizado':'criados ou atualizados'}.`);
      form.reset();await refresh();
    }catch(err){toolStatus('participants-import-status',err.message,true)}finally{button.disabled=false}
  });
  $('copy-google-sheets-script').addEventListener('click',async event=>{
    const button=event.currentTarget;button.disabled=true;toolStatus('google-sheets-status','Copiando Apps Script...');
    try{
      const result=await api('/api/google-sheets/script');await copyText(result.script);
      toolStatus('google-sheets-status','Apps Script copiado. Cole-o no editor da sua planilha.');
    }catch(err){toolStatus('google-sheets-status',err.message,true)}finally{button.disabled=false}
  });
  $('add-range').addEventListener('click',()=>{
    const ranges=[...$('range-rows').children].map(row=>Object.fromEntries(
      [...row.querySelectorAll('input')].map(input=>[input.name,input.value])));
    if(ranges.length>=26){toolStatus('ranges-status','O limite é 26 faixas.',true);return}
    renderRangeRows([...ranges,{from:'',to:'',guiche:''}]);
  });
  $('save-ranges').addEventListener('click',async event=>{
    const button=event.currentTarget;
    const ranges=[...$('range-rows').children].map(row=>Object.fromEntries(
      [...row.querySelectorAll('input')].map(input=>[input.name,input.value])));
    button.disabled=true;toolStatus('ranges-status','Salvando...');
    try{
      const result=await api('/api/guiches/config',{ranges,priority_guiche:$('priority-guiche').value});
      renderRangeRows(result.ranges);$('priority-guiche').value=result.priority_guiche;
      toolStatus('ranges-status','Guichês salvos. '+(result.updated?`${result.updated} ${result.updated===1?'participante mudou':'participantes mudaram'} de guichê.`:'Nenhum participante precisou mudar de guichê.'));
      await Promise.all([renderTeamNav(),refresh()]);
    }catch(err){toolStatus('ranges-status',err.message,true)}finally{button.disabled=false}
  });
  await refresh();
  try{const config=await api('/api/guiches/config');renderRangeRows(config.ranges);$('priority-guiche').value=config.priority_guiche}
  catch(err){toolStatus('ranges-status',err.message,true)}
  setInterval(refresh,10000);
}
async function initSummary(){
  const setNumber=(id,value)=>{const node=$(id);node.textContent=value;node.classList.toggle('many-digits',String(value).length>3)};
  async function refresh(){try{const data=await api('/api/dashboard/summary');setNumber('stat-total',data.total);setNumber('stat-arrived',data.arrived);setNumber('stat-completed',data.completed);message('page-error','')}catch(err){message('page-error',err.message)}}
  await refresh();setInterval(refresh,10000)
}
loadEventConfig().finally(()=>{if(page==='public'){setPublicLanguage(publicLanguage);initPublic()}if(page==='login')initLogin();if(page==='volunteer')initQueue('volunteer');if(page==='attendant')initQueue('attendant');if(page==='dashboard')initDashboard();if(page==='summary')initSummary()});
