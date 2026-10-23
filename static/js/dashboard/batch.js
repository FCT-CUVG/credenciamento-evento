// Alteração em lote: seleção de participantes na tabela e a mesma mudança para todos.
import {$, api, el, message, statusNames} from '../common.js';
import {filteredItems, pageItems, paymentValues, state, syncPageCheckbox} from './table.js';

const fieldNames = {
  payment: 'o pagamento', status: 'a situação do credenciamento', guiche: 'o guichê', priority: 'a prioridade'
};

// Valores possíveis para cada campo: [valor enviado, texto mostrado].
function valueOptions(field) {
  if (field === 'payment') return paymentValues().map(value => [value, value]);
  if (field === 'status') return Object.entries(statusNames);
  if (field === 'guiche') return (state.desks || []).map(desk => [desk.id, 'Guichê ' + desk.id]);
  if (field === 'priority') return [['1', 'Sim'], ['0', 'Não']];
  return [];
}

function fillValues() {
  const field = $('batch-field').value, select = $('batch-value'), current = select.value;
  const entries = valueOptions(field);
  select.replaceChildren(...[['', field ? 'Escolha…' : '—'], ...entries].map(([value, label]) => {
    const option = el('option', '', label);
    option.value = value;
    return option;
  }));
  select.value = entries.some(([value]) => value === current) ? current : '';
  select.disabled = !field;
}

export function renderBatch() {
  const count = state.selected.size, filtered = filteredItems();
  const missing = filtered.filter(item => !state.selected.has(item.id)).length;
  $('batch-count').textContent = count
    ? `${count} ${count === 1 ? 'participante selecionado' : 'participantes selecionados'}`
    : 'Nenhum participante selecionado';
  $('batch-bar').classList.toggle('has-selection', count > 0);
  const selectAll = $('batch-select-filtered');
  selectAll.hidden = !missing;
  selectAll.textContent = `Selecionar todos os ${filtered.length} filtrados`;
  $('batch-clear').hidden = !count;
  // A lista de valores acompanha as situações de pagamento e os guichês atuais.
  fillValues();
  $('batch-apply').disabled = !count || !$('batch-field').value || !$('batch-value').value;
}

function setSelected(ids, selected) {
  for (const id of ids) {
    if (selected) state.selected.add(id);
    else state.selected.delete(id);
  }
  for (const box of document.querySelectorAll('#dashboard-body .row-select')) {
    box.checked = state.selected.has(box.dataset.participantId);
    box.closest('tr').classList.toggle('selected', box.checked);
  }
  syncPageCheckbox();
  renderBatch();
}

function showResult(result, label) {
  const box = $('batch-result');
  const parts = [`${result.updated} ${result.updated === 1 ? 'participante alterado' : 'participantes alterados'}`];
  if (result.unchanged) parts.push(`${result.unchanged} já ${result.unchanged === 1 ? 'estava' : 'estavam'} assim`);
  if (result.skipped.length) parts.push(`${result.skipped.length} não ${result.skipped.length === 1 ? 'pôde' : 'puderam'} mudar`);
  box.replaceChildren(el('p', '', `${label}: ${parts.join(' · ')}.`));
  box.classList.toggle('error', result.skipped.length > 0);
  if (result.skipped.length) {
    // Quem ficou de fora continua selecionado, com o motivo, para tratar à parte.
    const details = el('details'), list = el('ul');
    details.append(el('summary', '', 'Ver quem não mudou e por quê'), list);
    for (const item of result.skipped) list.append(el('li', '', `${item.name || item.id}: ${item.reason}`));
    box.append(details);
  }
}

async function apply(refresh) {
  const field = $('batch-field').value, value = $('batch-value').value, ids = [...state.selected];
  const label = $('batch-value').selectedOptions[0]?.textContent || value;
  if (!ids.length || !field || !value) return;
  if (!confirm(`Mudar ${fieldNames[field]} de ${ids.length} ${ids.length === 1 ? 'participante' : 'participantes'} ` +
    `para “${label}”?`)) return;
  const button = $('batch-apply');
  button.disabled = true;
  try {
    const result = await api('/api/participants/batch', {ids, field, value});
    const skipped = new Set(result.skipped.map(item => item.id));
    setSelected(ids.filter(id => !skipped.has(id)), false);
    showResult(result, `${fieldNames[field][0].toUpperCase()}${fieldNames[field].slice(1)} → “${label}”`);
    await refresh();
  } catch (err) {
    message('page-error', err.message);
  } finally {
    renderBatch();
  }
}

export function initBatch(refresh) {
  $('dashboard-body').addEventListener('change', event => {
    const box = event.target;
    if (box.matches('.row-select')) setSelected([box.dataset.participantId], box.checked);
  });
  $('select-page').addEventListener('change', event =>
    setSelected(pageItems().map(item => item.id), event.target.checked));
  $('batch-select-filtered').addEventListener('click', () => setSelected(filteredItems().map(item => item.id), true));
  $('batch-clear').addEventListener('click', () => setSelected([...state.selected], false));
  $('batch-field').addEventListener('change', renderBatch);
  $('batch-value').addEventListener('change', renderBatch);
  $('batch-apply').addEventListener('click', () => apply(refresh));
  renderBatch();
}
