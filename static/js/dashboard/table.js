// Tabela do painel detalhado: linhas, etapas da situação, afiliação editável, filtros e cards por guichê.
import {$, el, movementTime, searchKey, statusNames} from '../common.js';

export const state = {items: [], page: 1, affiliationEditing: null, lookup: null};

const hasPending = item =>
  !['registered', 'completed'].includes(item.status) && (!item.paid || !item.affiliation.trim());
export const statusKey = item => item.status === 'prechecked' && hasPending(item) ? 'guidance' : item.status;
const statusLabel = item => statusKey(item) === 'guidance' ? 'Orientação pendente' : statusNames[item.status];
const statusSteps = [['registered', 'IN'], ['prechecked', 'AB'], ['searching', 'EB'], ['ready', 'PR'], ['completed', 'CR']];
const stepTimestamp = {
  registered: 'updated_at', prechecked: 'prechecked_at', searching: 'claimed_at', ready: 'ready_at', completed: 'completed_at'
};
const statusTime = item => item[stepTimestamp[item.status]] || item.updated_at;
// CPF e e-mail ficam guardados só como chave de busca; aparece apenas o início do CPF.
const cpfText = item => item.has_cpf ? `${item.cpf_prefix}.***.***-**` : 'Sem CPF';

// Linha de etapas clicáveis (IN → AB/OP → EB → PR → CR).
function statusStepper(item) {
  const wrap = el('div', 'stepper'), steps = el('ol', 'stepper-steps'), key = statusKey(item);
  const current = statusSteps.findIndex(([status]) => status === item.status), blocked = hasPending(item);
  wrap.setAttribute('role', 'group');
  wrap.setAttribute('aria-label', 'Situação de ' + item.name);
  statusSteps.forEach(([status, short], index) => {
    // Na segunda posição, OP (orientação pendente) substitui AB para quem chegou com pendência.
    const text = index === current && key === 'guidance' ? 'OP' : short;
    const li = el('li', index < current ? 'done' : index === current ? 'current' : '');
    const step = el('button', 'step' + (index === current ? ' status-' + key : ''), text);
    const label = statusNames[status];
    step.type = 'button';
    step.dataset.participantId = item.id;
    step.dataset.status = status;
    step.disabled = blocked && ['searching', 'ready', 'completed'].includes(status);
    step.title = step.disabled ? `${label} — resolva as pendências antes`
      : index === current ? `${statusLabel(item)} (atual)` : `Mudar para ${label}`;
    step.setAttribute('aria-label', index === current ? `${statusLabel(item)}, situação atual` : `Mudar para ${label}`);
    if (index === current) step.setAttribute('aria-current', 'step');
    li.append(step);
    steps.append(li);
  });
  wrap.append(steps, el('span', 'stepper-label status-' + key, statusLabel(item)));
  return wrap;
}

// Afiliação: texto fixo; quando falta, fica destacada e pode ser preenchida na própria linha.
function affiliationCell(item) {
  const td = el('td', 'affiliation-cell');
  if (state.affiliationEditing?.id === item.id) {
    const form = el('form', 'affiliation-form'), input = el('input', 'affiliation-input');
    input.name = 'affiliation';
    input.maxLength = 200;
    input.value = state.affiliationEditing.value;
    input.placeholder = 'Afiliação';
    input.dataset.participantId = item.id;
    input.setAttribute('aria-label', 'Afiliação de ' + item.name);
    const save = el('button', 'button small', 'Salvar');
    const cancel = el('button', 'button secondary small affiliation-cancel', 'Cancelar');
    save.type = 'submit';
    cancel.type = 'button';
    form.dataset.participantId = item.id;
    form.append(input, save, cancel);
    td.append(form);
    return td;
  }
  const filled = item.affiliation.trim();
  td.append(filled ? el('span', '', item.affiliation) : el('span', 'pill warning', 'Não informada'));
  const edit = el('button', 'affiliation-edit', filled ? 'Editar' : 'Preencher');
  edit.type = 'button';
  edit.dataset.participantId = item.id;
  edit.setAttribute('aria-label', `${edit.textContent} afiliação de ${item.name}`);
  td.append(edit);
  return td;
}

function personCell(item) {
  const td = el('td', 'person-cell'), details = el('dl', 'person-details');
  for (const [label, value] of [['Crachá', item.badge_name], ['CPF', cpfText(item)]]) {
    details.append(el('dt', '', label), el('dd', '', value));
  }
  td.append(el('div', 'participant-name', item.name), details);
  return td;
}

function paymentCell(item) {
  const td = el('td'), select = el('select', 'payment-select ' + (item.paid ? 'is-paid' : 'is-unpaid'));
  select.setAttribute('aria-label', 'Alterar pagamento de ' + item.name);
  select.dataset.participantId = item.id;
  for (const [value, label] of [['0', 'Não pago'], ['1', 'Pago']]) {
    const option = el('option', '', label);
    option.value = value;
    option.selected = (value === '1') === item.paid;
    select.append(option);
  }
  td.append(select);
  return td;
}

function priorityCell(item) {
  const td = el('td'), toggle = el('button', 'switch priority-switch');
  toggle.type = 'button';
  toggle.setAttribute('role', 'switch');
  toggle.setAttribute('aria-checked', String(item.priority));
  toggle.setAttribute('aria-label', 'Prioridade de ' + item.name);
  toggle.dataset.participantId = item.id;
  toggle.append(el('span', 'switch-track'), el('span', '', item.priority ? 'Sim' : 'Não'));
  td.append(toggle);
  return td;
}

function movementCell(item) {
  const td = el('td'), {label, title} = movementTime(statusTime(item)), time = el('time', '', label);
  if (title) {
    time.dateTime = new Date(statusTime(item)).toISOString();
    time.title = title;
  }
  td.append(time);
  return td;
}

function filteredItems() {
  const raw = $('dashboard-filter').value, text = searchKey(raw), status = $('status-filter').value;
  // CPF ou e-mail completo: vale a resposta do servidor (state.lookup), não o texto das linhas.
  const lookup = state.lookup?.text === raw ? state.lookup.ids : null;
  const priority = $('priority-filter').value, paid = $('payment-filter').value, desk = $('desk-filter').value;
  return state.items.filter(item =>
    (!status || statusKey(item) === status) &&
    (!priority || item.priority === (priority === '1')) &&
    (!paid || item.paid === (paid === '1')) &&
    (!desk || item.guiche === desk) &&
    (lookup ? lookup.has(item.id) : searchKey(`${item.name} ${item.badge_name} ${item.affiliation} ${item.guiche}`).includes(text)));
}

export function renderDashboard() {
  const body = $('dashboard-body');
  body.replaceChildren();
  const matches = filteredItems();
  $('list-count').textContent = `${matches.length} ${matches.length === 1 ? 'pessoa' : 'pessoas'}`;
  const size = Number($('page-size').value), pages = Math.max(1, Math.ceil(matches.length / size));
  state.page = Math.min(Math.max(1, state.page), pages);
  const first = (state.page - 1) * size, rows = matches.slice(first, first + size);
  $('page-range').textContent = matches.length ? `Mostrando ${first + 1}–${first + rows.length} de ${matches.length}` : '';
  $('page-label').textContent = `Página ${state.page} de ${pages}`;
  $('page-prev').disabled = state.page === 1;
  $('page-next').disabled = state.page === pages;
  if (!rows.length) {
    const row = el('tr'), cell = el('td', 'muted', 'Nenhum participante encontrado.');
    cell.colSpan = 8;
    row.append(cell);
    body.append(row);
    return;
  }
  for (const item of rows) {
    const tr = el('tr'), situation = el('td');
    situation.append(statusStepper(item));
    tr.append(personCell(item), el('td', '', item.badge_name), affiliationCell(item), paymentCell(item),
      priorityCell(item), el('td', '', item.guiche), situation, movementCell(item));
    body.append(tr);
  }
}

// Redesenha a tabela devolvendo o foco ao mesmo controle; só espera se um seletor estiver em uso.
export function renderDashboardKeepingFocus() {
  const active = document.activeElement, body = $('dashboard-body');
  if (!body.contains(active)) {
    renderDashboard();
    return;
  }
  // Não interrompe quem está escolhendo num seletor ou digitando a afiliação.
  if (active instanceof HTMLSelectElement || active instanceof HTMLInputElement) return;
  const id = active.dataset.participantId, status = active.dataset.status, kind = active.classList[0];
  renderDashboard();
  const selector = `.${kind}[data-participant-id="${id}"]` + (status ? `[data-status="${status}"]` : '');
  body.querySelector(selector)?.focus({preventScroll: true});
}

export function fillDeskFilter(desks) {
  const select = $('desk-filter'), current = select.value;
  const all = el('option', '', 'Todos');
  all.value = '';
  select.replaceChildren(all);
  for (const desk of desks) {
    const option = el('option', '', 'Guichê ' + desk.id);
    option.value = desk.id;
    select.append(option);
  }
  select.value = desks.some(desk => desk.id === current) ? current : '';
}

export function renderDeskStats(desks, pending) {
  const list = $('desk-stats-list');
  list.replaceChildren();
  for (const desk of desks) {
    const item = el('li', 'desk-stat' + (desk.priority ? ' priority' : ''));
    const detail = desk.priority ? 'Prioridade' : desk.ranges.join(', ');
    item.append(el('strong', 'desk-stat-total', desk.total), el('span', 'desk-stat-name', 'Guichê ' + desk.id));
    if (detail && !(desk.priority && /priorid/i.test(desk.id))) item.append(el('span', 'desk-stat-range', detail));
    list.append(item);
  }
  // Pendências não pertencem a um guichê: são atendidas com orientação, então ficam num card à parte.
  const item = el('li', 'desk-stat pending');
  item.append(el('strong', 'desk-stat-total', pending), el('span', 'desk-stat-name', 'Com pendência'),
    el('span', 'desk-stat-range', 'Pagamento ou afiliação'));
  list.append(item);
}
