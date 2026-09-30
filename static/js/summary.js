// Painel resumido público: inscritos, chegadas e credenciados.
import {$, api, loadEventConfig, message} from './common.js';

function setNumber(id, value) {
  const node = $(id);
  node.textContent = value;
  node.classList.toggle('many-digits', String(value).length > 3);
}

async function refresh() {
  try {
    const data = await api('/api/dashboard/summary');
    setNumber('stat-total', data.total);
    setNumber('stat-arrived', data.arrived);
    setNumber('stat-completed', data.completed);
    message('page-error', '');
  } catch (err) {
    message('page-error', err.message);
  }
}

await loadEventConfig();
await refresh();
setInterval(refresh, 10000);
