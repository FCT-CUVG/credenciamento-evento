// Pré-check-in do participante (página pública, em inglês ou português).
import {$, api, eventInfo, loadEventConfig, message} from './common.js';

const translations = {
  en: {
    brand: 'Check-in',
    heroTitle: 'HAVE YOU ARRIVED?',
    heroIntro: "Let the team know you've arrived. Then collect your badge and kit at the check-in desk.",
    searchTitle: 'FIND YOUR REGISTRATION',
    searchIntro: 'Use the CPF from your registration.',
    cpfLabel: 'CPF',
    cpfPlaceholder: '000.000.000-00',
    searchButton: 'Find registration',
    foundEyebrow: 'REGISTRATION FOUND',
    confirmIntro: 'Check your details. Confirm your arrival so the team can prepare your badge and kit.',
    confirmButton: 'Confirm my arrival',
    noAffiliation: 'No affiliation provided',
    retryButton: 'Change search',
    doneEyebrow: 'PRE-CHECK-IN COMPLETE',
    doneTitle: 'ARRIVAL CONFIRMED',
    doneIntro: 'Collect your badge and kit at this desk. Ask a volunteer if you need directions.',
    deskLabel: 'Go to desk',
    doneRetry: 'Find another registration',
    helpTitle: "Can't find your registration?",
    helpText: 'Check the CPF and try again. If you need help, ask at the check-in desk or speak to a volunteer.',
    pendingEyebrow: 'ARRIVAL RECORDED',
    pendingTitle: 'PLEASE FIND A VOLUNTEER',
    pendingIntro: 'Your arrival has been recorded. Please find a volunteer for guidance before collecting your badge and kit.'
  },
  'pt-BR': {
    brand: 'Credenciamento',
    heroTitle: 'VOCÊ CHEGOU?',
    heroIntro: 'Avise à equipe que você chegou. Depois, retire seu crachá e kit no atendimento.',
    searchTitle: 'LOCALIZE SUA INSCRIÇÃO',
    searchIntro: 'Use o CPF informado na sua inscrição.',
    cpfLabel: 'CPF',
    cpfPlaceholder: '000.000.000-00',
    searchButton: 'Buscar inscrição',
    foundEyebrow: 'INSCRIÇÃO LOCALIZADA',
    confirmIntro: 'Confira seus dados. Ao confirmar, a equipe saberá que você chegou e poderá preparar seu crachá e kit.',
    confirmButton: 'Confirmar minha chegada',
    noAffiliation: 'Afiliação não informada',
    retryButton: 'Corrigir a busca',
    doneEyebrow: 'PRÉ-CHECK-IN CONCLUÍDO',
    doneTitle: 'PRESENÇA CONFIRMADA',
    doneIntro: 'Retire seu crachá e kit neste guichê. Se não souber para onde ir, peça orientação a um voluntário.',
    deskLabel: 'Dirija-se ao guichê',
    doneRetry: 'Consultar outra inscrição',
    helpTitle: 'Não encontrou sua inscrição?',
    helpText: 'Confira o CPF e tente novamente. Se precisar de ajuda, procure o atendimento ou um voluntário.',
    pendingEyebrow: 'CHEGADA REGISTRADA',
    pendingTitle: 'PROCURE UM VOLUNTÁRIO',
    pendingIntro: 'Sua chegada foi registrada. Procure um voluntário para receber orientações antes de retirar seu crachá e kit.'
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
let lookupToken = null, affiliation = null, desk = null, errorState = null;

function show(id) {
  for (const key of ['public-search', 'public-confirm', 'public-done', 'public-pending']) {
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

function renderAffiliation() {
  if (affiliation === null) return;
  const node = $('found-affiliation');
  node.textContent = affiliation || translations[language].noAffiliation;
  node.classList.toggle('affiliation-missing', !affiliation);
}

function errorText(err, action) {
  if (language === 'pt-BR') return err.message;
  if (err.status === 429) return 'Too many attempts. Please wait a minute and try again.';
  if (action === 'lookup' && err.status === 400) return 'Enter a valid CPF with 11 digits.';
  if (action === 'lookup' && err.status === 404) return 'Registration not found. Check the CPF or ask the team for help.';
  if (action === 'precheck' && err.status === 400) return 'This search has expired. Please try again.';
  if (action === 'precheck' && err.status === 404) return 'Registration not found. Please search again.';
  return 'Something went wrong. Please try again.';
}

function showError(id, err, action) {
  errorState = {id, err, action};
  message(id, errorText(err, action));
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
  if (errorState) message(errorState.id, errorText(errorState.err, errorState.action));
  renderDesk();
  renderAffiliation();
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
  const nameInput = form.elements.namedItem('name');
  const emailInput = form.elements.namedItem('email');
  const alternativeButton = $('lookup-alternative');
  const alternativeFields = $('lookup-alternative-fields');

  const renderAlternative = () => {
    const pt = language === 'pt-BR';
    alternativeButton.textContent = pt ? 'ou use seu nome completo e e-mail' : 'or use your full name and e-mail';
    $('lookup-name-label').textContent = pt ? 'Nome completo' : 'Full name';
    $('lookup-email-label').textContent = 'E-mail';
  };
  const resetLookupMethod = () => {
    alternativeFields.classList.add('hidden');
    cpfInput.required = true;
    nameInput.required = false;
    emailInput.required = false;
  };

  alternativeButton.addEventListener('click', () => {
    alternativeFields.classList.remove('hidden');
    cpfInput.required = false;
    nameInput.required = true;
    emailInput.required = true;
    cpfInput.value = '';
    nameInput.focus();
  });
  $('language-switch').addEventListener('click', () => {
    setLanguage(language === 'en' ? 'pt-BR' : 'en');
    store.set('language', language);
    renderAlternative();
  });
  renderAlternative();

  form.addEventListener('submit', async event => {
    event.preventDefault();
    errorState = null;
    message('lookup-error', '');
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const data = await api('/api/lookup', {
        cpf: cpfInput.value.trim(), name: nameInput.value.trim(), email: emailInput.value.trim()
      });
      lookupToken = data.token || null;
      $('found-name').textContent = data.name;
      affiliation = data.affiliation || '';
      renderAffiliation();
      show('public-confirm');
    } catch (err) {
      showError('lookup-error', err, 'lookup');
    } finally {
      button.disabled = false;
    }
  });

  $('precheck-button').addEventListener('click', async () => {
    const button = $('precheck-button');
    button.disabled = true;
    errorState = null;
    message('confirm-error', '');
    try {
      const data = await api('/api/precheck', {token: lookupToken});
      const result = {
        name: data.name, needs_guidance: data.needs_guidance, guiche: data.guiche,
        guiche_ranges: data.guiche_ranges, guiche_priority: data.guiche_priority, saved_at: Date.now()
      };
      store.set('result', result);
      showResult(result);
    } catch (err) {
      showError('confirm-error', err, 'precheck');
    } finally {
      button.disabled = false;
    }
  });

  const retry = () => {
    store.remove('result');
    form.reset();
    resetLookupMethod();
    lookupToken = null;
    desk = null;
    errorState = null;
    message('lookup-error', '');
    message('confirm-error', '');
    show('public-search');
    cpfInput.focus();
  };
  for (const id of ['retry-button', 'done-retry', 'pending-retry']) $(id).addEventListener('click', retry);

  const saved = store.get('result');
  if (saved && typeof saved.name === 'string' && Date.now() - saved.saved_at < RESULT_TTL) showResult(saved);
  else store.remove('result');
}

await loadEventConfig();
setLanguage(language);
init();
