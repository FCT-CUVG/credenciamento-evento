// Pré-check-in do participante (página pública, em inglês ou português).
import {$, api, eventInfo, loadEventConfig, message} from './common.js';

const translations = {
  en: {
    brand: 'Check-in',
    heroTitle: 'HAVE YOU ARRIVED?',
    heroIntro: "Let the team know you've arrived. Then collect your badge and kit at the check-in desk.",
    searchTitle: 'FIND YOUR REGISTRATION',
    searchIntro: "Use the CPF from your registration or, if you don't have one, your e-mail.",
    cpfLabel: 'CPF',
    cpfPlaceholder: '000.000.000-00',
    emailLabel: 'E-mail',
    emailPlaceholder: 'name@example.com',
    useEmail: 'No CPF? Use your e-mail',
    useCpf: 'Use your CPF instead',
    searchButton: 'Pre-check-in',
    doneEyebrow: 'PRE-CHECK-IN COMPLETE',
    doneTitle: 'ARRIVAL CONFIRMED',
    doneIntro: 'Collect your badge and kit at this desk. Ask a volunteer if you need directions.',
    deskLabel: 'Go to desk',
    doneRetry: 'Find another registration',
    helpTitle: "Can't find your registration?",
    helpText: 'Check your CPF or e-mail and try again. If you need help, ask at the check-in desk or speak to a volunteer.',
    pendingTitle: 'PLEASE FIND A VOLUNTEER',
    pendingIntro: 'Your arrival has been recorded, but your registration has a pending item. Please find a volunteer for guidance before collecting your badge and kit.',
    notFoundTitle: 'REGISTRATION NOT FOUND',
    notFoundText: 'Check the CPF or e-mail and search again. If you still can\'t find it, please find a volunteer or go to the check-in desk.',
    ambiguousText: 'More than one registration uses this e-mail. Search with your CPF or find a volunteer for help.',
    notFoundRetry: 'Search again',
    closedTitle: 'PRE-CHECK-IN NOT OPEN YET',
    closedText: 'Online pre-check-in opens when the check-in desks start. Please come back later or find a volunteer.',
    closedRetry: 'Try again'
  },
  'pt-BR': {
    brand: 'Credenciamento',
    heroTitle: 'VOCÊ CHEGOU?',
    heroIntro: 'Avise à equipe que você chegou. Depois, retire seu crachá e kit no atendimento.',
    searchTitle: 'LOCALIZE SUA INSCRIÇÃO',
    searchIntro: 'Use o CPF informado na sua inscrição ou, se não tiver CPF, o e-mail.',
    cpfLabel: 'CPF',
    cpfPlaceholder: '000.000.000-00',
    emailLabel: 'E-mail',
    emailPlaceholder: 'nome@exemplo.com',
    useEmail: 'Não tem CPF? Use seu e-mail',
    useCpf: 'Usar o CPF',
    searchButton: 'Realizar pré-check-in',
    doneEyebrow: 'PRÉ-CHECK-IN CONCLUÍDO',
    doneTitle: 'PRESENÇA CONFIRMADA',
    doneIntro: 'Retire seu crachá e kit neste guichê. Se não souber para onde ir, peça orientação a um voluntário.',
    deskLabel: 'Dirija-se ao guichê',
    doneRetry: 'Consultar outra inscrição',
    helpTitle: 'Não encontrou sua inscrição?',
    helpText: 'Confira o CPF ou o e-mail e tente novamente. Se precisar de ajuda, procure o atendimento ou um voluntário.',
    pendingTitle: 'PROCURE UM VOLUNTÁRIO',
    pendingIntro: 'Sua chegada foi registrada, mas há uma pendência na sua inscrição. Procure um voluntário para receber orientações antes de retirar seu crachá e kit.',
    notFoundTitle: 'INSCRIÇÃO NÃO ENCONTRADA',
    notFoundText: 'Confira o CPF ou o e-mail e busque novamente. Se ainda assim não encontrar, procure um voluntário ou o atendimento.',
    ambiguousText: 'Há mais de uma inscrição com este e-mail. Busque pelo CPF ou procure um voluntário.',
    notFoundRetry: 'Buscar novamente',
    closedTitle: 'PRÉ-CHECK-IN AINDA NÃO ABERTO',
    closedText: 'O pré-check-in pelo celular abre quando o credenciamento começar. Volte mais tarde ou procure um voluntário.',
    closedRetry: 'Tentar novamente'
  }
};

// O resultado do pré-check-in fica no navegador da pessoa para sobreviver a um F5.
const store = {
  get(key) { try { return JSON.parse(localStorage.getItem('checkin:' + key)); } catch { return null; } },
  set(key, value) { try { localStorage.setItem('checkin:' + key, JSON.stringify(value)); } catch { /* sem armazenamento */ } },
  remove(key) { try { localStorage.removeItem('checkin:' + key); } catch { /* sem armazenamento */ } }
};
const RESULT_TTL = 12 * 60 * 60 * 1000;

let language = ['en', 'pt-BR'].includes(store.get('language')) ? store.get('language') : 'en';
let desk = null, errorState = null, notFound = null, useEmail = false;

function show(id) {
  for (const key of ['public-search', 'public-done', 'public-pending', 'public-notfound', 'public-closed']) {
    $(key).classList.toggle('hidden', key !== id);
  }
}

function renderDesk() {
  if (!desk) return;
  const pt = language === 'pt-BR';
  const ranges = (desk.ranges || []).map(({from, to}) => from === to ? from : `${from}–${to}`);
  const plural = ranges.length > 1 || ranges[0]?.length > 1;
  const letters = pt ? (plural ? 'Letras ' : 'Letra ') : (plural ? 'Letters ' : 'Letter ');
  $('done-desk-number').textContent = desk.guiche;
  $('done-desk-range').textContent = desk.priority ? (pt ? 'Atendimento prioritário' : 'Priority service')
    : ranges.length ? letters + ranges.join(', ') : '';
  $('done-desk-range').classList.toggle('hidden', !desk.priority && !ranges.length);
}

// Busca por CPF ou, para quem não tem CPF, por e-mail.
function renderLookupMethod() {
  const form = $('lookup-form');
  const cpfInput = form.elements.namedItem('cpf'), emailInput = form.elements.namedItem('email');
  $('cpf-field').classList.toggle('hidden', useEmail);
  $('email-field').classList.toggle('hidden', !useEmail);
  cpfInput.required = !useEmail;
  emailInput.required = useEmail;
  $('lookup-alternative').textContent = translations[language][useEmail ? 'useCpf' : 'useEmail'];
}

function renderNotFound() {
  if (notFound) $('notfound-text').textContent = translations[language][notFound === 409 ? 'ambiguousText' : 'notFoundText'];
}

function errorText(err) {
  if (language === 'pt-BR') return err.message;
  if (err.status === 429) return 'Too many attempts. Please wait a few minutes and try again.';
  if (err.status === 400) return useEmail ? 'Enter the e-mail used in your registration.' : 'Enter a valid CPF with 11 digits.';
  return 'Something went wrong. Please try again.';
}

function showError(err) {
  errorState = {err};
  message('lookup-error', errorText(err));
}

function setLanguage(value) {
  language = value;
  const english = language === 'en';
  document.documentElement.lang = language;
  document.title = (english ? 'Check-in | ' : 'Pré-check-in | ') + eventInfo.name;
  document.querySelector('.site-brand').setAttribute(
    'aria-label', eventInfo.name + (english ? ' — check-in home' : ' — início do credenciamento'));
  for (const node of document.querySelectorAll('[data-i18n]')) {
    const key = node.dataset.i18n;
    const hint = key === 'searchIntro' && eventInfo.registrationHints[language];
    node.textContent = hint || translations[language][key];
  }
  for (const node of document.querySelectorAll('[data-i18n-placeholder]')) {
    node.placeholder = translations[language][node.dataset.i18nPlaceholder];
  }
  $('language-switch').textContent = english ? 'Português (BR)' : 'English';
  if (errorState) message('lookup-error', errorText(errorState.err));
  renderLookupMethod();
  renderDesk();
  renderNotFound();
}

function showResult(result) {
  if (result.needs_guidance) {
    desk = null;
    $('pending-name').textContent = result.name;
    show('public-pending');
    return;
  }
  $('done-name').textContent = result.name;
  desk = {guiche: result.guiche, ranges: result.guiche_ranges, priority: result.guiche_priority};
  renderDesk();
  show('public-done');
}

function init() {
  const form = $('lookup-form');
  const cpfInput = form.elements.namedItem('cpf');
  const emailInput = form.elements.namedItem('email');

  $('lookup-alternative').addEventListener('click', () => {
    useEmail = !useEmail;
    errorState = null;
    message('lookup-error', '');
    renderLookupMethod();
    (useEmail ? emailInput : cpfInput).focus();
  });
  $('language-switch').addEventListener('click', () => {
    setLanguage(language === 'en' ? 'pt-BR' : 'en');
    store.set('language', language);
  });

  // Um passo só: encontrou, registra a chegada e mostra o guichê (ou pede orientação).
  form.addEventListener('submit', async event => {
    event.preventDefault();
    errorState = null;
    message('lookup-error', '');
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const data = await api('/api/checkin', useEmail ? {email: emailInput.value.trim()} : {cpf: cpfInput.value.trim()});
      const result = {
        name: data.name, needs_guidance: data.needs_guidance, guiche: data.guiche,
        guiche_ranges: data.guiche_ranges, guiche_priority: data.guiche_priority, saved_at: Date.now()
      };
      store.set('result', result);
      showResult(result);
    } catch (err) {
      if (err.status === 403 && err.data?.closed) {
        show('public-closed');
      } else if (err.status === 404 || err.status === 409) {
        notFound = err.status;
        renderNotFound();
        show('public-notfound');
        $('notfound-retry').focus();
      } else {
        showError(err);
      }
    } finally {
      button.disabled = false;
    }
  });

  // Nova busca mantém o método escolhido (CPF ou e-mail) e o que foi digitado, para corrigir.
  $('notfound-retry').addEventListener('click', () => {
    notFound = null;
    show('public-search');
    (useEmail ? emailInput : cpfInput).focus();
  });

  const retry = () => {
    store.remove('result');
    form.reset();
    useEmail = false;
    renderLookupMethod();
    desk = null;
    notFound = null;
    errorState = null;
    message('lookup-error', '');
    show('public-search');
    cpfInput.focus();
  };
  for (const id of ['done-retry', 'pending-retry']) $(id).addEventListener('click', retry);
  // Fechado: tenta de novo consultando se a coordenação já abriu o pré-check-in.
  $('closed-retry').addEventListener('click', async () => {
    await loadEventConfig();
    if (eventInfo.checkinOpen) retry();
  });

  const saved = store.get('result');
  if (saved && typeof saved.name === 'string' && Date.now() - saved.saved_at < RESULT_TTL) showResult(saved);
  else {
    store.remove('result');
    if (!eventInfo.checkinOpen) show('public-closed');
  }
}

await loadEventConfig();
setLanguage(language);
init();
