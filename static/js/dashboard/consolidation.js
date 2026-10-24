// Aba "Consolidação": lista original de inscrição (ex.: ECOS) x credenciamento, só neste navegador.
// O arquivo fica na memória da aba: não vai ao servidor nem a localStorage; recarregar apaga.
import {$, api, el} from '../common.js';
import {consolidate, decode} from './consolidation-core.js';

const TITLES = {
  cancelado_e_credenciado: 'Cancelados na lista, mas credenciados',
  fora_da_lista: 'No credenciamento, mas fora da lista',
  nao_credenciado: 'Inscrições válidas não credenciadas',
  nao_encontrado: 'Inscrições válidas não encontradas no credenciamento'
};
const HELP = {
  cancelado_e_credenciado: 'A inscrição está cancelada na lista, mas a pessoa retirou o kit.',
  fora_da_lista: 'Estão no credenciamento, mas em nenhuma inscrição válida da lista. Quem mudou de nome ' +
    'num dos sistemas aparece aqui e também em “não encontradas”: confirme a correspondência lá.',
  nao_credenciado: 'A inscrição é válida e a pessoa ainda não retirou o kit. Para resolver, mostre-as na lista ' +
    'de participantes e mude a situação na linha ou em lote.',
  nao_encontrado: 'Nenhuma pessoa do credenciamento com esse nome (e início de CPF). Se uma sugestão for a mesma ' +
    'pessoa, confirme; se a pessoa não existe no credenciamento, inclua-a pela aba Participantes.'
};

const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;

function status(text, error = false) {
  $('consolidation-status').textContent = text;
  $('consolidation-status').classList.toggle('error', error);
}

function download(name, text) {
  // BOM para o Excel abrir acentos certo.
  const url = URL.createObjectURL(new Blob(['﻿' + text], {type: 'text/csv;charset=utf-8'}));
  const link = el('a');
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

function table(headers, rows) {
  const wrap = el('div', 'table-wrap'), node = el('table', 'consolidation-table');
  const head = el('tr');
  for (const header of headers) head.append(el('th', '', header));
  node.createTHead().append(head);
  const body = node.createTBody();
  for (const cells of rows) {
    const row = el('tr');
    for (const cell of cells) {
      const td = el('td');
      td.append(...(Array.isArray(cell) ? cell : [cell ?? '']));
      row.append(td);
    }
    body.append(row);
  }
  wrap.append(node);
  return wrap;
}

// showParticipants(rótulo, ids): filtra a lista de participantes do painel por essas pessoas.
export function initConsolidation(showParticipants) {
  let text = null, baseName = 'lista';
  const manual = new Map(); // linha da planilha -> participante, confirmados aqui

  async function run() {
    if (text === null) return;
    status('Consolidando...');
    try {
      const {people} = await api('/api/consolidation/people');
      render(consolidate(text, people, manual));
      status('');
    } catch (err) {
      $('consolidation-result').hidden = true;
      status(err.message, true);
    }
  }

  function render(result) {
    const {summary: s, issues} = result, host = $('consolidation-result');
    host.replaceChildren();
    const lines = [
      `Lista: ${plural(s.rows, 'linha', 'linhas')}; ${s.cancelled} com status cancelado (fora da lista consolidada); ` +
        `${s.valid} válidas.`,
      `Inscrições válidas: ${plural(s.matched, 'pessoa encontrada', 'pessoas encontradas')}, ` +
        `${plural(s.matchedCompleted, 'credenciada', 'credenciadas')} e ` +
        `${plural(s.issues.nao_credenciado, 'não credenciada', 'não credenciadas')}; ` +
        `${plural(s.issues.nao_encontrado, 'não encontrada', 'não encontradas')} no credenciamento; ` +
        `${plural(s.repeated, 'linha repete', 'linhas repetem')} a pessoa de outra linha` +
        (s.manual ? `; ${plural(s.manual, 'correspondência confirmada', 'correspondências confirmadas')} à mão.` : '.'),
      `Credenciamento (deve bater com o painel): ${s.system.total} inscritos; ${s.system.arrived} chegaram; ` +
        `${s.system.completed} credenciados.`
    ];
    if (s.ignored.length) {
      const shown = s.ignored.slice(0, 5).map(item => `linha ${item.line} (${item.text.slice(0, 60)})`).join('; ');
      lines.push(`${plural(s.ignored.length, 'linha sem nome ignorada', 'linhas sem nome ignoradas')}, ` +
        `fora das contas acima (ex.: total ou rodapé da exportação): ${shown}` +
        `${s.ignored.length > 5 ? ` e mais ${s.ignored.length - 5}` : ''}.`);
    }
    if (!s.hasStatus) lines.push('A lista não tem coluna Status: nenhuma inscrição foi tratada como cancelada.');
    if (!s.hasCpf) lines.push('A lista não tem coluna CPF: homônimos ficam como não encontrados, com sugestões.');
    const summary = el('ul', 'consolidation-summary');
    for (const line of lines) summary.append(el('li', '', line));

    const actions = el('div', 'dashboard-tool-actions');
    const button = (label, onClick, className = 'button secondary small') => {
      const node = el('button', className, label);
      node.type = 'button';
      node.addEventListener('click', onClick);
      return node;
    };
    actions.append(
      button('Baixar lista consolidada', () => download(`${baseName}-consolidada.csv`, result.listCsv), 'button small'),
      button('Baixar pendências', () => download(`${baseName}-pendencias.csv`, result.issuesCsv)),
      button('Consolidar de novo', run));
    if (manual.size) {
      actions.append(button(`Desfazer correspondências confirmadas (${manual.size})`, () => { manual.clear(); run(); }));
    }
    host.append(summary, actions);

    for (const kind of Object.keys(TITLES)) {
      const items = issues.filter(item => item.ocorrencia === kind);
      const group = el('details', 'consolidation-group');
      group.open = items.length > 0 && kind !== 'fora_da_lista';
      group.append(el('summary', '', `${TITLES[kind]} (${items.length})`));
      if (!items.length) {
        group.append(el('p', 'muted', 'Nenhuma.'));
        host.append(group);
        continue;
      }
      group.append(el('p', 'muted', HELP[kind]));
      if (kind !== 'nao_encontrado') {
        group.append(button('Mostrar na lista de participantes', () =>
          showParticipants(`Consolidação: ${TITLES[kind].toLowerCase()}`, items.map(item => item.participante_id))));
        const done = kind !== 'nao_credenciado';
        group.append(table(['Nome', 'Linha na lista', 'Situação', 'Guichê', ...(done ? ['Credenciado em', 'Por'] : [])],
          items.map(item => [item.nome, item.linha_lista, item.situacao, item.guiche,
            ...(done ? [item.credenciado_em ? new Date(item.credenciado_em).toLocaleString('pt-BR') : '',
              item.credenciado_por] : [])])));
      } else {
        group.append(table(['Nome na lista', 'Linha', 'Pode ser'], items.map(item => [item.nome, item.linha_lista,
          item.sugestoes.length ? item.sugestoes.map(person => {
            const line = el('span', 'consolidation-suggestion');
            const info = `${person.name} · guichê ${person.guiche} · ${person.status === 'completed' ? 'credenciado' :
              'não credenciado'}${person.cpf_prefix ? ` · CPF ${person.cpf_prefix}…` : ''}`;
            line.append(el('span', '', info), button('É a mesma pessoa', () => {
              manual.set(Number(item.linha_lista), person.id);
              run();
            }, 'link-button'));
            return line;
          }) : el('span', 'muted', 'Sem sugestão')])));
      }
      host.append(group);
    }
    host.hidden = false;
  }

  $('consolidation-file').addEventListener('change', () => {
    text = null;
    manual.clear();
    $('consolidation-result').hidden = true;
    status('');
  });
  $('consolidation-form').addEventListener('submit', async event => {
    event.preventDefault();
    const file = $('consolidation-file').files[0];
    if (!file) return;
    if (file.size > 20_000_000) return status('Arquivo muito grande. O limite é 20 MB.', true);
    const button = event.currentTarget.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      text = decode(await file.arrayBuffer());
      baseName = file.name.replace(/\.[^.]*$/, '') || 'lista';
      await run();
    } finally {
      button.disabled = false;
    }
  });
}
