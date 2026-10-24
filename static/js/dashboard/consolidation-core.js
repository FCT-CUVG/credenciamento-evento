// Consolidação da lista original de inscrição (ex.: exportação do ECOS) com o credenciamento, no
// navegador. A CHECKIN_LOOKUP_SECRET fica só no servidor: para comparar CPF e e-mail com as chaves
// guardadas, vão ao servidor só a linha, o CPF e o e-mail de cada inscrição (keyEntries), e ele
// devolve os participantes de cada uma sem guardar nada. Nome e demais colunas ficam aqui. Sem
// resultado pelas chaves, a linha é procurada pelo nome e pelos 3 primeiros dígitos do CPF. O que não
// bater recebe sugestões, e a coordenação confirma a mesma pessoa (manual).
// Sem DOM: os testes rodam este módulo no Node.

export const SITUACAO = {registered: 'inscrito (não chegou)', prechecked: 'chegou, aguardando busca',
  searching: 'em busca', ready: 'pronto no guichê', completed: 'credenciado'};
export const EXTRA = ['credenciado', 'credenciado_em', 'credenciado_por', 'buscado_por', 'guiche', 'situacao',
  'encontrado_por', 'participante_id', 'mesma_pessoa_que_linha', 'linhas_canceladas_mesma_pessoa'];
export const ISSUE_KINDS = ['cancelado_e_credenciado', 'fora_da_lista', 'nao_credenciado', 'nao_encontrado'];
// Pessoas do credenciamento sem inscrição válida na lista (inclusive quem não tem CPF ou e-mail).
export const OTHER_FIELDS = ['nome', 'nome_cracha', 'afiliacao', 'pais', 'cpf_inicio', 'guiche', 'credenciado',
  'credenciado_em', 'credenciado_por', 'situacao', 'motivo', 'participante_id'];
export const ISSUE_FIELDS = ['ocorrencia', 'nome', 'linha_lista', 'situacao', 'credenciado_em', 'credenciado_por',
  'guiche', 'participante_id'];

// Igual a common.normalize do servidor: sem acentos, minúsculas e espaços simples.
export const normalize = value => String(value ?? '').trim().normalize('NFKD').replace(/\p{Mn}/gu, '')
  .toLowerCase().replace(/ß/g, 'ss').split(/\s+/).filter(Boolean).join(' ');

// UTF-8 (com ou sem BOM) ou, se não for, Windows-1252 (CSV salvo pelo Excel).
export function decode(buffer) {
  try {
    return new TextDecoder('utf-8', {fatal: true}).decode(buffer);
  } catch {
    return new TextDecoder('windows-1252').decode(buffer);
  }
}

// CSV com aspas (inclusive quebras de linha dentro do campo). Linha vazia vira um registro [''].
export function parseCsv(text, delimiter) {
  const records = [];
  let record = [], field = '', quoted = false, atStart = true;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') quoted = false;
      else field += c;
    } else if (c === '"' && atStart) {
      quoted = true;
      atStart = false;
    } else if (c === delimiter) {
      record.push(field);
      field = '';
      atStart = true;
    } else if (c === '\r' || c === '\n') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      record.push(field);
      records.push(record);
      record = [];
      field = '';
      atStart = true;
    } else {
      field += c;
      atStart = false;
    }
  }
  if (field || record.length || quoted) {
    record.push(field);
    records.push(record);
  }
  return records;
}

const columnName = field => normalize(field).replace(/[^a-z0-9]/g, '');

// Separador ponto e vírgula, vírgula ou tabulação: vale o primeiro com as colunas Nome e E-mail.
// A linha de cada registro é a da planilha (cabeçalho = 1). Linha sem nome (ex.: total ou rodapé da
// exportação) não é inscrição: fica em `ignored`, com o que tem, para o resumo avisar.
export function readList(text) {
  for (const delimiter of [';', ',', '\t']) {
    const [header = [], ...records] = parseCsv(text, delimiter);
    const fields = header.map(field => field.trim());
    const column = Object.fromEntries(['nome', 'email', 'cpf', 'status'].map(name =>
      [name, fields.find(field => columnName(field) === name) ?? null]));
    if (!column.nome || !column.email) continue;
    const rows = [], ignored = [];
    records.forEach((values, index) => {
      if (!values.some(value => value.trim())) return;
      const row = {};
      fields.forEach((field, position) => { row[field] = values[position] ?? ''; });
      if (row[column.nome].trim()) rows.push({line: index + 2, row});
      else ignored.push({line: index + 2, text: values.map(value => value.trim()).filter(Boolean).join(' · ')});
    });
    return {fields, column, delimiter, rows, ignored};
  }
  throw new Error('A lista precisa das colunas Nome e E-mail.');
}

const cpfOf = (row, column) => {
  const digits = column.cpf ? String(row[column.cpf] ?? '').replace(/\D/g, '') : '';
  return digits.length >= 9 && digits.length < 11 ? digits.padStart(11, '0') : digits; // o Excel tira os zeros
};
const words = key => new Set(key.split(' ').filter(word => word.length > 2));

// Pessoas parecidas com a linha (nome igual ou duas palavras do nome em comum), para confirmar à mão.
// CPF com outro início, nos dois lados, só conta se o nome for igual.
function suggest(nameKey, prefix, people) {
  const mine = words(nameKey);
  return people.map(person => {
    const same = person.name_key === nameKey, shared = [...words(person.name_key)].filter(w => mine.has(w)).length;
    const samePrefix = Boolean(prefix && person.cpf_prefix === prefix);
    const otherPrefix = Boolean(prefix && person.cpf_prefix && person.cpf_prefix !== prefix);
    return {person, same, shared, samePrefix, keep: same || (shared >= 2 && !otherPrefix)};
  }).filter(item => item.keep)
    .sort((a, b) => (b.same - a.same) || (b.samePrefix - a.samePrefix) || (b.shared - a.shared))
    .slice(0, 3).map(item => item.person);
}

// O que vai ao servidor para comparar com as chaves: só a linha, o CPF e o e-mail de cada inscrição.
export function keyEntries(text) {
  const {column, rows} = readList(text);
  return rows.map(({line, row}) => ({line, cpf: cpfOf(row, column), email: String(row[column.email] ?? '').trim()}))
    .filter(entry => entry.cpf.length === 11 || entry.email.includes('@'));
}

// manual: Map(linha da planilha -> id do participante) confirmados pela coordenação.
// keyMatches: Map(linha -> {cpf: id ou null, email: [ids]}) devolvido pelo servidor (null: só pelo nome).
export function consolidate(text, people, manual = new Map(), keyMatches = null) {
  const {fields, column, delimiter, rows, ignored} = readList(text);
  const byId = new Map(people.map(person => [person.id, person]));
  const byName = new Map();
  for (const person of people) {
    if (!byName.has(person.name_key)) byName.set(person.name_key, []);
    byName.get(person.name_key).push(person);
  }
  const find = (line, row) => {
    if (manual.has(line) && byId.has(manual.get(line))) return [byId.get(manual.get(line)), 'manual'];
    const nameKey = normalize(row[column.nome]), keys = keyMatches?.get(line);
    if (keys) {
      // Como no scripts/credenciados.py: nome + e-mail, depois CPF, depois e-mail de uma só pessoa.
      const byEmail = keys.email.map(id => byId.get(id)).filter(Boolean);
      const sameName = byEmail.find(person => person.name_key === nameKey);
      if (sameName) return [sameName, 'nome+email'];
      if (keys.cpf && byId.has(keys.cpf)) return [byId.get(keys.cpf), 'cpf'];
      if (byEmail.length === 1) return [byEmail[0], 'email'];
    }
    const cpf = cpfOf(row, column), prefix = cpf.length === 11 ? cpf.slice(0, 3) : '';
    const candidates = byName.get(nameKey) || [];
    const pool = prefix ? candidates.filter(p => !p.cpf_prefix || p.cpf_prefix === prefix) : candidates;
    if (pool.length !== 1) return [null, ''];
    return [pool[0], prefix && pool[0].cpf_prefix ? 'nome+cpf_inicio' : 'nome'];
  };

  const valid = [], cancelled = [];
  for (const {line, row} of rows) {
    const isCancelled = column.status && normalize(row[column.status]).startsWith('cancel');
    const [person, foundBy] = find(line, row);
    (isCancelled ? cancelled : valid).push({line, row, person, foundBy});
  }

  const details = person => {
    const completed = person.status === 'completed';
    return {situacao: SITUACAO[person.status] || person.status,
      credenciado_em: completed ? person.completed_at || '' : '',
      credenciado_por: completed ? person.delivered_by || '' : '',
      guiche: person.guiche, participante_id: person.id};
  };
  const issue = (kind, name, line, person) => ({ocorrencia: kind, nome: name, linha_lista: line,
    ...(person ? details(person) : Object.fromEntries(ISSUE_FIELDS.slice(3).map(key => [key, ''])))});

  // Cada linha é uma inscrição com o status atual dela, então a ordem não importa: quem tem alguma
  // linha válida está na lista, e as linhas canceladas da mesma pessoa só ficam anotadas.
  const cancelledLines = new Map(); // participante -> linhas canceladas que caíram nele
  for (const {line, person} of cancelled) {
    if (!person) continue;
    if (!cancelledLines.has(person.id)) cancelledLines.set(person.id, []);
    cancelledLines.get(person.id).push(String(line));
  }

  const firstLine = new Map(); // participante -> primeira linha válida da planilha em que apareceu
  const issues = [], output = [];
  for (const {line, row, person, foundBy} of valid) {
    const values = Object.fromEntries(fields.map(field => [field, row[field] ?? '']));
    const name = row[column.nome] ?? '';
    if (!person) {
      values.credenciado = 'não encontrado';
      issues.push(issue('nao_encontrado', name, String(line)));
    } else {
      const completed = person.status === 'completed', repeated = firstLine.has(person.id);
      if (!repeated) firstLine.set(person.id, line);
      const info = details(person);
      Object.assign(values, {credenciado: completed ? 'sim' : 'não', credenciado_em: info.credenciado_em,
        credenciado_por: info.credenciado_por, buscado_por: person.claimed_by || '', guiche: person.guiche,
        situacao: info.situacao, encontrado_por: foundBy, participante_id: person.id,
        mesma_pessoa_que_linha: repeated ? String(firstLine.get(person.id)) : '',
        linhas_canceladas_mesma_pessoa: (cancelledLines.get(person.id) || []).join(', ')});
      if (!completed && !repeated) issues.push(issue('nao_credenciado', name, String(line), person));
    }
    output.push(values);
  }

  const others = [];
  for (const person of people) {
    if (firstLine.has(person.id)) continue; // tem inscrição válida na lista
    const info = details(person), lines = cancelledLines.get(person.id);
    others.push({nome: person.name, nome_cracha: person.badge_name || '', afiliacao: person.affiliation || '',
      pais: person.country || '', cpf_inicio: person.cpf_prefix || '', guiche: person.guiche,
      credenciado: person.status === 'completed' ? 'sim' : 'não', credenciado_em: info.credenciado_em,
      credenciado_por: info.credenciado_por, situacao: info.situacao, participante_id: person.id,
      motivo: lines ? `inscrição cancelada na lista (linha ${lines.join(', ')})` : 'não está na lista'});
    if (cancelledLines.has(person.id)) {
      if (person.status === 'completed') {
        issues.push(issue('cancelado_e_credenciado', person.name, cancelledLines.get(person.id).join(','), person));
      }
    } else {
      issues.push(issue('fora_da_lista', person.name, '', person));
    }
  }
  const order = item => ISSUE_KINDS.indexOf(item.ocorrencia);
  issues.sort((a, b) => (order(a) - order(b)) || normalize(a.nome).localeCompare(normalize(b.nome)));

  // Sugestões para as linhas não encontradas: só quem não ficou com outra linha válida.
  const free = people.filter(person => !firstLine.has(person.id));
  const rowAt = new Map(valid.map(item => [String(item.line), item.row]));
  for (const item of issues) {
    if (item.ocorrencia !== 'nao_encontrado') continue;
    const row = rowAt.get(item.linha_lista), cpf = cpfOf(row, column);
    item.sugestoes = suggest(normalize(item.nome), cpf.length === 11 ? cpf.slice(0, 3) : '', free);
  }

  const count = Object.fromEntries(ISSUE_KINDS.map(kind => [kind, issues.filter(i => i.ocorrencia === kind).length]));
  const matched = people.filter(person => firstLine.has(person.id));
  const listCount = value => output.filter(values => values.credenciado === value).length;
  const summary = {list: {rows: output.length, yes: listCount('sim'), no: listCount('não'),
      notFound: listCount('não encontrado')},
    others: {total: others.length, completed: others.filter(item => item.credenciado === 'sim').length,
      noCpf: others.filter(item => !item.cpf_inicio).length},
    // Quantas linhas de cada status: só os que começam por "cancel" saem da lista.
    statuses: column.status ? Object.entries(rows.reduce((counts, {row}) => {
      const value = String(row[column.status] ?? '').trim() || '(vazio)';
      counts[value] = (counts[value] || 0) + 1;
      return counts;
    }, {})).sort((a, b) => b[1] - a[1]) : [],
    rows: valid.length + cancelled.length, cancelled: cancelled.length, valid: valid.length,
    matched: matched.length, matchedCompleted: matched.filter(p => p.status === 'completed').length,
    repeated: valid.length - count.nao_encontrado - matched.length, ignored, hasStatus: Boolean(column.status),
    hasCpf: Boolean(column.cpf), manual: valid.filter(item => item.foundBy === 'manual').length, issues: count,
    system: {total: people.length, arrived: people.filter(p => p.status !== 'registered').length,
      completed: people.filter(p => p.status === 'completed').length}};
  // Só os credenciados da lista, uma linha por pessoa.
  const completedRows = output.filter(values => values.credenciado === 'sim' && !values.mesma_pessoa_que_linha);
  return {summary, issues, listCsv: toCsv([...fields, ...EXTRA], output, delimiter),
    completedCsv: toCsv([...fields, ...EXTRA], completedRows, delimiter),
    othersCsv: toCsv(OTHER_FIELDS, others, delimiter), issuesCsv: toCsv(ISSUE_FIELDS, issues, delimiter)};
}

// Evita que a planilha execute o conteúdo da célula como fórmula.
const safe = value => typeof value === 'string' && /^[=+\-@]/.test(value.trimStart()) ? "'" + value : value;

export function toCsv(fields, records, delimiter) {
  const cell = value => {
    const text = String(safe(value ?? ''));
    return text.includes(delimiter) || /["\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return [fields, ...records.map(record => fields.map(field => record[field]))]
    .map(values => values.map(cell).join(delimiter)).join('\r\n') + '\r\n';
}
