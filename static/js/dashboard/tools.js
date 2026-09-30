// Ferramentas do painel detalhado: abas, importação, Apps Script do Google Sheets e guichês.
import {$, api, el, renderTeamNav} from '../common.js';

function toolStatus(id, value, error = false) {
  const node = $(id);
  node.textContent = value;
  node.classList.toggle('error', error);
}

async function copyText(value) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(value);
  const field = document.createElement('textarea');
  field.value = value;
  field.setAttribute('readonly', '');
  field.style.position = 'fixed';
  field.style.opacity = '0';
  document.body.append(field);
  field.select();
  const copied = document.execCommand('copy');
  field.remove();
  if (!copied) throw new Error('Não foi possível copiar o Apps Script.');
}

function renderRangeRows(ranges) {
  const host = $('range-rows');
  host.replaceChildren();
  ranges.forEach((range, index) => {
    const row = el('div', 'range-row');
    for (const [key, label] of [['from', 'De'], ['to', 'Até'], ['guiche', 'Guichê']]) {
      const field = el('label', 'field', label), input = el('input');
      input.name = key;
      input.value = range[key] || '';
      input.maxLength = key === 'guiche' ? 24 : 1;
      input.setAttribute('aria-label', `${label}, faixa ${index + 1}`);
      field.append(input);
      row.append(field);
    }
    const remove = el('button', 'button secondary small', 'Remover');
    remove.type = 'button';
    remove.setAttribute('aria-label', `Remover faixa ${index + 1}`);
    remove.addEventListener('click', () => {
      row.remove();
      toolStatus('ranges-status', '');
    });
    row.append(remove);
    host.append(row);
  });
}

const currentRanges = () => [...$('range-rows').children].map(row =>
  Object.fromEntries([...row.querySelectorAll('input')].map(input => [input.name, input.value])));

// Abas "Participantes" e "Configuração dos guichês" (setas do teclado e atalhos do topo).
export function initTabs() {
  const tabs = [...document.querySelectorAll('.dashboard-tabs [role="tab"]')];
  const select = (tab, focus = false) => {
    for (const other of tabs) {
      const selected = other === tab;
      other.setAttribute('aria-selected', String(selected));
      other.tabIndex = selected ? 0 : -1;
      $(other.getAttribute('aria-controls')).hidden = !selected;
    }
    if (focus) tab.focus();
  };
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => select(tab));
    tab.addEventListener('keydown', event => {
      const step = {ArrowRight: 1, ArrowLeft: -1}[event.key];
      if (!step) return;
      event.preventDefault();
      select(tabs[(index + step + tabs.length) % tabs.length], true);
    });
  });
  // Os atalhos do topo abrem a aba correspondente.
  for (const link of document.querySelectorAll('.dashboard-shortcuts a[href^="#"]')) {
    const tab = tabs.find(candidate => '#' + candidate.id === link.getAttribute('href'));
    if (tab) link.addEventListener('click', () => select(tab));
  }
  const initial = tabs.find(tab => '#' + tab.id === location.hash);
  if (initial) select(initial);
}

export async function initTools(refresh) {
  $('participants-import-form').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget, file = $('participants-file').files[0], button = form.querySelector('button');
    if (!file) return;
    if (file.size > 1_500_000) {
      toolStatus('participants-import-status', 'Arquivo muito grande. O limite é 1,5 MB.', true);
      return;
    }
    button.disabled = true;
    toolStatus('participants-import-status', 'Importando...');
    try {
      const result = await api('/api/participants/import', {filename: file.name, content: await file.text()});
      toolStatus('participants-import-status',
        `${result.read} ${result.read === 1 ? 'registro lido' : 'registros lidos'}; ` +
        `${result.changed} ${result.changed === 1 ? 'criado ou atualizado' : 'criados ou atualizados'}.`);
      form.reset();
      await refresh();
    } catch (err) {
      toolStatus('participants-import-status', err.message, true);
    } finally {
      button.disabled = false;
    }
  });

  $('copy-google-sheets-script').addEventListener('click', async event => {
    const button = event.currentTarget;
    button.disabled = true;
    toolStatus('google-sheets-status', 'Copiando Apps Script...');
    try {
      await copyText((await api('/api/google-sheets/script')).script);
      toolStatus('google-sheets-status', 'Apps Script copiado. Cole-o no editor da sua planilha.');
    } catch (err) {
      toolStatus('google-sheets-status', err.message, true);
    } finally {
      button.disabled = false;
    }
  });

  $('add-range').addEventListener('click', () => {
    const ranges = currentRanges();
    if (ranges.length >= 26) {
      toolStatus('ranges-status', 'O limite é 26 faixas.', true);
      return;
    }
    renderRangeRows([...ranges, {from: '', to: '', guiche: ''}]);
  });

  $('save-ranges').addEventListener('click', async event => {
    const button = event.currentTarget;
    button.disabled = true;
    toolStatus('ranges-status', 'Salvando...');
    try {
      const result = await api('/api/guiches/config', {ranges: currentRanges(), priority_guiche: $('priority-guiche').value});
      renderRangeRows(result.ranges);
      $('priority-guiche').value = result.priority_guiche;
      const moved = result.updated
        ? `${result.updated} ${result.updated === 1 ? 'participante mudou' : 'participantes mudaram'} de guichê.`
        : 'Nenhum participante precisou mudar de guichê.';
      toolStatus('ranges-status', 'Guichês salvos. ' + moved);
      await Promise.all([renderTeamNav(), refresh()]);
    } catch (err) {
      toolStatus('ranges-status', err.message, true);
    } finally {
      button.disabled = false;
    }
  });

  try {
    const config = await api('/api/guiches/config');
    renderRangeRows(config.ranges);
    $('priority-guiche').value = config.priority_guiche;
  } catch (err) {
    toolStatus('ranges-status', err.message, true);
  }
}
