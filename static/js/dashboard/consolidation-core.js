// Consolidação da lista original de inscrição (ex.: exportação do ECOS) com o credenciamento, no
// navegador. A CHECKIN_LOOKUP_SECRET fica só no servidor: para comparar CPF e e-mail com as chaves
// guardadas, vão ao servidor só a linha, o CPF e o e-mail de cada inscrição (keyEntries), e ele
// devolve os participantes de cada uma sem guardar nada. Nome e demais colunas ficam aqui. Sem
// resultado pelas chaves, a linha é procurada pelo nome e pelos 3 primeiros dígitos do CPF. O que não
// bater recebe sugestões, e a coordenação confirma a mesma pessoa (manual).
//
// O status da lista (pago, cancelado...) não importa. Quando a mesma pessoa está em várias linhas,
// vale a da última movimentação (a coluna de data escolhida; sem ela, a última linha do arquivo).
// Sem DOM: os testes rodam este módulo no Node.

export const SITUACAO = {registered: 'inscrito (não chegou)', prechecked: 'chegou, aguardando busca',
  searching: 'em busca', ready: 'pronto no guichê', completed: 'credenciado'};
export const EXTRA = ['credenciado', 'credenciado_em', 'credenciado_por', 'buscado_por', 'guiche', 'situacao',
  'encontrado_por', 'participante_id', 'linha_mais_recente'];
export const ISSUE_KINDS = ['fora_da_lista', 'nao_credenciado', 'nao_encontrado'];
// Pessoas do credenciamento que não estão em nenhuma linha da lista (inclusive quem não tem CPF ou e-mail).
export const OTHER_FIELDS = ['nome', 'nome_cracha', 'afiliacao', 'pais', 'cpf_inicio', 'guiche', 'credenciado',
  'credenciado_em', 'credenciado_por', 'situacao', 'participante_id'];
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
    const column = Object.fromEntries(['nome', 'email', 'cpf'].map(name =>
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
    return {fields, column, delimiter, rows, ignored, movement: movementColumn(fields)};
  }
  throw new Error('A lista precisa das colunas Nome e E-mail.');
}

// Coluna da última movimentação, pelo nome (ex.: "Data da última movimentação", "Atualizado em").
export function movementColumn(fields) {
  const named = words => fields.find(field => words.some(word => columnName(field).includes(word)));
  return named(['ultimamovimentacao', 'ultimaatualizacao', 'ultimaalteracao']) ??
    named(['movimentacao', 'atualizacao', 'atualizadoem', 'alteracao', 'modificacao']) ?? null;
}

// Data e hora de uma célula em dd/mm/aaaa [hh:mm[:ss]] ou aaaa-mm-dd [hh:mm[:ss]], para comparar;
// null quando não dá para ler.
export function moment(value) {
  const text = String(value ?? '').trim();
  let match = /^(\d{1,2})\/(\d{1,2})\/(\d{4}|\d{2})(?!\d)(?:[ T,]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?/.exec(text);
  if (match) {
    const [, day, month, year, hour = 0, minute = 0, second = 0] = match;
    return Date.UTC(year.length === 2 ? 2000 + Number(year) : Number(year), month - 1, day, hour, minute, second);
  }
  match = /^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?/.exec(text);
  if (match) {
    const [, year, month, day, hour = 0, minute = 0, second = 0] = match;
    return Date.UTC(year, month - 1, day, hour, minute, second);
  }
  return null;
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
// movement: coluna da última movimentação; undefined usa a encontrada pelo nome, null usa a ordem do arquivo.
export function consolidate(text, people, manual = new Map(), keyMatches = null, movement = undefined) {
  const list = readList(text), {fields, column, delimiter, rows, ignored} = list;
  const dateColumn = movement === undefined ? list.movement : movement && fields.includes(movement) ? movement : null;
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

  // Linhas da mesma pessoa: a do credenciamento ou, sem ela, o mesmo CPF ou e-mail na lista.
  const items = rows.map(({line, row}) => {
    const [person, foundBy] = find(line, row);
    const cpf = cpfOf(row, column), email = String(row[column.email] ?? '').trim().toLowerCase();
    const group = person ? 'p:' + person.id : cpf.length === 11 ? 'c:' + cpf : email ? 'e:' + email : 'l:' + line;
    return {line, row, person, foundBy, group, at: dateColumn ? moment(row[dateColumn]) : null};
  });
  // Vale a linha da última movimentação; sem data legível, conta como mais antiga; empate, a de baixo.
  const later = (a, b) => ((a.at ?? -Infinity) - (b.at ?? -Infinity)) || (a.line - b.line);
  const latest = new Map();
  for (const item of items) {
    if (!latest.has(item.group) || later(item, latest.get(item.group)) > 0) latest.set(item.group, item);
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

  const issues = [], output = [], latestRows = [];
  for (const item of items) {
    const {line, row, person, foundBy} = item, main = latest.get(item.group), isLatest = main === item;
    const values = Object.fromEntries(fields.map(field => [field, row[field] ?? '']));
    values.linha_mais_recente = isLatest ? '' : String(main.line);
    const name = row[column.nome] ?? '';
    if (!person) {
      values.credenciado = 'não encontrado';
      if (isLatest) issues.push(issue('nao_encontrado', name, String(line)));
    } else {
      const completed = person.status === 'completed', info = details(person);
      Object.assign(values, {credenciado: completed ? 'sim' : 'não', credenciado_em: info.credenciado_em,
        credenciado_por: info.credenciado_por, buscado_por: person.claimed_by || '', guiche: person.guiche,
        situacao: info.situacao, encontrado_por: foundBy, participante_id: person.id});
      if (!completed && isLatest) issues.push(issue('nao_credenciado', name, String(line), person));
    }
    output.push(values);
    if (isLatest) latestRows.push(values);
  }

  const inList = new Set(items.filter(item => item.person).map(item => item.person.id));
  const others = [];
  for (const person of people) {
    if (inList.has(person.id)) continue;
    const info = details(person);
    others.push({nome: person.name, nome_cracha: person.badge_name || '', afiliacao: person.affiliation || '',
      pais: person.country || '', cpf_inicio: person.cpf_prefix || '', guiche: person.guiche,
      credenciado: person.status === 'completed' ? 'sim' : 'não', credenciado_em: info.credenciado_em,
      credenciado_por: info.credenciado_por, situacao: info.situacao, participante_id: person.id});
    issues.push(issue('fora_da_lista', person.name, '', person));
  }
  const order = item => ISSUE_KINDS.indexOf(item.ocorrencia);
  issues.sort((a, b) => (order(a) - order(b)) || normalize(a.nome).localeCompare(normalize(b.nome)));

  // Sugestões para as pessoas não encontradas: só quem do credenciamento não está na lista.
  const free = people.filter(person => !inList.has(person.id));
  const rowAt = new Map(items.map(item => [String(item.line), item.row]));
  for (const item of issues) {
    if (item.ocorrencia !== 'nao_encontrado') continue;
    const row = rowAt.get(item.linha_lista), cpf = cpfOf(row, column);
    item.sugestoes = suggest(normalize(item.nome), cpf.length === 11 ? cpf.slice(0, 3) : '', free);
  }

  const count = Object.fromEntries(ISSUE_KINDS.map(kind => [kind, issues.filter(i => i.ocorrencia === kind).length]));
  const completedRows = latestRows.filter(values => values.credenciado === 'sim');
  const summary = {
    list: {rows: output.length, people: latestRows.length, yes: completedRows.length, no: count.nao_credenciado,
      notFound: count.nao_encontrado},
    others: {total: others.length, completed: others.filter(item => item.credenciado === 'sim').length,
      noCpf: others.filter(item => !item.cpf_inicio).length},
    dateColumn, undated: dateColumn ? items.filter(item => item.at === null).length : 0, ignored,
    hasCpf: Boolean(column.cpf), manual: items.filter(item => item.foundBy === 'manual').length, issues: count,
    system: {total: people.length, arrived: people.filter(p => p.status !== 'registered').length,
      completed: people.filter(p => p.status === 'completed').length}};
  return {summary, issues, fields, listCsv: toCsv([...fields, ...EXTRA], output, delimiter),
    // Só os credenciados, na linha da última movimentação de cada um (com e sem as colunas do credenciamento).
    completedCsv: toCsv([...fields, ...EXTRA], completedRows, delimiter),
    completedOriginalCsv: toCsv(fields, completedRows, delimiter),
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
