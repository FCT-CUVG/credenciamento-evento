// Aba "Backup e limpeza": baixar, restaurar e limpar participantes e histórico.
import {$, api} from '../common.js';

function status(text, error = false) {
  $('backup-status').textContent = text;
  $('backup-status').classList.toggle('error', error);
}

// Baixa o backup e só devolve depois de o navegador receber o arquivo inteiro.
async function downloadBackup() {
  const response = await fetch('/api/backup/download', {credentials: 'same-origin'});
  if (!response.ok) throw new Error('Não foi possível gerar o backup.');
  const name = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') || '')?.[1] || 'backup.json';
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
  return name;
}

const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;

export function initBackup(refresh) {
  let backup = null;

  $('backup-download').addEventListener('click', async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      status(`Backup baixado: ${await downloadBackup()}.`);
    } catch (err) {
      status(err.message, true);
    } finally {
      button.disabled = false;
    }
  });

  // Confirmação digitada: o botão só libera com a palavra exata.
  const form = $('restore-form'), restoreButton = form.querySelector('button[type="submit"]');
  const syncRestore = () => { restoreButton.disabled = !backup || $('restore-confirm').value.trim() !== 'RESTAURAR'; };
  $('restore-confirm').addEventListener('input', syncRestore);
  $('restore-file').addEventListener('change', async () => {
    backup = null;
    $('restore-summary').textContent = '';
    const file = $('restore-file').files[0];
    if (file) {
      try {
        const data = JSON.parse(await file.text());
        if (data?.format !== 'credenciamento-backup') throw new Error();
        backup = data;
        const credenciados = data.participants.filter(item => item.status === 'completed').length;
        $('restore-summary').textContent = `Backup de ${new Date(data.created_at).toLocaleString('pt-BR')}: ` +
          `${plural(data.participants.length, 'participante', 'participantes')} ` +
          `(${plural(credenciados, 'credenciado', 'credenciados')}) e ${plural(data.events.length, 'movimentação', 'movimentações')}.`;
      } catch {
        $('restore-summary').textContent = 'Este arquivo não é um backup do credenciamento.';
      }
    }
    syncRestore();
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!backup) return;
    restoreButton.disabled = true;
    status('Restaurando...');
    try {
      const result = await api('/api/backup/restore', {confirm: 'RESTAURAR', backup});
      status(`Backup restaurado: ${plural(result.participants, 'participante', 'participantes')} e ` +
        `${plural(result.events, 'movimentação', 'movimentações')}. O estado anterior foi guardado no servidor ` +
        `(${result.server_copy}).`);
      form.reset();
      backup = null;
      $('restore-summary').textContent = '';
      await refresh();
    } catch (err) {
      status(err.message, true);
    } finally {
      syncRestore();
    }
  });

  const clearButton = $('clear-participants');
  $('clear-confirm').addEventListener('input', () => {
    clearButton.disabled = $('clear-confirm').value.trim() !== 'LIMPAR';
  });
  clearButton.addEventListener('click', async () => {
    clearButton.disabled = true;
    try {
      // Sem o backup baixado, não limpa.
      status('Baixando o backup...');
      const name = await downloadBackup();
      status('Limpando...');
      const result = await api('/api/participants/clear', {confirm: 'LIMPAR'});
      status(`Backup baixado (${name}) e ${plural(result.participants, 'participante apagado', 'participantes apagados')}, ` +
        `com ${plural(result.events, 'movimentação', 'movimentações')}. Cópia também guardada no servidor (${result.server_copy}).`);
      $('clear-confirm').value = '';
      await refresh();
    } catch (err) {
      status(err.message, true);
      clearButton.disabled = $('clear-confirm').value.trim() !== 'LIMPAR';
    }
  });
}
