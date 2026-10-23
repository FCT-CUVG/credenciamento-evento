// Ordem dos blocos do painel detalhado: arrastar pela alça ou usar ↑ e ↓, e salvar no servidor.
import {$, api, clockTime, el} from '../common.js';

// defaults: ordem do HTML, guardada antes de qualquer mudança, para o botão "Ordem padrão".
let saved = [], defaults = [], editing = false, dragging = null;

const blocks = () => [...document.querySelectorAll('[data-block]')];
const currentOrder = () => blocks().map(block => block.dataset.block);

function status(text, error = false) {
  $('layout-status').textContent = text;
  $('layout-status').classList.toggle('error', error);
}

function savedText(layout) {
  if (!layout.by) return '';
  return `Ordem salva por ${layout.by}${layout.at ? ' às ' + clockTime(layout.at) : ''}.`;
}

// Coloca os blocos na ordem pedida, logo depois da barra de organização. Blocos que não estão
// na lista (por exemplo, criados depois de salvar) ficam na ordem padrão, no fim.
function apply(order) {
  const byId = new Map(blocks().map(block => [block.dataset.block, block]));
  const ids = [...order.filter(id => byId.has(id)), ...defaults.filter(id => !order.includes(id))];
  $('layout-bar').after(...ids.map(id => byId.get(id)));
  updateButtons();
}

function updateButtons() {
  const list = blocks();
  list.forEach((block, index) => {
    const up = block.querySelector(':scope > .block-handle .block-up');
    const down = block.querySelector(':scope > .block-handle .block-down');
    if (up) up.disabled = index === 0;
    if (down) down.disabled = index === list.length - 1;
  });
}

function changed() {
  status('Ordem alterada, ainda não salva.');
}

function move(block, step) {
  const list = blocks(), index = list.indexOf(block), target = list[index + step];
  if (!target) return;
  if (step < 0) target.before(block); else target.after(block);
  updateButtons();
  changed();
  // Mantém o foco no mesmo botão para dar para mover várias vezes seguidas pelo teclado.
  block.querySelector(`:scope > .block-handle .block-${step < 0 ? 'up' : 'down'}`)?.focus();
  block.scrollIntoView({block: 'nearest', behavior: 'smooth'});
}

function handleFor(block) {
  const handle = el('div', 'block-handle');
  const grip = el('span', 'block-grip', '⠿');
  grip.setAttribute('aria-hidden', 'true');
  handle.draggable = true;
  const title = block.dataset.blockTitle;
  const up = el('button', 'button secondary small block-up', '↑'), down = el('button', 'button secondary small block-down', '↓');
  up.type = down.type = 'button';
  up.setAttribute('aria-label', `Subir “${title}”`);
  down.setAttribute('aria-label', `Descer “${title}”`);
  up.addEventListener('click', () => move(block, -1));
  down.addEventListener('click', () => move(block, 1));
  handle.append(grip, el('strong', 'block-title', title), up, down);
  handle.addEventListener('dragstart', event => {
    dragging = block;
    block.classList.add('is-dragging');
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', block.dataset.block);
  });
  handle.addEventListener('dragend', () => {
    block.classList.remove('is-dragging');
    dragging = null;
    updateButtons();
  });
  return handle;
}

// Durante o arraste, o bloco vai para antes ou depois do bloco sob o ponteiro, conforme a metade.
function dragOver(event) {
  if (!dragging) return;
  event.preventDefault();
  const target = event.target.closest?.('[data-block]');
  if (!target || target === dragging || target.contains(dragging)) return;
  const box = target.getBoundingClientRect(), before = event.clientY < box.top + box.height / 2;
  if ((before ? target.previousElementSibling : target.nextElementSibling) === dragging) return;
  if (before) target.before(dragging); else target.after(dragging);
  changed();
}

function setEditing(on) {
  editing = on;
  document.body.classList.toggle('layout-editing', on);
  $('layout-help').hidden = !on;
  $('layout-edit').hidden = on;
  for (const id of ['layout-save', 'layout-cancel', 'layout-reset']) $(id).hidden = !on;
  for (const block of blocks()) {
    block.querySelector(':scope > .block-handle')?.remove();
    if (on) block.prepend(handleFor(block));
  }
  updateButtons();
}

async function save(order) {
  $('layout-save').disabled = true;
  try {
    const layout = await api('/api/dashboard/layout', {order});
    saved = layout.order;
    setEditing(false);
    status(savedText(layout));
  } catch (err) {
    status(err.message, true);
  } finally {
    $('layout-save').disabled = false;
  }
}

export async function initLayout() {
  defaults = currentOrder();
  apply([]);
  document.querySelector('main').addEventListener('dragover', dragOver);
  document.querySelector('main').addEventListener('drop', event => { if (dragging) event.preventDefault(); });
  $('layout-edit').addEventListener('click', () => {
    setEditing(true);
    status('');
  });
  $('layout-cancel').addEventListener('click', () => {
    apply(saved);
    setEditing(false);
    status('Alterações descartadas.');
  });
  $('layout-reset').addEventListener('click', () => {
    apply(defaults);
    changed();
  });
  $('layout-save').addEventListener('click', () => {
    const order = currentOrder();
    // A ordem igual à padrão é salva como lista vazia: blocos novos entram no lugar previsto.
    save(order.join() === defaults.join() ? [] : order);
  });
  try {
    const layout = await api('/api/dashboard/layout');
    saved = layout.order;
    if (!editing) apply(saved);
    status(savedText(layout));
  } catch (err) {
    status(err.message, true);
  }
}
