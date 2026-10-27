// Aba "Pagamento": quais situações de pagamento liberam o credenciamento.
import {$, api, el, searchKey} from '../common.js';

let options = [];

function status(text, error = false) {
  $('payment-status').textContent = text;
  $('payment-status').classList.toggle('error', error);
}

function render() {
  const host = $('payment-options');
  host.replaceChildren();
  if (!options.length) host.append(el('p', 'muted', 'Nenhuma situação ainda. Importe participantes ou adicione abaixo.'));
  for (const option of options) {
    const row = el('div', 'payment-option' + (option.ok ? ' is-ok' : ''));
    const label = el('label', 'payment-option-label'), box = el('input');
    box.type = 'checkbox';
    box.checked = option.ok;
    box.addEventListener('change', () => {
      option.ok = box.checked;
      row.classList.toggle('is-ok', box.checked);
      status('Alterações não salvas.');
    });
    label.append(box, el('span', 'payment-option-name', option.value));
    row.append(label, el('span', 'muted', `${option.total} ${option.total === 1 ? 'pessoa' : 'pessoas'}`),
      el('span', 'payment-option-state', option.ok ? 'Libera' : 'Pendência'));
    box.addEventListener('change', () => {
      row.querySelector('.payment-option-state').textContent = box.checked ? 'Libera' : 'Pendência';
    });
    // Só dá para tirar da lista uma situação que ninguém tem.
    if (!option.total) {
      const remove = el('button', 'link-button', 'Remover');
      remove.type = 'button';
      remove.addEventListener('click', () => {
        options = options.filter(other => other !== option);
        render();
        status('Alterações não salvas.');
      });
      row.append(remove);
    }
    host.append(row);
  }
}

async function load() {
  try {
    options = (await api('/api/payment/config')).options;
    render();
  } catch (err) {
    status(err.message, true);
  }
}

export async function initPayment(refresh) {
  $('payment-add-form').addEventListener('submit', event => {
    event.preventDefault();
    const value = $('payment-new').value.trim().replace(/\s+/g, ' ');
    if (!value) return;
    if (options.some(option => searchKey(option.value) === searchKey(value))) {
      status(`“${value}” já está na lista.`, true);
      return;
    }
    options.push({value, total: 0, ok: false});
    $('payment-new').value = '';
    render();
    status('Alterações não salvas.');
  });
  $('payment-save').addEventListener('click', async event => {
    const button = event.currentTarget;
    button.disabled = true;
    status('Salvando...');
    try {
      const result = await api('/api/payment/config', {
        ok: options.filter(option => option.ok).map(option => option.value),
        options: options.filter(option => !option.total).map(option => option.value)
      });
      options = result.options;
      render();
      status('Situações salvas. ' + (result.updated
        ? `${result.updated} ${result.updated === 1 ? 'participante mudou' : 'participantes mudaram'} entre liberado e pendência.`
        : 'Ninguém mudou entre liberado e pendência.'));
      await refresh();
    } catch (err) {
      status(err.message, true);
    } finally {
      button.disabled = false;
    }
  });
  // Ao abrir a tela, relê as situações (uma importação pode ter trazido novas), sem perder edições.
  document.addEventListener('admin:view', event => {
    if (event.detail === 'pagamento' && $('payment-status').textContent !== 'Alterações não salvas.') load();
  });
  await load();
}
