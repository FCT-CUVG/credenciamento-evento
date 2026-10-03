// Identidade visual no painel: nomes, cores (com prévia e aviso de contraste) e logo do evento.
import {$, api, el} from '../common.js';

const COLORS = [
  ['primary', 'Principal', 'Títulos e painel de destaque'],
  ['navigation', 'Navegação', 'Nome no topo e links da equipe'],
  ['text', 'Texto', 'Texto principal'],
  ['text_muted', 'Texto secundário', 'Legendas e detalhes'],
  ['action', 'Botões', 'Botões e estados positivos'],
  ['action_hover', 'Botões com o mouse', 'Botão ao passar o mouse'],
  ['focus', 'Destaque', 'Linha do topo e contorno de foco'],
  ['page_background', 'Fundo da página', 'Atrás dos cartões'],
  ['surface', 'Fundo dos cartões', 'Também o texto sobre as cores fortes']
];
// Nome de cada cor no app.css (veja COLOR_ALIASES em event_theme.py).
const CSS_NAMES = {
  primary: '--navy', navigation: '--navy-header', text: '--ink', text_muted: '--slate', action: '--leaf',
  action_hover: '--leaf-dark', focus: '--gold', page_background: '--fog', surface: '--white'
};
// Pares de texto e fundo que aparecem nas páginas.
const PAIRS = [
  ['surface', 'primary', 'Texto sobre a cor principal'],
  ['surface', 'action', 'Texto dos botões'],
  ['surface', 'action_hover', 'Texto dos botões com o mouse em cima'],
  ['navigation', 'surface', 'Navegação sobre o fundo dos cartões'],
  ['text', 'page_background', 'Texto sobre o fundo da página'],
  ['text', 'surface', 'Texto sobre os cartões'],
  ['text_muted', 'surface', 'Texto secundário sobre os cartões']
];

function luminance(hex) {
  const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map(c => c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

const contrast = (a, b) => {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (light + 0.05) / (dark + 0.05);
};

const form = () => $('theme-form');
const field = name => form().elements.namedItem(name);

function status(text, error = false) {
  const node = $('theme-status');
  node.textContent = text;
  node.classList.toggle('error', error);
}

const colorValues = () => Object.fromEntries(COLORS.map(([key]) => [key, field(key).value]));

function renderPreview() {
  const colors = colorValues(), preview = $('theme-preview');
  for (const [key, name] of Object.entries(CSS_NAMES)) preview.style.setProperty(name, colors[key]);
  for (const [key] of COLORS) field(key).closest('.color-field').querySelector('.color-hex').textContent = colors[key];
  $('theme-preview-name').textContent = field('name').value;
  const warnings = PAIRS.map(([text, background, label]) => [label, contrast(colors[text], colors[background])])
    .filter(([, ratio]) => ratio < 4.5);
  $('theme-contrast').replaceChildren(...warnings.map(([label, ratio]) => el('li', '',
    `${label}: contraste de ${ratio.toFixed(1).replace('.', ',')}:1, abaixo do recomendado (4,5:1). Pode ficar difícil de ler.`)));
}

function renderColorFields() {
  $('theme-colors').replaceChildren(...COLORS.map(([key, label, hint]) => {
    const wrap = el('label', 'color-field'), input = el('input'), text = el('span', '', label);
    input.type = 'color';
    input.name = key;
    text.append(el('small', '', hint), el('small', 'color-hex'));
    wrap.append(input, text);
    return wrap;
  }));
}

// Mostra os valores em uso e aplica na própria página (tema e logo), sem recarregar.
function show(settings, live = false) {
  const {current} = settings;
  for (const key of ['name', 'short_name', 'registration_hint_pt', 'registration_hint_en']) field(key).value = current[key] || '';
  for (const [key] of COLORS) field(key).value = current.colors[key];
  $('theme-preview-logo').src = current.logo;
  $('logo-remove').hidden = !settings.custom_logo;
  $('theme-origin').textContent = settings.customized
    ? 'Esta identidade foi personalizada no painel.' : 'Em uso: o padrão do arquivo config/evento.yaml.';
  renderPreview();
  if (live) {
    const sheet = document.querySelector('link[href^="/theme.css"]');
    if (sheet) sheet.href = '/theme.css?v=' + Date.now();
    for (const img of document.querySelectorAll('.site-brand img')) img.src = current.logo;
  }
}

async function run(button, action, done) {
  button.disabled = true;
  status('Salvando...');
  try {
    show(await action(), true);
    status(done);
  } catch (err) {
    status(err.message, true);
  } finally {
    button.disabled = false;
  }
}

const readBase64 = file => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(String(reader.result).split(',', 2)[1] || '');
  reader.onerror = () => reject(new Error('Não foi possível ler o arquivo.'));
  reader.readAsDataURL(file);
});

export async function initTheme() {
  renderColorFields();
  form().addEventListener('input', renderPreview);
  form().addEventListener('submit', event => {
    event.preventDefault();
    const values = Object.fromEntries(['name', 'short_name', 'registration_hint_pt', 'registration_hint_en']
      .map(key => [key, field(key).value]));
    run(event.submitter || form().querySelector('[type="submit"]'),
      () => api('/api/theme', {...values, colors: colorValues()}), 'Nomes e cores salvos.');
  });
  $('theme-reset').addEventListener('click', event => {
    if (!confirm('Voltar nomes, cores e logo ao padrão do arquivo config/evento.yaml?')) return;
    run(event.currentTarget, () => api('/api/theme/reset', {}), 'Identidade visual padrão restaurada.');
  });
  $('logo-form').addEventListener('submit', event => {
    event.preventDefault();
    const file = $('logo-file').files[0];
    if (!file) return;
    if (file.size > 1_000_000) return status('A logo deve ter até 1 MB.', true);
    run(event.submitter || $('logo-form').querySelector('[type="submit"]'), async () => {
      const result = await api('/api/theme/logo', {data: await readBase64(file)});
      $('logo-form').reset();
      return result;
    }, 'Logo enviada.');
  });
  $('logo-remove').addEventListener('click', event => {
    if (!confirm('Voltar à logo padrão? As cores e os nomes continuam como estão.')) return;
    run(event.currentTarget, () => api('/api/theme/logo/remove', {}), 'Logo padrão restaurada.');
  });
  try {
    show(await api('/api/theme'));
  } catch (err) {
    status(err.message, true);
  }
}
