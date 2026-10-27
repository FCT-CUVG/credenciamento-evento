// Utilitários compartilhados por todas as páginas: DOM, chamadas à API, sessão da equipe e menu.

export const $ = id => document.getElementById(id);

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export const statusNames = {
  registered: 'Inscrito',
  prechecked: 'Aguardando busca',
  searching: 'Em busca',
  ready: 'Pronto no guichê',
  completed: 'Credenciado'
};

// Sessão da equipe (preenchida por requireSession); o token CSRF vai em todo POST.
export let session = null;

// Identidade do evento carregada de /api/event.
export const eventInfo = {name: 'Evento', registrationHints: {}, checkinOpen: true};

export async function api(path, data) {
  if (location.protocol === 'file:') {
    throw new Error('Abra o sistema pelo servidor em http://127.0.0.1:8000, não diretamente pelo arquivo.');
  }
  const options = {credentials: 'same-origin'};
  if (data !== undefined) {
    options.method = 'POST';
    options.headers = {'Content-Type': 'application/json'};
    if (session?.csrf) options.headers['X-CSRF-Token'] = session.csrf;
    options.body = JSON.stringify(data);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error('Não foi possível conectar ao servidor. A página tentará novamente.');
  }
  const body = await response.json();
  if (!response.ok) {
    const error = new Error(body.error || 'Não foi possível concluir. Tente novamente.');
    error.status = response.status;
    error.data = body;
    throw error;
  }
  return body;
}

// Mostra (ou esconde, com texto vazio) uma mensagem num elemento de aviso.
export function message(id, text) {
  const node = $(id);
  if (!node) return;
  node.textContent = text;
  node.classList.toggle('hidden', !text);
}

export async function loadEventConfig() {
  try {
    const event = await api('/api/event');
    eventInfo.name = event.name;
    eventInfo.registrationHints = event.registration_hints || {};
    eventInfo.checkinOpen = event.checkin_open !== false;
    for (const img of document.querySelectorAll('.site-brand img, .summary-logo')) {
      img.src = event.logo;
      img.alt = event.name;
    }
    for (const link of document.querySelectorAll('.site-brand')) {
      link.setAttribute('aria-label', event.name + ' — início do credenciamento');
    }
  } catch (err) {
    console.error('Configuração visual:', err);
  }
}

export const homeFor = role => role === 'volunteer' ? '/separacao' : role === 'attendant' ? '/fila' : '/admin';

// Configurações do admin: cada uma tem endereço próprio e aparece no menu "Configurações".
export const ADMIN_SECTIONS = [
  ['participantes', 'Importar participantes'],
  ['guiches', 'Configuração dos guichês'],
  ['pagamento', 'Pagamento'],
  ['backup', 'Backup e limpeza'],
  ['consolidacao', 'Consolidação'],
  ['identidade', 'Identidade visual'],
  ['google-sheets', 'Backup no Google Sheets'],
];

const navLink = (label, href) => {
  const a = el('a', '', label);
  a.href = href;
  return a;
};

// Menu suspenso do cabeçalho; fecha ao clicar fora, com Esc ou ao escolher uma opção.
function navMenu(label, options) {
  const menu = el('details', 'nav-menu'), choices = el('div', 'nav-options');
  menu.append(el('summary', '', label));
  choices.append(...options);
  menu.append(choices);
  document.addEventListener('click', event => { if (!menu.contains(event.target)) menu.open = false; });
  menu.addEventListener('keydown', event => { if (event.key === 'Escape') menu.open = false; });
  choices.addEventListener('click', event => { if (event.target.closest('a')) menu.open = false; });
  return menu;
}

// Destaca no menu a página aberta (também quando o admin troca de tela sem recarregar).
export function markCurrentPage() {
  const here = location.pathname + location.search;
  for (const a of document.querySelectorAll('#team-links a')) {
    if (a.getAttribute('href') === here) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  }
}

export async function renderTeamNav() {
  const nav = $('team-links');
  if (!nav) return;
  const desks = [navLink('Todos os guichês', '/fila?guiche=all')];
  try {
    const data = await api('/api/guiches');
    // Com guichê de prioridade, uma opção junta só os guichês gerais (por letras).
    if (data.guiches.some(desk => desk.priority)) {
      desks.push(navLink('Guichês gerais (sem prioridade)', '/fila?guiche=all&exclude=priority'));
    }
    desks.push(el('hr', 'nav-separator'));
    for (const desk of data.guiches) {
      const detail = desk.priority ? (/priorid/i.test(desk.id) ? '' : ' · Prioridade')
        : desk.ranges.length ? ' · ' + desk.ranges.join(', ') : '';
      desks.push(navLink('Guichê ' + desk.id + detail, '/fila?guiche=' + encodeURIComponent(desk.id)));
    }
  } catch (err) {
    message('page-error', err.message);
  }
  nav.replaceChildren(navLink('Separação', '/separacao'), navMenu('Guichês', desks), navLink('Dashboard', '/dashboard'));
  if (session.user.role === 'admin') {
    nav.append(navLink('Admin', '/admin'),
      navMenu('Configurações', ADMIN_SECTIONS.map(([slug, label]) => navLink(label, '/admin/' + slug))));
  }
  markCurrentPage();
}

// Exige login com um dos papéis; senão, redireciona para o login ou para a tela do papel.
export async function requireSession(roles) {
  session = await api('/api/me');
  if (!session.user) {
    location.href = '/login';
    return false;
  }
  if (!roles.includes(session.user.role)) {
    location.href = homeFor(session.user.role);
    return false;
  }
  const label = $('user-label');
  if (label) label.textContent = session.user.username;
  $('logout')?.addEventListener('click', async () => {
    await api('/api/logout', {});
    location.href = '/login';
  });
  await renderTeamNav();
  return true;
}


// Texto sem acentos e em minúsculas, para buscas.
export const searchKey = value =>
  String(value || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLocaleLowerCase();

const dateTime = (date, year) => new Intl.DateTimeFormat('pt-BR', {
  day: '2-digit', month: '2-digit', year, hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
}).format(date).replace(',', ' às');

// "há 5 minutos" / "há 2 horas" / data curta, com a data completa como título.
export function movementTime(value) {
  if (!value) return {label: '—', title: ''};
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return {label: '—', title: ''};
  const minutes = Math.max(0, Math.floor((Date.now() - date.getTime()) / 60000));
  const title = dateTime(date, 'numeric');
  if (minutes < 60) return {label: `há ${minutes} ${minutes === 1 ? 'minuto' : 'minutos'}`, title};
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return {label: `há ${hours} ${hours === 1 ? 'hora' : 'horas'}`, title};
  return {label: dateTime(date, '2-digit'), title};
}

export const clockTime = value =>
  (value ? new Date(value) : new Date()).toLocaleTimeString('pt-BR', {hour: '2-digit', minute: '2-digit', second: '2-digit'});
