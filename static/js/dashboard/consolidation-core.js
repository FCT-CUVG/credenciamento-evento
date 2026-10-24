// Consolidação da lista original de inscrição (ex.: exportação do ECOS) com o credenciamento, só no
// navegador: a lista tem CPF e e-mail em texto e não vai ao servidor. Sem a CHECKIN_LOOKUP_SECRET
// (que fica só no servidor), CPF e e-mail não podem ser comparados com as chaves guardadas; cada
// linha é procurada pelo nome e, quando a lista tem CPF, pelos 3 primeiros dígitos para separar
// homônimos. O que não bater recebe sugestões, e a coordenação confirma a mesma pessoa (manual).
// Sem DOM: os testes rodam este módulo no Node.

export const SITUACAO = {registered: 'inscrito (não chegou)', prechecked: 'chegou, aguardando busca',
  searching: 'em busca', ready: 'pronto no guichê', completed: 'credenciado'};
export const EXTRA = ['credenciado', 'credenciado_em', 'credenciado_por', 'buscado_por', 'guiche', 'situacao',
  'encontrado_por', 'participante_id', 'mesma_pessoa_que_linha'];
export const ISSUE_KINDS = ['cancelado_e_credenciado', 'fora_da_lista', 'nao_credenciado', 'nao_encontrado'];
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

// manual: Map(linha da planilha -> id do participante) confirmados pela coordenação.
export function consolidate(text, people, manual = new Map()) {
  const {fields, column, delimiter, rows, ignored} = readList(text);
  const byId = new Map(people.map(person => [person.id, person]));
  const byName = new Map();
  for (const person of people) {
    if (!byName.has(person.name_key)) byName.set(person.name_key, []);
    byName.get(person.name_key).push(person);
  }
  const find = (line, row) => {
    if (manual.has(line) && byId.has(manual.get(line))) return [byId.get(manual.get(line)), 'manual'];
    const cpf = cpfOf(row, column), prefix = cpf.length === 11 ? cpf.slice(0, 3) : '';
    const candidates = byName.get(normalize(row[column.nome])) || [];
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
        mesma_pessoa_que_linha: repeated ? String(firstLine.get(person.id)) : ''});
      if (!completed && !repeated) issues.push(issue('nao_credenciado', name, String(line), person));
    }
    output.push(values);
  }

  const cancelledLines = new Map(); // participante -> linhas canceladas que caíram nele
  for (const {line, person} of cancelled) {
    if (!person) continue;
    if (!cancelledLines.has(person.id)) cancelledLines.set(person.id, []);
    cancelledLines.get(person.id).push(String(line));
  }
  for (const person of people) {
    if (firstLine.has(person.id)) continue; // tem inscrição válida na lista
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
  const summary = {rows: valid.length + cancelled.length, cancelled: cancelled.length, valid: valid.length,
    matched: matched.length, matchedCompleted: matched.filter(p => p.status === 'completed').length,
    repeated: valid.length - count.nao_encontrado - matched.length, ignored, hasStatus: Boolean(column.status),
    hasCpf: Boolean(column.cpf), manual: valid.filter(item => item.foundBy === 'manual').length, issues: count,
    system: {total: people.length, arrived: people.filter(p => p.status !== 'registered').length,
      completed: people.filter(p => p.status === 'completed').length}};
  return {summary, issues, listCsv: toCsv([...fields, ...EXTRA], output, delimiter),
    issuesCsv: toCsv(ISSUE_FIELDS, issues, delimiter)};
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
