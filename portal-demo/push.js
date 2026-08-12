/**
 * AstroDicas — PWA Install + Web Push
 *
 * Expõe em window.AstroPush:
 *   init(apiBase, showToastFn)  — registra SW, ouve BroadcastChannel
 *   promptInstall()             — dispara o banner de instalação (só após gesto)
 *   requestPushPermission()     — pede permissão e registra subscription (só após gesto)
 *   unsubscribePush()           — cancela subscription
 *   isPushGranted()             — true se já tem permissão concedida
 *   isIos()                     — true se iOS (sem beforeinstallprompt)
 *   showIosInstallHint(el)      — mostra instrução manual para iOS
 *
 * Regras de UX (hard):
 *   - Permissão de notificação SÓ após gesto do usuário (nunca no load).
 *   - beforeinstallprompt salvo e disparado só quando chamado explicitamente.
 *   - iOS: instrução textual, sem botão fake.
 */

(function () {
  'use strict';

  let _apiBase = '';
  let _showToast = () => {};
  let _deferredInstallPrompt = null;
  let _swRegistration = null;
  let _pushChannel = null;

  // -------------------------------------------------------------------------
  // Detecção de plataforma
  // -------------------------------------------------------------------------

  function isIos() {
    return /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
  }

  function isInStandaloneMode() {
    return window.matchMedia('(display-mode: standalone)').matches ||
           navigator.standalone === true;
  }

  // -------------------------------------------------------------------------
  // Helpers VAPID
  // -------------------------------------------------------------------------

  function _urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(base64);
    return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
  }

  // -------------------------------------------------------------------------
  // Service Worker
  // -------------------------------------------------------------------------

  async function _registerSW() {
    if (!('serviceWorker' in navigator)) return null;
    try {
      _swRegistration = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
      return _swRegistration;
    } catch (err) {
      console.warn('[AstroPush] SW registration failed:', err);
      return null;
    }
  }

  // -------------------------------------------------------------------------
  // BroadcastChannel: in-app toast quando aba está aberta
  // -------------------------------------------------------------------------

  function _listenPushChannel() {
    if (!('BroadcastChannel' in window)) return;
    _pushChannel = new BroadcastChannel('astrodicas-push');
    _pushChannel.onmessage = (event) => {
      const { title, body } = event.data || {};
      if (title || body) {
        _showToast(`${title}${body ? ' — ' + body : ''}`);
      }
    };
  }

  // -------------------------------------------------------------------------
  // Push subscription
  // -------------------------------------------------------------------------

  async function _getVapidPublicKey() {
    const r = await fetch(`${_apiBase}/api/me/push/vapid-public-key`);
    if (!r.ok) throw new Error('Falha ao obter chave VAPID');
    const { publicKey } = await r.json();
    return publicKey;
  }

  async function _subscribe() {
    if (!_swRegistration) throw new Error('Service Worker não registrado');
    const publicKey = await _getVapidPublicKey();
    const sub = await _swRegistration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: _urlBase64ToUint8Array(publicKey),
    });
    const json = sub.toJSON();
    const locale = document.documentElement.lang || navigator.language || 'pt-BR';

    await fetch(`${_apiBase}/api/me/push/subscribe`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        endpoint: json.endpoint,
        p256dh: json.keys.p256dh,
        auth: json.keys.auth,
        locale,
      }),
    });
    return sub;
  }

  async function _unsubscribe() {
    if (!_swRegistration) return;
    const sub = await _swRegistration.pushManager.getSubscription();
    if (!sub) return;
    await fetch(`${_apiBase}/api/me/push/unsubscribe`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ endpoint: sub.endpoint }),
    });
    await sub.unsubscribe();
  }

  // -------------------------------------------------------------------------
  // Instalação
  // -------------------------------------------------------------------------

  function _captureInstallPrompt() {
    window.addEventListener('beforeinstallprompt', (e) => {
      e.preventDefault();
      _deferredInstallPrompt = e;
    });
  }

  async function promptInstall() {
    if (!_deferredInstallPrompt) return false;
    _deferredInstallPrompt.prompt();
    const { outcome } = await _deferredInstallPrompt.userChoice;
    _deferredInstallPrompt = null;
    return outcome === 'accepted';
  }

  function canPromptInstall() {
    return !!_deferredInstallPrompt;
  }

  function showIosInstallHint(container) {
    if (!container) return;
    const lang = document.documentElement.lang || 'pt-BR';
    const isAr = lang === 'es-AR' || lang.startsWith('es');
    container.innerHTML = isAr
      ? `Para instalar: toca <strong>Compartir</strong> (cuadrado con flecha ↑) y luego <strong>"Agregar a pantalla de inicio"</strong>.`
      : `Para instalar: toque <strong>Compartilhar</strong> (quadrado com seta ↑) e depois <strong>"Adicionar à Tela de Início"</strong>.`;
    container.hidden = false;
  }

  // -------------------------------------------------------------------------
  // API pública
  // -------------------------------------------------------------------------

  async function init(apiBase, showToastFn) {
    _apiBase = (apiBase || '').replace(/\/$/, '');
    _showToast = showToastFn || (() => {});
    _captureInstallPrompt();
    await _registerSW();
    _listenPushChannel();
  }

  function isPushGranted() {
    return 'Notification' in window && Notification.permission === 'granted';
  }

  async function requestPushPermission() {
    if (!('Notification' in window)) return false;
    if (!('PushManager' in window)) return false;
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') return false;
    await _subscribe();
    return true;
  }

  async function unsubscribePush() {
    await _unsubscribe();
  }

  window.AstroPush = {
    init,
    promptInstall,
    canPromptInstall,
    requestPushPermission,
    unsubscribePush,
    isPushGranted,
    isIos,
    isInStandaloneMode,
    showIosInstallHint,
  };
})();
