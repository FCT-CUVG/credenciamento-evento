// Painel detalhado da coordenação: números, tabela de participantes e ferramentas.
import {$, api, clockTime, loadEventConfig, message, requireSession, statusNames} from './common.js';
import {fillDeskFilter, renderDashboard, renderDashboardKeepingFocus, renderDeskStats, state} from './dashboard/table.js';
import {initTabs, initTools} from './dashboard/tools.js';

const FILTERS = ['status-filter', 'priority-filter', 'payment-filter', 'desk-filter'];

function syncText(data) {
  if (!data.sheet_configured) return 'Google Sheets ainda não configurado; as alterações estão guardadas localmente.';
  if (data.sheet_pending) return `${data.sheet_pending} alterações aguardando sincronização com Google Sheets.`;
  return 'Cópia local atualizada; sem alterações pendentes para Google Sheets.';
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

function initFilters() {
  $('dashboard-filter').addEventListener('input', firstPage);
  for (const id of [...FILTERS, 'page-size']) $(id).addEventListener('change', firstPage);
  $('clear-filters').addEventListener('click', () => {
    $('dashboard-filter').value = '';
    for (const id of FILTERS) $(id).value = '';
    firstPage();
  });
  // O atalho de pendências filtra a própria tabela.
  $('pending-shortcut').addEventListener('click', event => {
    event.preventDefault();
    $('dashboard-filter').value = '';
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
  await Promise.all([refresh(), initTools(refresh)]);
  setInterval(refresh, 10000);
}
