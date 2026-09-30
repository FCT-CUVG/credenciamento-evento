// Entrada da equipe: leva cada papel para a sua tela.
import {$, api, homeFor, loadEventConfig, message} from './common.js';

await loadEventConfig();

const form = $('login-form');
form.addEventListener('submit', async event => {
  event.preventDefault();
  message('login-error', '');
  const button = form.querySelector('button');
  button.disabled = true;
  try {
    const data = await api('/api/login', {
      username: form.elements.namedItem('username').value,
      password: form.elements.namedItem('password').value
    });
    location.href = homeFor(data.role);
  } catch (err) {
    message('login-error', err.message);
  } finally {
    button.disabled = false;
  }
});
