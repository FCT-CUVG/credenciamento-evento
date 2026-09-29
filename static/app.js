const page=document.body.dataset.page;
const $=id=>document.getElementById(id);
const statusNames={registered:'Inscrito',prechecked:'Aguardando busca',searching:'Em busca',ready:'Pronto no guichê',completed:'Credenciado'};
const el=(tag,className,text)=>{const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node};
let session=null,items=[],pending=null,lookupToken=null;
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
function show(id){for(const key of ['public-search','public-confirm','public-done'])$(key).classList.toggle('hidden',key!==id)}
const publicTranslations={
  en:{brand:'Check-in',heroTitle:'HAVE YOU ARRIVED?',heroIntro:"Let the team know you've arrived. Then collect your badge and kit at the check-in desk.",searchTitle:'FIND YOUR REGISTRATION',searchIntro:'Use the CPF from your registration.',cpfLabel:'CPF',cpfPlaceholder:'000.000.000-00',searchButton:'Find registration',foundEyebrow:'REGISTRATION FOUND',confirmIntro:'Check your details. Confirm your arrival so the team can prepare your badge and kit.',confirmButton:'Confirm my arrival',retryButton:'Change search',doneEyebrow:'PRE-CHECK-IN COMPLETE',doneTitle:'ARRIVAL CONFIRMED',doneIntro:'Visit the check-in desk to collect your badge and kit. Ask a volunteer if you need directions.',doneRetry:'Find another registration',helpTitle:"Can't find your registration?",helpText:'Check the CPF and try again. If you need help, ask at the check-in desk or speak to a volunteer.'},
  'pt-BR':{brand:'Credenciamento',heroTitle:'VOCÊ CHEGOU?',heroIntro:'Avise à equipe que você chegou. Depois, retire seu crachá e kit no atendimento.',searchTitle:'LOCALIZE SUA INSCRIÇÃO',searchIntro:'Use o CPF informado na sua inscrição.',cpfLabel:'CPF',cpfPlaceholder:'000.000.000-00',searchButton:'Buscar inscrição',foundEyebrow:'INSCRIÇÃO LOCALIZADA',confirmIntro:'Confira seus dados. Ao confirmar, a equipe saberá que você chegou e poderá preparar seu crachá e kit.',confirmButton:'Confirmar minha chegada',retryButton:'Corrigir a busca',doneEyebrow:'PRÉ-CHECK-IN CONCLUÍDO',doneTitle:'PRESENÇA CONFIRMADA',doneIntro:'Procure o atendimento do credenciamento para retirar seu crachá e kit. Se não souber para onde ir, peça orientação a um voluntário.',doneRetry:'Consultar outra inscrição',helpTitle:'Não encontrou sua inscrição?',helpText:'Confira o CPF e tente novamente. Se precisar de ajuda, procure o atendimento ou um voluntário.'}
};
let publicLanguage='en',publicErrorState=null;
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
}
function setPublicError(id,err,action){publicErrorState={id,err,action};message(id,publicError(err,action))}
function initPublic(){
  const form=$('lookup-form');
  $('language-switch').addEventListener('click',()=>setPublicLanguage(publicLanguage==='en'?'pt-BR':'en'));
  form.addEventListener('submit',async ev=>{
    ev.preventDefault();publicErrorState=null;message('lookup-error','');
    const button=form.querySelector('button');button.disabled=true;
    try{
      const data=await api('/api/lookup',{cpf:form.elements.namedItem('cpf').value.trim()});
      lookupToken=data.token;$('found-name').textContent=data.name;$('found-affiliation').textContent=data.affiliation||'';
      if(data.status!=='registered'){$('done-name').textContent=data.name;show('public-done')}else show('public-confirm');
    }catch(err){setPublicError('lookup-error',err,'lookup')}finally{button.disabled=false}
  });
  $('precheck-button').addEventListener('click',async()=>{
    const button=$('precheck-button');button.disabled=true;publicErrorState=null;message('confirm-error','');
    try{const data=await api('/api/precheck',{token:lookupToken});$('done-name').textContent=data.name;show('public-done')}
    catch(err){setPublicError('confirm-error',err,'precheck')}finally{button.disabled=false}
  });
  const retry=()=>{form.reset();lookupToken=null;publicErrorState=null;message('lookup-error','');message('confirm-error','');show('public-search');form.elements.namedItem('cpf').focus()};
  $('retry-button').addEventListener('click',retry);$('done-retry').addEventListener('click',retry);
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
      const range=desk.ranges.length?' · '+desk.ranges.join(', '):'';
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
  content.append(el('div', 'name', item.name));
  const meta = el('div', 'meta');
  if (!split) meta.append(el('span', 'guiche', 'Guichê ' + item.guiche));
  if (item.claimed_by && item.status !== 'completed') meta.append(el('span', 'muted', item.claimed_by));
  if (item.status === 'completed' && item.completed_at) {
    const time = new Date(item.completed_at).toLocaleTimeString('pt-BR', {hour:'2-digit', minute:'2-digit'});
    meta.append(el('span', 'muted', 'Retirado às ' + time));
  }
  if (meta.childElementCount) content.append(meta);

  if (split) {
    const guiche = el('div', 'queue-guiche');
    guiche.append(el('span', 'queue-guiche-label', 'Guichê'), el('strong', 'queue-guiche-value', item.guiche));
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
    $('guiche-title').textContent = guiche ? 'GUICHÊ ' + guiche : 'TODOS OS GUICHÊS';
    $('guiche-title').closest('.attendant-heading').classList.toggle('all-desks', !guiche);
    if (guiche) {
      try {
        const desks = (await api('/api/guiches')).guiches;
        const ranges = desks.find(desk => desk.id === guiche)?.ranges || [];
        $('guiche-range').textContent = ranges.length
          ? 'Faixa de letras: ' + ranges.map(range => range.replace('–', ' a ')).join(' · ')
          : 'Faixa de letras não configurada';
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
function renderDashboard(){
  const text=searchKey($('dashboard-filter').value),status=$('status-filter').value;
  const body=$('dashboard-body');body.replaceChildren();
  const rows=items.filter(item=>(!status||item.status===status)&&
    searchKey(`${item.name} ${item.badge_name} ${item.email} ${item.cpf} ${item.affiliation} ${item.guiche}`).includes(text));
  $('list-count').textContent=`${rows.length} ${rows.length===1?'pessoa':'pessoas'}`;
  if(!rows.length){const row=el('tr'),cell=el('td','muted','Nenhum participante encontrado.');cell.colSpan=11;row.append(cell);body.append(row);return}
  for(const item of rows){
    const tr=el('tr');
    for(const value of [item.name,item.badge_name,item.email,item.cpf,item.affiliation||'—'])tr.append(el('td','',value));
    const payment=el('td'),paymentSelect=el('select','payment-select');
    paymentSelect.setAttribute('aria-label','Alterar pagamento de '+item.name);paymentSelect.dataset.participantId=item.id;
    for(const [value,label] of [['0','Não pago'],['1','Pago']]){const option=el('option','',label);option.value=value;option.selected=(value==='1')===item.paid;paymentSelect.append(option)}
    payment.append(paymentSelect);tr.append(payment,el('td','',item.priority?'Prioridade':'Não'),el('td','',item.guiche));
    const td=el('td'),select=el('select','status-select');
    select.setAttribute('aria-label','Alterar situação de '+item.name);select.dataset.participantId=item.id;
    for(const [status,label] of Object.entries(statusNames)){const option=el('option','',label);option.value=status;option.selected=status===item.status;select.append(option)}
    td.append(select);
    const lastMovement=movementTime(statusTime(item)),movement=el('time','',lastMovement.label);
    if(lastMovement.title){movement.dateTime=new Date(statusTime(item)).toISOString();movement.title=lastMovement.title}
    tr.append(td,el('td','',item.claimed_by||'—'),movement);body.append(tr);
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
async function initDashboard(){
  if(!await requireSession(['admin']))return;
  async function refresh(){
    try{
      const data=await api('/api/dashboard');items=data.items;
      $('stat-total').textContent=data.total;
      $('stat-prechecked').textContent=data.counts.prechecked+data.counts.searching+data.counts.ready+data.counts.completed;
      $('stat-searching').textContent=data.counts.searching;
      $('stat-ready').textContent=data.counts.ready;
      $('stat-completed').textContent=data.counts.completed;
      $('sync-status').textContent=!data.sheet_configured?'Google Sheets ainda não configurado; as alterações estão guardadas localmente.':data.sheet_pending?`${data.sheet_pending} alterações aguardando sincronização com Google Sheets.`:'Cópia local atualizada; sem alterações pendentes para Google Sheets.';
      renderDashboard();message('page-error','');
    }catch(err){message('page-error',err.message)}
  }
  $('dashboard-filter').addEventListener('input',renderDashboard);
  $('status-filter').addEventListener('change',renderDashboard);
  $('dashboard-body').addEventListener('change',async event=>{
    const select=event.target;
    if(!(select instanceof HTMLSelectElement)||!select.matches('.status-select, .payment-select'))return;
    const item=items.find(candidate=>candidate.id===select.dataset.participantId);
    if(select.matches('.payment-select')){
      const paid=select.value==='1';
      if(!confirm(`Marcar ${item.name} como ${paid?'pago':'não pago'}?`)){select.value=item.paid?'1':'0';return}
      try{await api('/api/participants/payment',{id:item.id,paid:paid?1:0});await refresh()}
      catch(err){select.value=item.paid?'1':'0';message('page-error',err.message)}
      return;
    }
    const status=select.value;
    if(!item)return;
    if(!confirm(`Alterar a situação de ${item.name} para “${statusNames[status]}”?`)){select.value=item.status;return}
    select.disabled=true;
    try{await api('/api/participants/status',{id:item.id,status});await refresh()}
    catch(err){select.value=item.status;message('page-error',err.message)}
    finally{select.disabled=false}
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
      const result=await api('/api/guiches/config',{ranges});
      renderRangeRows(result.ranges);toolStatus('ranges-status','Guichês salvos. Reimporte a lista para atualizar participantes já cadastrados.');
      await renderTeamNav();
    }catch(err){toolStatus('ranges-status',err.message,true)}finally{button.disabled=false}
  });
  await refresh();
  try{renderRangeRows((await api('/api/guiches/config')).ranges)}
  catch(err){toolStatus('ranges-status',err.message,true)}
  setInterval(refresh,10000);
}
async function initSummary(){
  const setNumber=(id,value)=>{const node=$(id);node.textContent=value;node.classList.toggle('many-digits',String(value).length>3)};
  async function refresh(){try{const data=await api('/api/dashboard/summary');setNumber('stat-total',data.total);setNumber('stat-arrived',data.arrived);setNumber('stat-completed',data.completed);message('page-error','')}catch(err){message('page-error',err.message)}}
  await refresh();setInterval(refresh,10000)
}
loadEventConfig().finally(()=>{if(page==='public'){setPublicLanguage(publicLanguage);initPublic()}if(page==='login')initLogin();if(page==='volunteer')initQueue('volunteer');if(page==='attendant')initQueue('attendant');if(page==='dashboard')initDashboard();if(page==='summary')initSummary()});
