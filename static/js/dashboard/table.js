// Tabela do painel detalhado: linhas, etapas da situação, afiliação editável, filtros e cards por guichê.
import {$, el, movementTime, searchKey, statusNames} from '../common.js';

export const state = {items: [], page: 1, affiliationEditing: null, lookup: null, paymentOptions: [], selected: new Set()};

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

// Situações de pagamento disponíveis; verde quando a situação libera o credenciamento.
export const paymentValues = () => state.paymentOptions.map(option => option.value);

function paymentCell(item) {
  const td = el('td'), select = el('select', 'payment-select ' + (item.paid ? 'is-paid' : 'is-unpaid'));
  select.setAttribute('aria-label', 'Alterar pagamento de ' + item.name);
  select.dataset.participantId = item.id;
  const values = paymentValues();
  if (!values.includes(item.payment_status)) values.push(item.payment_status);
  for (const value of values) {
    const option = el('option', '', value);
    option.value = value;
    option.selected = value === item.payment_status;
    select.append(option);
  }
  td.append(select);
  return td;
}

function selectCell(item) {
  const td = el('td', 'select-cell'), box = el('input', 'row-select');
  box.type = 'checkbox';
  box.checked = state.selected.has(item.id);
  box.dataset.participantId = item.id;
  box.setAttribute('aria-label', 'Selecionar ' + item.name);
  td.append(box);
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

// Filtro de pagamento: "ok" (libera), "pending" (não libera) ou "s:" + situação.
function paymentMatches(item, filter) {
  if (!filter) return true;
  if (filter === 'ok') return item.paid;
  if (filter === 'pending') return !item.paid;
  return searchKey(item.payment_status) === searchKey(filter.slice(2));
}

export function filteredItems() {
  const raw = $('dashboard-filter').value, text = searchKey(raw), status = $('status-filter').value;
  // CPF ou e-mail completo: vale a resposta do servidor (state.lookup), não o texto das linhas.
  const lookup = state.lookup?.text === raw ? state.lookup.ids : null;
  const priority = $('priority-filter').value, paid = $('payment-filter').value, desk = $('desk-filter').value;
  const country = $('country-filter').value;
  return state.items.filter(item =>
    (!status || statusKey(item) === status) &&
    (!priority || item.priority === (priority === '1')) &&
    paymentMatches(item, paid) &&
    (!country || (country === '__none' ? !item.country : item.country === country)) &&
    (!desk || item.guiche === desk) &&
    (lookup ? lookup.has(item.id)
      : searchKey(`${item.name} ${item.badge_name} ${item.affiliation} ${item.country} ${item.guiche}`).includes(text)));
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
    cell.colSpan = 10;
    row.append(cell);
    body.append(row);
    syncPageCheckbox();
    return;
  }
  for (const item of rows) {
    const tr = el('tr'), situation = el('td');
    situation.append(statusStepper(item));
    tr.classList.toggle('selected', state.selected.has(item.id));
    tr.append(selectCell(item), personCell(item), el('td', '', item.badge_name), affiliationCell(item),
      el('td', item.country ? '' : 'muted', item.country || '—'), paymentCell(item),
      priorityCell(item), el('td', '', item.guiche), situation, movementCell(item));
    body.append(tr);
  }
  syncPageCheckbox();
}

// Redesenha a tabela devolvendo o foco ao mesmo controle; só espera se um seletor estiver em uso.
export function renderDashboardKeepingFocus() {
  const active = document.activeElement, body = $('dashboard-body');
  if (!body.contains(active)) {
    renderDashboard();
    return;
  }
  // Não interrompe quem está escolhendo num seletor ou digitando a afiliação.
  if (active instanceof HTMLSelectElement || (active instanceof HTMLInputElement && active.type !== 'checkbox')) return;
  const id = active.dataset.participantId, status = active.dataset.status, kind = active.classList[0];
  renderDashboard();
  const selector = `.${kind}[data-participant-id="${id}"]` + (status ? `[data-status="${status}"]` : '');
  body.querySelector(selector)?.focus({preventScroll: true});
}

// O cabeçalho marca/desmarca a página atual; fica "meio marcado" quando só parte dela está selecionada.
export function pageItems() {
  const matches = filteredItems(), size = Number($('page-size').value), first = (state.page - 1) * size;
  return matches.slice(first, first + size);
}

export function syncPageCheckbox() {
  const rows = pageItems(), chosen = rows.filter(item => state.selected.has(item.id)).length;
  const box = $('select-page');
  box.checked = rows.length > 0 && chosen === rows.length;
  box.indeterminate = chosen > 0 && chosen < rows.length;
}

// Reconstrói um filtro mantendo a escolha atual, se ela ainda existir.
function fillSelect(select, entries) {
  const current = select.value;
  select.replaceChildren(...entries.map(([value, label]) => {
    const option = el('option', '', label);
    option.value = value;
    return option;
  }));
  select.value = entries.some(([value]) => value === current) ? current : '';
}

export function fillPaymentFilter() {
  fillSelect($('payment-filter'), [['', 'Todos'], ['ok', 'Libera o credenciamento'], ['pending', 'Não libera'],
    ...state.paymentOptions.map(option => ['s:' + option.value, option.value])]);
}

export function fillCountryFilter() {
  const countries = [...new Set(state.items.map(item => item.country).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b, 'pt-BR'));
  fillSelect($('country-filter'), [['', 'Todos'], ['__none', 'Sem país'], ...countries.map(country => [country, country])]);
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
