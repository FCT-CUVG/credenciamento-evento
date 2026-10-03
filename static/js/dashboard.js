// Painel detalhado da coordenação: números, tabela de participantes e ferramentas.
import {$, api, clockTime, el, loadEventConfig, message, requireSession, statusNames} from './common.js';
import {fillDeskFilter, renderDashboard, renderDashboardKeepingFocus, renderDeskStats, state} from './dashboard/table.js';
import {initTheme} from './dashboard/theme.js';
import {initTabs, initTools} from './dashboard/tools.js';

const FILTERS = ['status-filter', 'priority-filter', 'payment-filter', 'desk-filter'];

function syncText(data) {
  if (!data.sheet_configured) return 'Google Sheets ainda não configurado; as alterações estão guardadas localmente.';
  if (data.sheet_pending) return `${data.sheet_pending} alterações aguardando sincronização com Google Sheets.`;
  return 'Cópia local atualizada; sem alterações pendentes para Google Sheets.';
}

// Abrir e fechar o pré-check-in pelo celular; fechado, a página pública só mostra um aviso.
let checkin = null;

function renderCheckin(state, misses) {
  checkin = state;
  const box = document.querySelector('.checkin-control'), info = $('checkin-state'), toggle = $('checkin-toggle');
  box.classList.toggle('is-open', state.open);
  const who = state.by ? ` · ${state.open ? 'aberto' : 'fechado'} por ${state.by}${state.at ? ' às ' + clockTime(state.at) : ''}` : '';
  info.replaceChildren(el('strong', '', state.open ? 'Aberto' : 'Fechado'),
    state.open ? ' — os participantes conseguem registrar a chegada' : ' — a página pública só mostra um aviso', who);
  toggle.textContent = state.open ? 'Fechar pré-check-in' : 'Abrir pré-check-in';
  toggle.className = 'button' + (state.open ? ' secondary' : '');
  toggle.disabled = false;
  // Muitas buscas sem resultado em pouco tempo podem ser alguém testando CPFs.
  message('misses-alert', misses.count >= misses.alert
    ? `Atenção: ${misses.count} buscas sem resultado na página pública nos últimos ${misses.minutes} minutos. ` +
      'Pode ser alguém testando CPFs. Se não houver fila no credenciamento, considere fechar o pré-check-in.'
    : '');
}

async function toggleCheckin() {
  const open = !checkin.open;
  if (!confirm(open ? 'Abrir o pré-check-in pelo celular para os participantes?'
    : 'Fechar o pré-check-in? Quem abrir a página pública verá um aviso para procurar a equipe.')) return;
  $('checkin-toggle').disabled = true;
  try {
    await api('/api/public-checkin', {open});
    await refresh();
  } catch (err) {
    message('page-error', err.message);
    $('checkin-toggle').disabled = false;
  }
}

async function refresh() {
  try {
    const data = await api('/api/dashboard');
    state.items = data.items;
    const {counts} = data;
    $('stat-total').textContent = data.total;
    $('stat-prechecked').textContent = counts.prechecked + counts.searching + counts.ready + counts.completed;
    $('stat-searching').textContent = counts.searching;
    $('stat-ready').textContent = counts.ready;
    $('stat-completed').textContent = counts.completed;
    $('pending-shortcut-count').textContent = data.guidance_pending;
    renderDeskStats(data.desks, data.registration_pending);
    renderCheckin(data.public_checkin, data.search_misses);
    fillDeskFilter(data.desks);
    $('sync-status').textContent = `Atualizado às ${clockTime()} · ${syncText(data)}`;
    renderDashboardKeepingFocus();
    message('page-error', '');
  } catch (err) {
    message('page-error', err.message);
  }
}

// Qualquer mudança de busca ou filtro volta para a primeira página.
function firstPage() {
  state.page = 1;
  renderDashboard();
}

// O painel não recebe CPF nem e-mail; um CPF ou e-mail completo digitado na busca é conferido no servidor.
const lookupQuery = text => {
  const value = text.trim();
  return (/^[\d.\-\s]+$/.test(value) && value.replace(/\D/g, '').length === 11) || /^[^@\s]+@[^@\s]+$/.test(value);
};
let lookupTimer = null;

function searchChanged() {
  const text = $('dashboard-filter').value;
  clearTimeout(lookupTimer);
  if (!lookupQuery(text)) {
    state.lookup = null;
    return firstPage();
  }
  lookupTimer = setTimeout(async () => {
    try {
      const {ids} = await api('/api/participants/find', {query: text.trim()});
      if ($('dashboard-filter').value === text) state.lookup = {text, ids: new Set(ids)};
      message('page-error', '');
    } catch (err) {
      state.lookup = {text, ids: new Set()};
      message('page-error', err.message);
    }
    firstPage();
  }, 300);
}

function initFilters() {
  $('dashboard-filter').addEventListener('input', searchChanged);
  for (const id of [...FILTERS, 'page-size']) $(id).addEventListener('change', firstPage);
  $('clear-filters').addEventListener('click', () => {
    $('dashboard-filter').value = '';
    state.lookup = null;
    for (const id of FILTERS) $(id).value = '';
    firstPage();
  });
  // O atalho de pendências filtra a própria tabela.
  $('pending-shortcut').addEventListener('click', event => {
    event.preventDefault();
    $('dashboard-filter').value = '';
    state.lookup = null;
    for (const id of FILTERS) $(id).value = '';
    $('status-filter').value = 'guidance';
    firstPage();
    document.querySelector('.toolbar').scrollIntoView({block: 'start', behavior: 'smooth'});
  });
  const turnPage = step => {
    state.page += step;
    renderDashboard();
    document.querySelector('.table-wrap').scrollIntoView({block: 'start', behavior: 'smooth'});
  };
  $('page-prev').addEventListener('click', () => turnPage(-1));
  $('page-next').addEventListener('click', () => turnPage(1));
}

const findItem = id => state.items.find(candidate => candidate.id === id);

async function changePayment(select) {
  const item = findItem(select.dataset.participantId);
  if (!item) return;
  const paid = select.value === '1';
  // Tira o foco do seletor para a atualização automática voltar a redesenhar a tabela.
  select.blur();
  if (!confirm(`Marcar ${item.name} como ${paid ? 'pago' : 'não pago'}?`)) {
    select.value = item.paid ? '1' : '0';
    return;
  }
  try {
    await api('/api/participants/payment', {id: item.id, paid: paid ? 1 : 0});
    await refresh();
  } catch (err) {
    select.value = item.paid ? '1' : '0';
    message('page-error', err.message);
  }
}

async function saveAffiliation(form) {
  const item = findItem(form.dataset.participantId);
  if (!item) return;
  const button = form.querySelector('button[type="submit"]'), input = form.elements.namedItem('affiliation');
  // Tira o foco do campo (Enter o mantém ali) para a tabela poder ser redesenhada ao salvar.
  button.disabled = true;
  input.blur();
  try {
    await api('/api/participants/affiliation', {id: item.id, affiliation: input.value});
    state.affiliationEditing = null;
    await refresh();
  } catch (err) {
    message('page-error', err.message);
    button.disabled = false;
  }
}

function editAffiliation(id) {
  const item = findItem(id);
  state.affiliationEditing = {id: item.id, value: item.affiliation};
  renderDashboard();
  document.querySelector(`.affiliation-input[data-participant-id="${item.id}"]`)?.focus();
}

function cancelAffiliation() {
  state.affiliationEditing = null;
  renderDashboard();
}

async function changeStatus(step) {
  const item = findItem(step.dataset.participantId), status = step.dataset.status;
  if (!item || status === item.status) return;
  if (!confirm(`Alterar a situação de ${item.name} para “${statusNames[status]}”?`)) return;
  step.disabled = true;
  try {
    await api('/api/participants/status', {id: item.id, status});
    await refresh();
  } catch (err) {
    message('page-error', err.message);
    step.disabled = false;
  }
}

async function togglePriority(toggle) {
  const item = findItem(toggle.dataset.participantId);
  if (!item) return;
  const priority = !item.priority;
  const question = priority
    ? `Marcar ${item.name} como prioridade? A pessoa passa para o guichê de prioridade.`
    : `Remover a prioridade de ${item.name}? Se estiver no guichê de prioridade, volta para o guichê da sua letra.`;
  if (!confirm(question)) return;
  toggle.disabled = true;
  try {
    await api('/api/participants/priority', {id: item.id, priority: priority ? 1 : 0});
    await refresh();
  } catch (err) {
    message('page-error', err.message);
  } finally {
    toggle.disabled = false;
  }
}

// Um ouvinte por tipo de evento na tabela inteira (as linhas são redesenhadas a cada atualização).
function initTableEvents() {
  const body = $('dashboard-body');
  body.addEventListener('change', event => {
    if (event.target.matches('select.payment-select')) changePayment(event.target);
  });
  body.addEventListener('input', event => {
    if (event.target.matches('.affiliation-input') && state.affiliationEditing) {
      state.affiliationEditing.value = event.target.value;
    }
  });
  body.addEventListener('submit', event => {
    event.preventDefault();
    saveAffiliation(event.target);
  });
  body.addEventListener('keydown', event => {
    if (event.key === 'Escape' && event.target.matches('.affiliation-input')) cancelAffiliation();
  });
  body.addEventListener('click', event => {
    const target = event.target;
    const edit = target.closest('.affiliation-edit');
    if (edit) return editAffiliation(edit.dataset.participantId);
    if (target.closest('.affiliation-cancel')) return cancelAffiliation();
    const step = target.closest('.step');
    if (step) return changeStatus(step);
    const toggle = target.closest('.priority-switch');
    if (toggle) return togglePriority(toggle);
  });
}

await loadEventConfig();
if (await requireSession(['admin'])) {
  initTabs();
  initFilters();
  initTableEvents();
  $('checkin-toggle').addEventListener('click', toggleCheckin);
  await Promise.all([refresh(), initTools(refresh), initTheme()]);
  setInterval(refresh, 10000);
}
