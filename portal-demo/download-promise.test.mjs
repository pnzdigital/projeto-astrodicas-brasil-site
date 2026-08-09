// Trava a promessa de "Baixar PDF" contra o que o backend de fato gera.
// Horóscopo diário e previsão semanal são conteúdo efêmero — o backend
// (api/app/main.py) recusa gerar PDF de leitura sem seções fixas para eles.
// Qualquer conteúdo novo em WEB_CONTENT precisa declarar `downloadable`
// explicitamente (o factory content() quebra sem isso), então este teste
// só precisa fixar os casos que já sabemos que devem ser false.
//
// Roda com: ``node portal-demo/download-promise.test.mjs``.

import assert from 'node:assert/strict';
import test from 'node:test';
import { CONTENT, WEB_CONTENT } from './portal-config.js';

const byId = (id) => WEB_CONTENT.find((item) => item.id === id);

test('horóscopo diário não promete download — conteúdo muda todo dia', () => {
  assert.equal(byId(CONTENT.HOROSCOPE_DAILY).downloadable, false);
});

test('previsão semanal não promete download — conteúdo muda toda semana', () => {
  assert.equal(byId(CONTENT.WEEKLY_FORECAST).downloadable, false);
});

test('bônus do Completo (calendário lunar, retrógrados, manual do ascendente) mantêm download', () => {
  for (const id of [CONTENT.LUNAR_CALENDAR, CONTENT.RETROGRADES_GUIDE, CONTENT.ASCENDANT_MANUAL]) {
    assert.equal(byId(id).downloadable, true, `${id} deveria manter downloadable:true`);
  }
});

test('todo item de WEB_CONTENT declara downloadable como booleano explícito', () => {
  for (const item of WEB_CONTENT) {
    assert.equal(typeof item.downloadable, 'boolean', `${item.id} sem downloadable explícito`);
  }
});
