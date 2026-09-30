// Filas da equipe: Separação (/busca, voluntários) e guichês (/fila, atendentes).
import {$, api, clockTime, el, loadEventConfig, message, requireSession, session} from './common.js';

const mode = document.body.dataset.page === 'attendant' ? 'attendant' : 'volunteer';
let items = [], desk = '', pendingAction = null;

const dialogTitles = {
  ready: 'Material no guichê?',
  complete: 'Confirmar retirada?',
  undo_ready: 'Voltar para a busca?',
  undo_complete: 'Desfazer retirada?'
};

// Ações com efeito no atendimento pedem confirmação num diálogo.
function ask(action, item) {
  const descriptions = {
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
    : action === 'complete' ? 'Confirmar retirada' : 'Confirmar';
  $('confirm-dialog').showModal();
}

function initDialog(refresh) {
  $('dialog-cancel').addEventListener('click', () => $('confirm-dialog').close());
  $('dialog-confirm').addEventListener('click', async () => {
    if (!pendingAction) return;
    const button = $('dialog-confirm');
    button.disabled = true;
    try {
      await api('/api/action/' + pendingAction.action, {id: pendingAction.item.id});
      $('confirm-dialog').close();
      await refresh();
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
  const {role, username, guiche: ownDesk} = session.user;
  const volunteer = mode === 'volunteer';
  const attendantReady = mode === 'attendant' && item.status === 'ready';
  const split = volunteer || attendantReady;
  const card = el('article', 'queue-card' + (volunteer ? ' volunteer-card' : attendantReady ? ' attendant-card'
    : item.status === 'completed' ? ' completed-card' : ''));
  const content = split ? el('div', 'queue-card-main') : card;
  if (split) card.append(content);

  const name = el('div', 'name', item.name);
  if (item.priority) name.append(el('span', 'pill priority', 'Prioridade'));
  content.append(name);

  // Na tela de um guichê específico, o número do guichê é redundante: mostra o início do CPF.
  const showCpf = mode === 'attendant' && desk;
  const meta = el('div', 'meta');
  // Depois da retirada, o CPF não é mais necessário; em "Todos os guichês" o guichê continua visível.
  if (!split && !showCpf) meta.append(el('span', 'guiche', 'Guichê ' + item.guiche));
  if (item.claimed_by && item.status !== 'completed') meta.append(el('span', 'muted', item.claimed_by));
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

  const staff = ['volunteer', 'admin'].includes(role);
  const actions = el('div', 'queue-actions');
  if (volunteer && staff && item.status === 'prechecked') {
    actions.append(actionButton('Assumir busca', 'button', button => runAction('claim', item, button, refresh)));
  }
  if (volunteer && staff && item.status === 'searching' && (item.claimed_by === username || role === 'admin')) {
    actions.append(
      actionButton('Liberar busca', 'button secondary small', button => runAction('release', item, button, refresh)),
      actionButton('Pronto no guichê', 'button', () => ask('ready', item)));
  }
  const canManageDesk = staff || (role === 'attendant' && ownDesk === item.guiche);
  if (item.status === 'ready' && canManageDesk) {
    actions.append(actionButton('Voltar para busca', 'button secondary small', () => ask('undo_ready', item)));
    if (mode === 'attendant') actions.append(actionButton('Confirmar retirada', 'button', () => ask('complete', item)));
  }
  if (actions.childElementCount) content.append(actions);
  if (mode === 'attendant' && item.status === 'completed' && canManageDesk) {
    content.append(actionButton('Voltar para prontos', 'button secondary small', () => ask('undo_complete', item)));
  }
  return card;
}

const emptyText = {ready: 'Ninguém aguardando retirada.', completed: 'Nenhuma retirada confirmada.'};

function renderQueue(refresh) {
  const filter = ($('queue-filter').value || '').toLocaleLowerCase();
  const statuses = mode === 'attendant' ? ['ready', 'completed'] : ['prechecked', 'searching', 'ready'];
  for (const status of statuses) {
    const list = $('lane-' + status);
    list.replaceChildren();
    const rows = items.filter(item => item.status === status &&
      `${item.name} ${item.guiche}`.toLocaleLowerCase().includes(filter));
    $('count-' + status).textContent = `(${rows.length})`;
    if (!rows.length) list.append(el('div', 'empty', emptyText[status] || 'Nenhum participante nesta etapa.'));
    else rows.forEach(item => list.append(queueCard(item, refresh)));
  }
}

async function showDeskHeading() {
  const requested = new URLSearchParams(location.search).get('guiche') || '';
  desk = requested === 'all' ? '' : requested || (session.user.role === 'attendant' ? session.user.guiche : '');
  $('guiche-title').textContent = desk ? 'GUICHÊ ' + desk : 'TODOS OS GUICHÊS';
  $('guiche-title').closest('.attendant-heading').classList.toggle('all-desks', !desk);
  if (!desk) {
    $('guiche-range').textContent = 'Visualização de todos os guichês';
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
    const query = mode === 'attendant' ? '?view=attendant&guiche=' + encodeURIComponent(desk || 'all')
      : desk ? '?guiche=' + encodeURIComponent(desk) : '';
    items = (await api('/api/queue' + query)).items;
    renderQueue(refresh);
    $('last-update').textContent = 'Atualizado ' + clockTime();
    message('page-error', '');
  } catch (err) {
    message('page-error', err.message);
  }
}

await loadEventConfig();
if (await requireSession(['volunteer', 'attendant', 'admin'])) {
  if (mode === 'attendant') await showDeskHeading();
  $('queue-filter').addEventListener('input', () => renderQueue(refresh));
  initDialog(refresh);
  await refresh();
  setInterval(refresh, 5000);
}
