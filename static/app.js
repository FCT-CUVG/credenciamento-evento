const page=document.body.dataset.page;
const $=id=>document.getElementById(id);
const statusNames={registered:'Inscrito',prechecked:'Aguardando busca',searching:'Em busca',ready:'Pronto no guichê',completed:'Credenciado'};
const el=(tag,className,text)=>{const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node};
let session=null,items=[],pending=null,lookupToken=null;
async function api(path,data){const options={credentials:'same-origin'};if(data!==undefined){options.method='POST';options.headers={'Content-Type':'application/json'};if(session?.csrf)options.headers['X-CSRF-Token']=session.csrf;options.body=JSON.stringify(data)}const response=await fetch(path,options);const body=await response.json();if(!response.ok)throw new Error(body.error||'Não foi possível concluir. Tente novamente.');return body}
function message(id,text){const node=$(id);if(!node)return;node.textContent=text;node.classList.toggle('hidden',!text)}
function show(id){for(const key of ['public-search','public-confirm','public-done'])$(key).classList.toggle('hidden',key!==id)}
function initPublic(){const form=$('lookup-form');form.addEventListener('submit',async ev=>{ev.preventDefault();message('lookup-error','');const button=form.querySelector('button');button.disabled=true;try{const data=await api('/api/lookup',{name:form.elements.namedItem('name').value,email:form.elements.namedItem('email').value});lookupToken=data.token;$('found-name').textContent=data.name;$('found-affiliation').textContent=data.affiliation||'';if(data.status!=='registered'){$('done-name').textContent=data.name;show('public-done')}else show('public-confirm')}catch(err){message('lookup-error',err.message)}finally{button.disabled=false}});$('precheck-button').addEventListener('click',async()=>{const button=$('precheck-button');button.disabled=true;message('confirm-error','');try{const data=await api('/api/precheck',{token:lookupToken});$('done-name').textContent=data.name;show('public-done')}catch(err){message('confirm-error',err.message)}finally{button.disabled=false}});const retry=()=>{form.reset();lookupToken=null;show('public-search');form.elements.namedItem('name').focus()};$('retry-button').addEventListener('click',retry);$('done-retry').addEventListener('click',retry)}
async function initLogin(){const form=$('login-form');form.addEventListener('submit',async ev=>{ev.preventDefault();message('login-error','');const button=form.querySelector('button');button.disabled=true;try{const data=await api('/api/login',{username:form.elements.namedItem('username').value,password:form.elements.namedItem('password').value});location.href=data.role==='volunteer'?'/busca':data.role==='attendant'?'/fila':'/painel'}catch(err){message('login-error',err.message)}finally{button.disabled=false}})}
async function renderTeamNav(){
  const nav=$('team-links');
  if(!nav)return;
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
  const titles = { ready: 'Material no guichê?', complete: 'Confirmar retirada?' };
  const descriptions = {
    ready: `Confirme que o crachá e o kit de ${item.name} já estão no guichê ${item.guiche}.`,
    complete: `Confirme que ${item.name} recebeu o crachá e o kit no guichê ${item.guiche}.`
  };
  pending = { action, item };
  $('dialog-title').textContent = titles[action];
  $('dialog-description').textContent = descriptions[action];
  $('dialog-confirm').textContent = action === 'complete' ? 'Confirmar retirada' : 'Confirmar';
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
  const card = el('article', 'queue-card');
  card.append(el('div', 'name', item.name));
  const meta = el('div', 'meta');
  meta.append(el('span', 'guiche', 'Guichê ' + item.guiche));
  if (item.claimed_by) meta.append(el('span', 'muted', 'Com ' + item.claimed_by));
  card.append(meta);

  if (mode === 'volunteer' && ['volunteer','admin'].includes(session.user.role) && item.status === 'prechecked') {
    const actions = el('div', 'queue-actions');
    const claim = el('button', 'button', 'Assumir busca');
    claim.addEventListener('click', () => runAction('claim', item, claim, refresh));
    actions.append(claim);
    card.append(actions);
  }
  if (mode === 'volunteer' && ['volunteer','admin'].includes(session.user.role) && item.status === 'searching' &&
      (item.claimed_by === session.user.username || session.user.role === 'admin')) {
    const actions = el('div', 'queue-actions');
    const release = el('button', 'button secondary small', 'Liberar busca');
    release.addEventListener('click', () => runAction('release', item, release, refresh));
    const ready = el('button', 'button', 'Pronto no guichê');
    ready.addEventListener('click', () => ask('ready', item));
    actions.append(release, ready);
    card.append(actions);
  }
  if (mode === 'attendant' && item.status === 'ready' &&
      (session.user.role === 'admin' || (session.user.role === 'attendant' && session.user.guiche === item.guiche))) {
    const button = el('button', 'button', 'Confirmar retirada');
    button.addEventListener('click', () => ask('complete', item));
    card.append(button);
  }
  return card;
}

function renderQueue(mode, refresh) {
  const filter = ($('queue-filter').value || '').toLocaleLowerCase();
  for (const status of ['prechecked', 'searching', 'ready']) {
    const list = $('lane-' + status);
    list.replaceChildren();
    const rows = items.filter(item => item.status === status &&
      (`${item.name} ${item.guiche}`).toLocaleLowerCase().includes(filter));
    $('count-' + status).textContent = `(${rows.length})`;
    if (!rows.length) list.append(el('div', 'empty', 'Nenhum participante nesta etapa.'));
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
  }
  async function refresh() {
    try {
      const query=guiche?'?guiche='+encodeURIComponent(guiche):mode==='attendant'?'?guiche=all':'';
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

function renderDashboard(){const text=($('dashboard-filter').value||'').toLocaleLowerCase(),status=$('status-filter').value;const body=$('dashboard-body');body.replaceChildren();const rows=items.filter(item=>(!status||item.status===status)&&(`${item.name} ${item.email} ${item.guiche}`).toLocaleLowerCase().includes(text));$('list-count').textContent=`${rows.length} pessoas`;for(const item of rows){const tr=el('tr');for(const value of [item.name,item.email,item.affiliation||'—',item.guiche])tr.append(el('td','',value));const td=el('td');td.append(el('span','pill '+item.status,statusNames[item.status]));tr.append(td,el('td','',item.claimed_by||'—'));body.append(tr)}}
async function initDashboard(){if(!await requireSession(['admin']))return;async function refresh(){try{const data=await api('/api/dashboard');items=data.items;$('stat-total').textContent=data.total;$('stat-prechecked').textContent=data.counts.prechecked+data.counts.searching+data.counts.ready+data.counts.completed;$('stat-searching').textContent=data.counts.searching;$('stat-ready').textContent=data.counts.ready;$('stat-completed').textContent=data.counts.completed;$('sync-status').textContent=!data.sheet_configured?'Google Sheets ainda não configurado; as alterações estão guardadas localmente.':data.sheet_pending?`${data.sheet_pending} alterações aguardando sincronização com Google Sheets.`:'Cópia local atualizada; sem alterações pendentes para Google Sheets.';renderDashboard();message('page-error','')}catch(err){message('page-error',err.message)}}$('dashboard-filter').addEventListener('input',renderDashboard);$('status-filter').addEventListener('change',renderDashboard);await refresh();setInterval(refresh,10000)}
async function initSummary(){if(!await requireSession(['volunteer','attendant','admin']))return;async function refresh(){try{const data=await api('/api/dashboard/summary');$('stat-total').textContent=data.total;$('stat-arrived').textContent=data.arrived;$('stat-completed').textContent=data.completed;message('page-error','')}catch(err){message('page-error',err.message)}}await refresh();setInterval(refresh,10000)}
if(page==='public')initPublic();if(page==='login')initLogin();if(page==='volunteer')initQueue('volunteer');if(page==='attendant')initQueue('attendant');if(page==='dashboard')initDashboard();if(page==='summary')initSummary();
