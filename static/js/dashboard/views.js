// Telas do admin: a lista de participantes (/admin) e uma tela por configuração (/admin/guiches...).
// Trocar de tela pelos links não recarrega a página: o resultado da consolidação e o que está em
// edição continuam. Ao abrir uma tela, avisa com o evento "admin:view" (detail: nome da tela ou '').
import {$, markCurrentPage} from '../common.js';

const sections = () => [...document.querySelectorAll('[data-section]')];
const isView = path => path === '/admin' || sections().some(section => '/admin/' + section.dataset.section === path);
let baseTitle = '';

function render(focus) {
  const current = sections().find(section => '/admin/' + section.dataset.section === location.pathname);
  // Endereço desconhecido (/admin/xyz) mostra a lista, com o endereço certo.
  if (!current && location.pathname !== '/admin') history.replaceState(null, '', '/admin' + location.hash);
  $('admin-overview').hidden = Boolean(current);
  $('admin-config').hidden = !current;
  for (const section of sections()) section.hidden = section !== current;
  const heading = current ? current.querySelector('h2') : document.querySelector('#admin-overview h1');
  document.title = current ? `${heading.textContent} · ${baseTitle}` : baseTitle;
  markCurrentPage();
  if (focus) {
    scrollTo(0, 0);
    heading.focus({preventScroll: true});
  }
  document.dispatchEvent(new CustomEvent('admin:view', {detail: current?.dataset.section || ''}));
}

// focus: leva ao topo e põe o foco no título da tela (como numa página nova).
export function showView(path, focus = true) {
  if (location.pathname !== path) history.pushState(null, '', path);
  render(focus);
}

export function initViews() {
  baseTitle = document.title;
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href^="/admin"]');
    // Ctrl/⌘ ou o botão do meio continuam abrindo em outra aba.
    if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const path = link.getAttribute('href');
    if (!isView(path)) return;
    event.preventDefault();
    showView(path);
  });
  addEventListener('popstate', () => render(false));
  render(false);
}
