// Aba "Consolidação": lista original de inscrição (ex.: ECOS) x credenciamento, neste navegador.
// O arquivo fica na memória da aba (nem localStorage); recarregar apaga. Ao servidor vão só a linha,
// o CPF e o e-mail de cada inscrição, para comparar com as chaves, e ele não guarda nada.
import {$, api, el} from '../common.js';
import {consolidate, decode, keyEntries, readList} from './consolidation-core.js';

const TITLES = {
  fora_da_lista: 'No credenciamento, mas fora da lista',
  nao_credenciado: 'Pessoas da lista não credenciadas',
  nao_encontrado: 'Pessoas da lista não encontradas no credenciamento'
};
const HELP = {
  fora_da_lista: 'Estão no credenciamento, mas em nenhuma linha da lista. Quem tem CPF e e-mail diferentes ' +
    'nos dois sistemas aparece aqui e também em “não encontradas”: confirme a correspondência lá.',
  nao_credenciado: 'A pessoa está na lista e ainda não retirou o kit. Para resolver, mostre-as na lista ' +
    'de participantes e mude a situação na linha ou em lote.',
  nao_encontrado: 'Nenhuma pessoa do credenciamento com esse CPF, e-mail ou nome. Se uma sugestão for a mesma ' +
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
      // Só linha, CPF e e-mail vão ao servidor, que compara com as chaves e não guarda nada.
      const {matches} = await api('/api/consolidation/match', {entries: keyEntries(text)});
      render(consolidate(text, people, manual, new Map(matches.map(match => [match.line, match])),
        $('consolidation-movement').value || null));
      status('');
    } catch (err) {
      $('consolidation-result').hidden = true;
      status(err.message, true);
    }
  }

  function render(result) {
    const {summary: s, issues} = result, host = $('consolidation-result');
    host.replaceChildren();
    const button = (label, onClick, className = 'button secondary small') => {
      const node = el('button', className, label);
      node.type = 'button';
      node.addEventListener('click', onClick);
      return node;
    };
    const buttons = (...nodes) => {
      const row = el('div', 'consolidation-downloads');
      row.append(...nodes);
      return row;
    };
    // As duas listas para baixar: a enviada, com o credenciamento de cada linha, e os demais.
    const part = (title, items, action) => {
      const box = el('section', 'consolidation-part'), list = el('ul', 'consolidation-summary');
      for (const item of items.filter(Boolean)) list.append(el('li', '', item));
      box.append(el('h3', '', title), list, action);
      return box;
    };
    const parts = el('div', 'consolidation-parts');
    parts.append(
      part(`Sua lista: ${plural(s.list.people, 'pessoa', 'pessoas')} em ${plural(s.list.rows, 'linha', 'linhas')}`, [
        `${plural(s.list.yes, 'credenciada', 'credenciadas')}`,
        `${plural(s.list.no, 'não credenciada', 'não credenciadas')}`,
        `${plural(s.list.notFound, 'não encontrada', 'não encontradas')} no credenciamento`,
        s.list.cancelled ? `${plural(s.list.cancelled, 'inscrição cancelada ficou', 'inscrições canceladas ficaram')} ` +
          'de fora (quem foi credenciado conta, mesmo cancelado).' : '',
        s.list.rows > s.list.people + s.list.cancelled ? 'Quem está em mais de uma linha conta pela linha da última ' +
          'movimentação ' + (s.dateColumn ? `(coluna “${s.dateColumn}”).` : '(sem coluna de data: a última linha do arquivo).')
          : '',
        // Só avisa se nenhuma data da coluna escolhida pôde ser lida.
        s.dateColumn && s.undated === s.list.rows ? `Nenhuma data da coluna “${s.dateColumn}” pôde ser lida; ` +
          'escolha outra coluna.' : ''
      ], buttons(
        button('Baixar sua lista com o credenciamento',
          () => download(`${baseName}-atualizada.csv`, result.listCsv), 'button small'),
        button(`Baixar só os credenciados (${s.list.yes})`,
          () => download(`${baseName}-credenciados.csv`, result.completedCsv)),
        button('Só os credenciados, com as colunas da sua lista',
          () => download(`${baseName}-credenciados-colunas-originais.csv`, result.completedOriginalCsv)))),
      part(`No credenciamento, fora da sua lista: ${plural(s.others.total, 'pessoa', 'pessoas')}`, [
        `${plural(s.others.completed, 'credenciada', 'credenciadas')}`,
        `${plural(s.others.total - s.others.completed, 'não credenciada', 'não credenciadas')}`,
        s.others.noCpf ? `${s.others.noCpf} sem CPF no cadastro` : ''
      ], button('Baixar os demais', () => download(`${baseName}-fora-da-lista.csv`, result.othersCsv), 'button small')));

    const lines = [
      `Credenciamento (deve bater com o painel): ${s.system.total} inscritos; ${s.system.arrived} chegaram; ` +
        `${s.system.completed} credenciados.`
    ];
    if (s.manual) lines.push(`${plural(s.manual, 'correspondência confirmada', 'correspondências confirmadas')} à mão.`);
    if (!s.hasCpf) lines.push('A lista não tem coluna CPF: a comparação é pelo nome e pelo e-mail.');
    const summary = el('ul', 'consolidation-summary');
    for (const line of lines) summary.append(el('li', '', line));

    const actions = el('div', 'dashboard-tool-actions');
    actions.append(
      button('Baixar pendências', () => download(`${baseName}-pendencias.csv`, result.issuesCsv)),
      button('Consolidar de novo', run));
    if (manual.size) {
      actions.append(button(`Desfazer correspondências confirmadas (${manual.size})`, () => { manual.clear(); run(); }));
    }
    host.append(parts, summary, actions);

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

  // Ao escolher o arquivo, lê o cabeçalho para oferecer a coluna da última movimentação.
  const movement = $('consolidation-movement');
  $('consolidation-file').addEventListener('change', async () => {
    text = null;
    manual.clear();
    $('consolidation-result').hidden = true;
    status('');
    movement.replaceChildren(new Option('Nenhuma: vale a última linha do arquivo', ''));
    const file = $('consolidation-file').files[0];
    if (!file || file.size > 20_000_000) return;
    try {
      const list = readList(decode(await file.arrayBuffer()));
      for (const field of list.fields) movement.append(new Option(field, field));
      movement.value = list.movement || '';
    } catch (err) {
      status(err.message, true);
    }
  });
  movement.addEventListener('change', () => { if (!$('consolidation-result').hidden) run(); });
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
