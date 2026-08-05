(function (window, document) {
  'use strict';

  var PIXEL_ID = '2812114902493745';
  var locale = window.location.pathname === '/es' || window.location.pathname.indexOf('/es/') === 0
    ? 'es-AR'
    : 'pt-BR';

  if (window.fbq) return;

  !function(f,b,e,v,n,t,s)
  {if(f.fbq)return;n=f.fbq=function(){n.callMethod?
  n.callMethod.apply(n,arguments):n.queue.push(arguments)};
  if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';
  n.queue=[];t=b.createElement(e);t.async=!0;
  t.src=v;s=b.getElementsByTagName(e)[0];
  s.parentNode.insertBefore(t,s)}(window,document,'script',
  'https://connect.facebook.net/en_US/fbevents.js');

  window.fbq('init', PIXEL_ID);
  window.fbq('track', 'PageView', { content_language: locale });
})(window, document);
