/** Bound script for the backup spreadsheet. Deploy as a web app running as owner. */
function doPost(e) {
  try {
    const input = JSON.parse(e.postData.contents);
    const expected = PropertiesService.getScriptProperties().getProperty('CHECKIN_SHEETS_SECRET');
    if (!expected || input.secret !== expected) throw new Error('Chave inválida');
    if (!Array.isArray(input.records) || input.records.length > 40) throw new Error('Lote inválido');
    const lock = LockService.getScriptLock();
    lock.waitLock(30000);
    try {
      const book = SpreadsheetApp.getActiveSpreadsheet();
      const people = getSheet(book, 'Participantes', ['ID', 'Nome', 'Nome no crachá', 'Email', 'CPF', 'Afiliação', 'Pago', 'Prioridade', 'Guichê', 'Estado', 'Responsável', 'Pré-check-in', 'Busca iniciada', 'Pronto no guichê', 'Retirado em', 'Atualizado em', 'Revisão']);
      const history = getSheet(book, 'Histórico', ['ID do evento', 'ID do participante', 'Ação', 'Responsável', 'Data e hora']);
      const peopleRows = people.getLastRow() > 1 ? people.getRange(2, 1, people.getLastRow() - 1, 17).getValues() : [];
      const eventRows = history.getLastRow() > 1 ? history.getRange(2, 1, history.getLastRow() - 1, 1).getValues() : [];
      const personIndex = new Map(peopleRows.map((r, i) => [String(r[0]), i + 2]));
      const eventIds = new Set(eventRows.map(r => String(r[0])));
      for (const record of input.records) {
        const p = record.participant, event = record.event;
        if (!p || !event || !p.id || !event.id) throw new Error('Registro incompleto');
        const row = [p.id, safe(p.name), safe(p.badge_name || ''), safe(p.email), safe(p.cpf || ''), safe(p.affiliation || ''),
          p.paid ? 1 : 0, p.priority ? 1 : 0, safe(p.guiche), p.status, safe(p.claimed_by || ''),
          p.prechecked_at || '', p.claimed_at || '', p.ready_at || '', p.completed_at || '', p.updated_at, Number(p.revision)];
        const at = personIndex.get(String(p.id));
        if (at) {
          const current = Number(people.getRange(at, 17).getValue()) || 0;
          if (Number(p.revision) >= current) people.getRange(at, 1, 1, row.length).setValues([row]);
        } else {
          people.appendRow(row);
          personIndex.set(String(p.id), people.getLastRow());
        }
        if (!eventIds.has(String(event.id))) {
          history.appendRow([event.id, event.participant_id, event.action, safe(event.actor), event.occurred_at]);
          eventIds.add(String(event.id));
        }
      }
      SpreadsheetApp.flush();
    } finally {
      lock.releaseLock();
    }
    return ContentService.createTextOutput(JSON.stringify({ok: true})).setMimeType(ContentService.MimeType.JSON);
  } catch (error) {
    return ContentService.createTextOutput(JSON.stringify({ok: false, error: String(error)})).setMimeType(ContentService.MimeType.JSON);
  }
}

function getSheet(book, name, header) {
  let sheet = book.getSheetByName(name);
  if (!sheet) sheet = book.insertSheet(name);
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(header);
    sheet.setFrozenRows(1);
  } else if (sheet.getLastColumn() < header.length) {
    sheet.getRange(1, 1, 1, header.length).setValues([header]);
  }
  return sheet;
}

function safe(value) {
  const text = String(value || '');
  return /^[=+\-@]/.test(text.trimStart()) ? "'" + text : text;
}
