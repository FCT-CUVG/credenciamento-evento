// Filas da equipe: Separação (/busca, voluntários) e guichês (/fila, atendentes).
import {$, api, clockTime, el, loadEventConfig, message, requireSession, searchKey, session} from './common.js';

const mode = document.body.dataset.page === 'attendant' ? 'attendant' : 'volunteer';
let items = [], desk = '', priorityDesk = '', withoutPriority = false, pendingAction = null;

const dialogTitles = {
  request: 'Solicitar separação?',
  ready: 'Material no guichê?',
  complete: 'Confirmar retirada?',
  undo_ready: 'Voltar para a busca?',
  undo_complete: 'Desfazer retirada?'
};

// Ações com efeito no atendimento pedem confirmação num diálogo.
function ask(action, item) {
  const descriptions = {
    request: item.pending
      ? `A inscrição de ${item.name} tem uma pendência. A chegada será registrada, mas o material só será ` +
        'separado depois que a coordenação resolver. Oriente a pessoa a procurar a coordenação.'
      : `A chegada de ${item.name} será registrada e o pedido vai para a separação (guichê ${targetDesk(item)}).`,
    ready: `Confirme que o crachá e o kit de ${item.name} já estão no guichê ${item.guiche}.`,
    complete: `Confirme que ${item.name} recebeu o crachá e o kit no guichê ${item.guiche}.`,
    undo_ready: `${item.name} sairá dos prontos no guichê ${item.guiche} e voltará para ` +
      `${item.claimed_by ? 'Em busca' : 'Aguardando busca'}.`,
    undo_complete: `A retirada de ${item.name} será desfeita. O nome voltará para Prontos para retirada no guichê ${item.guiche}.`
  };
  pendingAction = {action, item};
  $('dialog-title').textContent = dialogTitles[action];
  $('dialog-description').textContent = descriptions[action];
  $('dialog-confirm').textContent = action.startsWith('undo_') ? 'Voltar'
    : action === 'complete' ? 'Confirmar retirada'
    : action === 'request' ? (item.pending ? 'Registrar chegada' : 'Solicitar separação') : 'Confirmar';
  $('confirm-dialog').showModal();
  $('dialog-confirm').focus();
}

function initDialog(refresh) {
  $('dialog-cancel').addEventListener('click', () => $('confirm-dialog').close());
  // Na busca do guichê, o foco volta para o campo para a próxima pessoa.
  $('confirm-dialog').addEventListener('close', () => { if (mode === 'attendant') $('queue-filter').focus(); });
  $('dialog-confirm').addEventListener('click', async () => {
    if (!pendingAction) return;
    const button = $('dialog-confirm');
    button.disabled = true;
    try {
      const {action, item} = pendingAction;
      await api('/api/action/' + action, action === 'request' ? {id: item.id, guiche: desk} : {id: item.id});
      $('confirm-dialog').close();
      await refresh();
      if (action === 'request') {
        showNotice(`Chegada de ${item.name} registrada. Oriente a pessoa a procurar a coordenação para resolver a pendência.`);
      }
    } catch (err) {
      $('confirm-dialog').close();
      await refresh();
      message('page-error', err.message);
    } finally {
      button.disabled = false;
      pendingAction = null;
    }
  });
}

// Ações sem confirmação (assumir e liberar busca).
async function runAction(action, item, button, refresh) {
  button.disabled = true;
  try {
    await api('/api/action/' + action, {id: item.id});
    await refresh();
  } catch (err) {
    await refresh();
    message('page-error', err.message);
  } finally {
    button.disabled = false;
  }
}

function actionButton(label, className, onClick) {
  const button = el('button', className, label);
  button.addEventListener('click', () => onClick(button));
  return button;
}

function queueCard(item, refresh) {
  const {role, username} = session.user;
  const volunteer = mode === 'volunteer';
  const attendantReady = mode === 'attendant' && item.status === 'ready';
  const split = volunteer || attendantReady;
  const card = el('article', 'queue-card' + (volunteer ? ' volunteer-card' : attendantReady ? ' attendant-card'
    : item.status === 'completed' ? ' completed-card' : ''));
  const content = split ? el('div', 'queue-card-main') : card;
  if (split) card.append(content);

  // No guichê de prioridade, a cor do guichê já identifica a prioridade; o selo fica para quem está em outro guichê.
  const atPriorityDesk = Boolean(priorityDesk) && item.guiche === priorityDesk;
  if (atPriorityDesk) card.classList.add('priority-desk');
  const name = el('div', 'name', item.name);
  if (item.priority && !atPriorityDesk) name.append(el('span', 'pill priority', 'Prioridade'));
  content.append(name);
  if (item.affiliation) content.append(el('div', 'affiliation', item.affiliation));

  // Na tela de um guichê específico, o número do guichê é redundante: mostra o início do CPF.
  const showCpf = mode === 'attendant' && desk;
  const meta = el('div', 'meta');
  // Depois da retirada, o CPF não é mais necessário; em "Todos os guichês" o guichê continua visível.
  if (!split && !showCpf) meta.append(el('span', 'guiche', 'Guichê ' + item.guiche));
  if (item.claimed_by && item.status !== 'completed') meta.append(el('span', 'muted', item.claimed_by));
  // Na separação, a fila segue a ordem de chegada (prioridade primeiro): o horário ajuda a conferir.
  if (item.status === 'prechecked' && item.prechecked_at) meta.append(el('span', 'muted arrival', 'Chegou às ' + clockTime(item.prechecked_at).slice(0, 5)));
  if (item.status === 'completed' && item.completed_at) {
    const time = new Date(item.completed_at).toLocaleTimeString('pt-BR', {hour: '2-digit', minute: '2-digit'});
    meta.append(el('span', 'muted', 'Retirado às ' + time));
  }
  if (meta.childElementCount) content.append(meta);

  if (split) {
    const block = el('div', 'queue-guiche');
    // Nomes longos de guichê (ex.: "Prioridade") usam fonte menor para caber sem quebrar a palavra.
    const sized = value => el('strong',
      'queue-guiche-value' + (value.length > 4 ? ' long' : value.length > 2 ? ' medium' : ''), value);
    if (showCpf) {
      block.append(el('span', 'queue-guiche-label', item.cpf_prefix ? 'Início do CPF' : 'Sem CPF'),
        sized(item.cpf_prefix || '—'));
    } else {
      block.append(el('span', 'queue-guiche-label', 'Guichê'), sized(item.guiche));
    }
    card.append(block);
  }

  // Voluntários e atendentes podem separar kits e atender em qualquer guichê.
  const staff = ['volunteer', 'attendant', 'admin'].includes(role);
  const actions = el('div', 'queue-actions');
  if (volunteer && staff && item.status === 'prechecked') {
    actions.append(actionButton('Assumir busca', 'button', button => runAction('claim', item, button, refresh)));
  }
  if (volunteer && staff && item.status === 'searching' && (item.claimed_by === username || role === 'admin')) {
    actions.append(
      actionButton('Liberar busca', 'button secondary small', button => runAction('release', item, button, refresh)),
      actionButton('Pronto no guichê', 'button', () => ask('ready', item)));
  }
  const canManageDesk = staff;
  if (item.status === 'ready' && canManageDesk) {
    actions.append(actionButton('Voltar para busca', 'button secondary small', () => ask('undo_ready', item)));
    if (mode === 'attendant') actions.append(actionButton('Confirmar retirada', 'button', () => ask('complete', item)));
  }
  // Na separação, os botões ficam numa coluna à direita, na altura toda do cartão: o cartão
  // fica só com a altura do texto e cabem mais pessoas na tela.
  if (actions.childElementCount) (volunteer ? card : content).append(actions);
  if (volunteer) card.classList.toggle('no-actions', !actions.childElementCount);
  if (mode === 'attendant' && item.status === 'completed' && canManageDesk) {
    content.append(actionButton('Voltar para prontos', 'button secondary small', () => ask('undo_complete', item)));
  }
  return card;
}

const emptyText = {ready: 'Ninguém aguardando retirada.', completed: 'Nenhuma retirada confirmada.'};

function renderQueue(refresh) {
  const filter = searchKey($('queue-filter').value).split(/\s+/).filter(Boolean);
  const statuses = mode === 'attendant' ? ['ready', 'completed'] : ['prechecked', 'searching', 'ready'];
  for (const status of statuses) {
    const list = $('lane-' + status);
    list.replaceChildren();
    const rows = items.filter(item => item.status === status && (exactSearch()
      ? searchResults.some(result => result.id === item.id)
      : filter.every(word => searchKey(`${item.name} ${item.affiliation} ${item.guiche} ${item.cpf_prefix || ''}`)
        .includes(word))));
    $('count-' + status).textContent = `(${rows.length})`;
    if (!rows.length) list.append(el('div', 'empty', emptyText[status] || 'Nenhum participante nesta etapa.'));
    else rows.forEach(item => list.append(queueCard(item, refresh)));
  }
}

// Busca do guichê: acha qualquer inscrito (não só quem fez o pré-check-in) para pedir a separação.
const statusText = {
  registered: 'Ainda não chegou', prechecked: 'Aguardando busca', searching: 'Em busca',
  ready: 'Pronto no guichê', completed: 'Já credenciado'
};
let searchResults = [], activeResult = -1, searchSeq = 0, searchTimer = null, searchNotice = null;

// CPF completo ou e-mail: o navegador não tem esses dados, então as colunas usam o resultado do servidor.
const exactSearch = () => {
  const query = $('queue-filter').value.trim();
  return query.includes('@') || query.replace(/\D/g, '').length === 11;
};

// Aviso da última ação da busca; fica visível até a próxima busca digitada.
function showNotice(text, undo) {
  searchNotice = {text, undo};
  const summary = $('search-summary');
  summary.replaceChildren(text);
  if (undo) {
    const button = el('button', 'link-button', 'Desfazer');
    button.type = 'button';
    button.addEventListener('click', undo);
    summary.append(' ', button);
  }
}

// Enter (ou o botão) manda direto para a separação. Com pendência, confirma antes: a pessoa
// não entra na fila até a coordenação resolver.
// A pessoa passa a ser atendida no guichê da tela, onde ela está esperando ("Todos os guichês"
// mantém o guichê dela).
const targetDesk = item => desk || item.guiche;
// Quem já chegou, mas ainda espera a busca em outro guichê, pode ser trazido para este.
const canMoveHere = item => item.status === 'prechecked' && desk && item.guiche !== desk;

async function sendToSeparation(item, refresh) {
  if (item.pending && item.status === 'registered') {
    ask('request', item);
    return;
  }
  const moved = targetDesk(item) !== item.guiche;
  try {
    await api('/api/action/request', {id: item.id, guiche: desk});
    await refresh();
    const text = item.status === 'registered'
      ? `${item.name} foi para a separação no guichê ${targetDesk(item)}${moved ? ` (era o ${item.guiche})` : ''}.`
      : `${item.name} passou do guichê ${item.guiche} para o guichê ${targetDesk(item)}.`;
    showNotice(text, async () => {
      try {
        // Desfazer devolve a situação e o guichê anteriores.
        await api(item.status === 'registered' ? '/api/action/undo_request' : '/api/action/request',
          {id: item.id, guiche: item.guiche});
        await refresh();
        showNotice(`Pedido de ${item.name} desfeito.`);
      } catch (err) {
        showNotice(`Não foi possível desfazer: ${err.message}`);
      }
      $('queue-filter').focus();
    });
  } catch (err) {
    await refresh();
    message('page-error', err.message);
  }
}

function resultRow(item, index, refresh) {
  const row = el('div', 'search-result' + (item.pending ? ' pending' : '') + (index === activeResult ? ' active' : ''));
  row.id = 'result-' + item.id;
  row.setAttribute('role', 'option');
  row.setAttribute('aria-selected', String(index === activeResult));
  const main = el('div', 'search-result-main');
  const name = el('div', 'search-result-name', item.name);
  if (item.priority) name.append(el('span', 'pill priority', 'Prioridade'));
  main.append(name, el('div', 'search-result-affiliation', item.affiliation || 'Sem afiliação'));
  const facts = el('div', 'search-result-facts');
  facts.append(el('span', 'search-cpf', item.cpf_prefix ? 'CPF ' + item.cpf_prefix + '…' : 'Sem CPF'),
    el('span', '', 'Guichê ' + item.guiche),
    el('span', 'search-status status-' + item.status, item.pending && !['registered', 'completed'].includes(item.status)
      ? 'Chegou; aguarda orientação' : statusText[item.status] || item.status));
  if (item.pending) facts.append(el('span', 'pill pending-pill', 'Pendência: orientar a procurar a coordenação'));
  const action = el('div', 'search-result-action');
  if (item.status === 'registered') {
    action.append(actionButton(item.pending ? 'Registrar chegada' : 'Solicitar separação',
      'button small' + (item.pending ? ' warning' : ''), () => sendToSeparation(item, refresh)));
  } else if (canMoveHere(item)) {
    action.append(actionButton('Trazer para este guichê', 'button small', () => sendToSeparation(item, refresh)));
  }
  row.append(main, facts, action);
  row.addEventListener('click', event => {
    if (event.target.closest('button')) return;
    activeResult = index;
    renderSearch(refresh);
  });
  return row;
}

function renderSearch(refresh) {
  const query = $('queue-filter').value.trim();
  const panel = $('search-panel'), list = $('search-results');
  panel.classList.toggle('hidden', !query);
  $('queue-filter').setAttribute('aria-expanded', String(Boolean(query)));
  list.replaceChildren(...searchResults.map((item, index) => resultRow(item, index, refresh)));
  const active = searchResults[activeResult];
  if (active) {
    $('queue-filter').setAttribute('aria-activedescendant', 'result-' + active.id);
    $('result-' + active.id).scrollIntoView({block: 'nearest'});
  } else {
    $('queue-filter').removeAttribute('aria-activedescendant');
  }
}

async function runSearch(refresh) {
  const query = $('queue-filter').value.trim();
  const seq = ++searchSeq;
  if (!query) {
    searchResults = [];
    activeResult = -1;
    renderSearch(refresh);
    renderQueue(refresh);
    return;
  }
  try {
    const data = await api('/api/participants/search?q=' + encodeURIComponent(query));
    if (seq !== searchSeq) return;
    const activeId = searchResults[activeResult]?.id;
    searchResults = data.items;
    activeResult = Math.max(searchResults.findIndex(item => item.id === activeId), searchResults.length ? 0 : -1);
    // O aviso da última solicitação fica visível até a próxima busca digitada.
    if (!searchNotice) $('search-summary').textContent = !data.total ? 'Nenhum participante encontrado.'
      : data.total > searchResults.length ? `${data.total} encontrados; mostrando os ${searchResults.length} primeiros. Refine a busca.`
      : `${data.total} ${data.total === 1 ? 'participante encontrado' : 'participantes encontrados'}.`;
    renderSearch(refresh);
    renderQueue(refresh);
  } catch (err) {
    if (seq === searchSeq) message('page-error', err.message);
  }
}

function initSearch(refresh) {
  const input = $('queue-filter');
  input.addEventListener('input', () => {
    searchNotice = null;
    renderQueue(refresh);
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => runSearch(refresh), 250);
  });
  input.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      input.value = '';
      searchNotice = null;
      runSearch(refresh);
      return;
    }
    if (!searchResults.length) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      activeResult = (activeResult + step + searchResults.length) % searchResults.length;
      renderSearch(refresh);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const item = searchResults[activeResult];
      if (!item) return;
      if (item.status === 'registered' || canMoveHere(item)) sendToSeparation(item, refresh);
      else showNotice(`${item.name}: ${item.status === 'completed' ? 'já credenciado.'
        : item.status === 'ready' ? `material já pronto no guichê ${item.guiche}. Encaminhe a pessoa para lá.`
        : item.status === 'searching' ? `material já está sendo separado para o guichê ${item.guiche}. Encaminhe a pessoa para lá.`
        : 'já está na separação deste guichê.'}`);
    }
  });
}

async function showDeskHeading() {
  const params = new URLSearchParams(location.search);
  const requested = params.get('guiche') || '';
  desk = requested === 'all' ? '' : requested || (session.user.role === 'attendant' ? session.user.guiche : '');
  withoutPriority = !desk && params.get('exclude') === 'priority';
  $('guiche-title').textContent = desk ? 'GUICHÊ ' + desk : withoutPriority ? 'GUICHÊS GERAIS' : 'TODOS OS GUICHÊS';
  $('guiche-title').closest('.attendant-heading').classList.toggle('all-desks', !desk);
  if (!desk) {
    $('guiche-range').textContent = withoutPriority ? 'Todos os guichês, exceto o de prioridade'
      : 'Visualização de todos os guichês';
    return;
  }
  try {
    const info = (await api('/api/guiches')).guiches.find(candidate => candidate.id === desk);
    const ranges = info?.ranges || [];
    $('guiche-range').textContent = info?.priority ? 'Atendimento prioritário'
      : ranges.length ? 'Faixa de letras: ' + ranges.map(range => range.replace('–', ' a ')).join(' · ') : '';
  } catch {
    $('guiche-range').textContent = 'Faixa de letras indisponível';
  }
}

async function refresh() {
  try {
    const query = mode === 'attendant' ? '?view=attendant&guiche=' + encodeURIComponent(desk || 'all') +
      (withoutPriority ? '&exclude=priority' : '')
      : desk ? '?guiche=' + encodeURIComponent(desk) : '';
    const data = await api('/api/queue' + query);
    items = data.items;
    priorityDesk = data.priority_guiche || '';
    renderQueue(refresh);
    if (mode === 'attendant' && $('queue-filter').value.trim()) await runSearch(refresh);
    $('last-update').textContent = 'Atualizado ' + clockTime();
    message('page-error', '');
  } catch (err) {
    message('page-error', err.message);
  }
}

await loadEventConfig();
if (await requireSession(['volunteer', 'attendant', 'admin'])) {
  if (mode === 'attendant') await showDeskHeading();
  if (mode === 'attendant') initSearch(refresh);
  else $('queue-filter').addEventListener('input', () => renderQueue(refresh));
  initDialog(refresh);
  await refresh();
  setInterval(refresh, 5000);
}
