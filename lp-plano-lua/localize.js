(function () {
  'use strict';

  var script = document.currentScript;
  var page = script && script.dataset.page || (location.pathname.includes('oferta-lua-2') ? 'v2' : 'v1');
  var argentina = location.pathname.startsWith('/es/');
  if (!argentina) return;

  document.documentElement.lang = 'es-AR';

  var common = [
    ['Plano Lua — seu horóscopo, não o do seu signo', 'Plan Luna — tu horóscopo, no el de tu signo'],
    ['Calculado no seu mapa natal, todo dia. Mapa Astral Completo de brinde.', 'Calculado sobre tu carta natal, todos los días. Carta Astral Completa de regalo.'],
    ['Horóscopo diário calculado no SEU mapa astral. Mapa Astral Completo de brinde.', 'Horóscopo diario calculado sobre TU carta natal. Carta Astral Completa de regalo.'],
    ['Astrologia calculada, não chutada', 'Astrología calculada, no improvisada'],
    ['Astrologia calculada', 'Astrología calculada'],
    ['Mapa Astral Completo', 'Carta Astral Completa'],
    ['Mapa Astral', 'Carta Astral'],
    ['Plano Lua Premium', 'Plan Luna Premium'],
    ['Plano Lua', 'Plan Luna'],
    ['Mapa do Amor', 'Mapa del Amor'],
    ['Mapa da Prosperidade', 'Mapa de la Prosperidad'],
    ['Calendário Lunar', 'Calendario Lunar'],
    ['Guia dos Retrógrados', 'Guía de los Retrógrados'],
    ['Manual do Seu Ascendente', 'Manual de tu Ascendente'],
    ['Guia do Mês', 'Guía del Mes'],
    ['Condição de lançamento', 'Condición de lanzamiento'],
    ['Acesso imediato', 'Acceso inmediato'],
    ['Cancela quando quiser', 'Cancelá cuando quieras'],
    ['7 dias de garantia', '7 días de garantía'],
    ['Pagamento único', 'Pago único'],
    ['pagamento único', 'pago único'],
    ['Sem recorrência', 'Sin recurrencia'],
    ['Sem assinatura', 'Sin suscripción'],
    ['Dúvidas', 'Dudas'],
    ['Perguntas frequentes', 'Preguntas frecuentes'],
    ['Termos', 'Términos'],
    ['Privacidade', 'Privacidad'],
    ['Suporte', 'Soporte'],
    ['Entrega no', 'Entrega en el'],
    ['na 1ª assinatura', 'con la primera suscripción'],
    ['mapa de regalo', 'carta de regalo'],
    ['em PDF', 'en PDF'],
    ['e o ', 'y la '],
    ['pra sempre', 'para siempre'],
    ['site', 'sitio'],
    ['R$', 'ARS '],
    ['/mês', '/mes'],
    ['por mês', 'por mes'],
    ['de brinde', 'de regalo'],
    ['brinde', 'regalo'],
    ['Brinde', 'Regalo'],
    ['assinante há', 'suscriptora desde hace'],
    ['há 5 meses', 'hace 5 meses'],
    ['há 8 meses', 'hace 8 meses'],
    ['há 3 meses', 'hace 3 meses'],
    ['há 2 meses', 'hace 2 meses'],
    ['há 6 meses', 'hace 6 meses'],
    ['há 4 meses', 'hace 4 meses']
  ];

  var v1 = [
    ['Plano Lua — seu horóscopo calculado no seu mapa astral | AstroDicas', 'Plan Luna — tu horóscopo calculado sobre tu carta natal | AstroDicas'],
    ['Horóscopo diário calculado em cima do SEU mapa astral, entregue no site e no seu e-mail. Mapa Astral Completo de brinde na primeira assinatura.', 'Horóscopo diario calculado sobre TU carta natal, disponible en el sitio y en tu e-mail. Carta Astral Completa de regalo con la primera suscripción.'],
    ['O horóscopo que você', 'El horóscopo que leés'],
    ['lê por aí foi escrito', 'por ahí fue escrito'],
    ['uma vez e copiado pra', 'una vez y copiado para'],
    ['200 milhões de pessoas.', '200 millones de personas.'],
    ['O seu não.', 'El tuyo no.'],
    ['Todo dia de manhã eu leio os trânsitos daquele dia em cima do seu mapa natal — calculado com a data, a hora e a cidade exatas do seu nascimento — e escrevo uma leitura nova, só sua. Mais a previsão da sua semana e o aviso de cada lua e retrógrado antes de eles chegarem.', 'Cada mañana leo los tránsitos del día sobre tu carta natal — calculada con la fecha, la hora y la ciudad exactas de tu nacimiento — y preparo una lectura nueva, solamente tuya. También recibís la previsión de tu semana y el aviso de cada luna y retrógrado antes de que lleguen.'],
    ['E o seu Mapa Astral Completo entra de brinde na primeira assinatura.', 'Y tu Carta Astral Completa viene de regalo con la primera suscripción.'],
    ['Todo dia de manhã eu leio os trânsitos daquele dia em cima do seu mapa natal —', 'Cada mañana leo los tránsitos del día sobre tu carta natal —'],
    ['calculado com a data, a hora e a cidade exatas do seu nascimento — e escrevo uma', 'calculada con la fecha, la hora y la ciudad exactas de tu nacimiento — y preparo una'],
    ['leitura nova, só sua. Mais a previsão da sua semana e o aviso de cada lua e', 'lectura nueva, solamente tuya. También recibís la previsión de tu semana y el aviso de cada luna y'],
    ['retrógrado antes de eles chegarem.', 'retrógrado antes de que lleguen.'],
    ['Quero meu horóscopo personalizado', 'Quiero mi horóscopo personalizado'],
    ['Mapa Astral Completo de brinde · acesso imediato', 'Carta Astral Completa de regalo · acceso inmediato'],
    ['Cancela quando quiser', 'Cancelá cuando quieras'],
    ['Sem app pra baixar', 'Sin aplicaciones para descargar'],
    ['calculado, não chutado', 'calculado, no improvisado'],
    ['★ de avaliação', '★ de valoración'],
    ['+1.469 mapas calculados', '+1.469 cartas calculadas'],
    ['+1.000', '+1.469'],
    ['e no seu', 'y en tu'],
    ['A diferença', 'La diferencia'],
    ['Um é pra 200 milhões.', 'Uno es para 200 millones.'],
    ['O outro é', 'El otro es'],
    ['só seu', 'solo tuyo'],
    ['Todo signo tem cerca de 700 milhões de pessoas no mundo. Nenhum texto consegue falar com todas elas ao mesmo tempo — e é exatamente por isso que horóscopo genérico nunca acerta nada na sua vida.', 'Cada signo reúne cerca de 700 millones de personas en el mundo. Ningún texto puede hablarle a todas al mismo tiempo, y por eso un horóscopo genérico nunca alcanza a describir tu vida.'],
    ['Horóscopo de revista', 'Horóscopo de revista'],
    ['Escrito pro seu signo', 'Escrito para tu signo'],
    ['Usa só o Sol — ignora Lua, Ascendente e as 12 casas', 'Usa solamente el Sol: ignora la Luna, el Ascendente y las 12 casas'],
    ['Mesmo texto pra todo mundo que nasceu no mesmo mês', 'El mismo texto para todos los que nacieron el mismo mes'],
    ['Não sabe a hora nem a cidade do seu nascimento', 'No conoce la hora ni la ciudad de tu nacimiento'],
    ['Frases vagas que servem pra qualquer pessoa', 'Frases vagas que podrían servirle a cualquiera'],
    ['Nunca te avisa de um trânsito antes dele bater', 'Nunca te avisa de un tránsito antes de que se sienta'],
    ['Escrito pro seu nascimento', 'Escrito para tu nacimiento'],
    ['Mapa natal completo: Sol, Lua, Ascendente, casas e aspectos', 'Carta natal completa: Sol, Luna, Ascendente, casas y aspectos'],
    ['Cada dia é recalculado com os trânsitos reais sobre o SEU mapa', 'Cada día se recalcula con los tránsitos reales sobre TU carta'],
    ['Data, hora e cidade exatas entram no cálculo', 'La fecha, la hora y la ciudad exactas entran en el cálculo'],
    ['Português claro — sem jargão de astrólogo', 'Español claro, sin jerga astrológica'],
    ['Te avisa antes: retrógrados, lunações, trânsitos pesados', 'Te avisa antes: retrógrados, lunaciones y tránsitos intensos'],
    ['O que você recebe', 'Lo que recibís'],
    ['Um horóscopo por dia —', 'Un horóscopo por día —'],
    ['pra começar.', 'para empezar.'],
    ['Seu horóscopo, todo dia', 'Tu horóscopo, todos los días'],
    ['O produto principal. Toda manhã, uma leitura nova — trânsitos do dia cruzados com o seu mapa natal, em português claro. Chega no seu e-mail e fica salva na sua área. Nunca é texto repetido.', 'El producto principal. Cada mañana recibís una lectura nueva: los tránsitos del día cruzados con tu carta natal, en español claro. Llega a tu e-mail y queda guardada en tu área. Nunca es texto repetido.'],
    ['Seu mapa natal interpretado em PDF: Sol, Lua, Ascendente, os 10 planetas, as 12 casas e os aspectos. É a base de todo horóscopo que você vai receber — e é o mesmo mapa que vendemos avulso.', 'Tu carta natal interpretada en PDF: Sol, Luna, Ascendente, los 10 planetas, las 12 casas y los aspectos. Es la base de cada horóscopo que vas a recibir y es la misma carta que vendemos por separado.'],
    ['Brinde da 1ª assinatura', 'Regalo de la primera suscripción'],
    ['Previsão da semana', 'Previsión de la semana'],
    ['Todo domingo, uma leitura maior: o que vem pela frente nos próximos sete dias no seu mapa — amor, dinheiro, trabalho e os dias que pedem cautela. Pra você entrar na semana sabendo onde pisa.', 'Cada domingo recibís una lectura más amplia sobre los próximos siete días de tu carta: amor, dinero, trabajo y los días que piden cautela. Para entrar en la semana sabiendo dónde pisás.'],
    ['Avisos de lua e trânsitos', 'Avisos de luna y tránsitos'],
    ['Lua nova, lua cheia, Mercúrio retrógrado. Você é avisada', 'Luna nueva, luna llena, Mercurio retrógrado. Te avisamos'],
    ['— e com a leitura de como aquilo bate especificamente no seu mapa, não no geral.', '— con una lectura de cómo impacta específicamente en tu carta, no de manera general.'],
    ['Sua área no site', 'Tu área en el sitio'],
    ['Um lugar seu: histórico de todos os horóscopos, seus mapas pra baixar quando quiser, e tudo que você comprar depois fica guardado lá. Sem app, sem senha decorada.', 'Un lugar propio: el historial de todos tus horóscopos, tus cartas para descargar cuando quieras y todo lo que compres después guardado ahí. Sin aplicación y sin contraseñas para memorizar.'],
    ['Como funciona', 'Cómo funciona'],
    ['Dois minutos pra configurar.', 'Dos minutos para configurarlo.'],
    ['Depois é automático.', 'Después es automático.'],
    ['Você assina', 'Te suscribís'],
    ['Pagamento em segundos por PIX ou cartão. Na hora, cai um e-mail com o acesso à sua área — sem senha pra inventar, é só clicar no link.', 'Pagás en segundos con Mercado Pago. En el momento recibís un e-mail con el acceso a tu área: sin inventar contraseñas, solamente hacés clic en el enlace.'],
    ['Manda seu nascimento', 'Completás tus datos de nacimiento'],
    ['Nome, data, hora e cidade. É isso. A hora importa porque é ela que define seu Ascendente e a posição das casas — a parte que o horóscopo de revista joga fora.', 'Nombre, fecha, hora y ciudad. Eso es todo. La hora importa porque define tu Ascendente y la posición de las casas, la parte que un horóscopo de revista deja afuera.'],
    ['Recebe todo dia', 'Recibís todos los días'],
    ['Seu Mapa Astral Completo sai na hora. E a partir do dia seguinte o horóscopo personalizado chega sozinho, todo dia, no e-mail e na sua área.', 'Tu Carta Astral Completa se genera en el momento. Desde el día siguiente, tu horóscopo personalizado llega automáticamente todos los días a tu e-mail y a tu área.'],
    ['Quem já recebe', 'Quienes ya lo reciben'],
    ['Parece que foi escrito', 'Parece escrito'],
    ['olhando pra minha vida.', 'mirando mi vida.'],
    ['Eu li o de hoje de manhã e travei. Falava exatamente da conversa que eu tive ontem no trabalho. Nunca tinha acontecido isso com horóscopo.', 'Leí el de esta mañana y me quedé helada. Hablaba exactamente de la conversación que tuve ayer en el trabajo. Nunca me había pasado con un horóscopo.'],
    ['O mapa completo sozinho já valeu o ano inteiro. Eu tinha pago quase duzentos reais num parecido e esse veio mais detalhado.', 'La carta completa por sí sola ya valió todo el año. Había pagado mucho más por una parecida y esta llegó con más detalle.'],
    ['Me avisou do retrógrado antes. Segurei a assinatura de um contrato por causa disso e no fim deu ruim mesmo pra quem assinou.', 'Me avisó del retrógrado antes. Esperé para firmar un contrato y al final realmente salió mal para quienes lo firmaron.'],
    ['Comece hoje pelo preço', 'Empezá hoy por el precio'],
    ['de um café por semana', 'de un café por semana'],
    ['um café por semana', 'un café por semana'],
    ['Horóscopo diário calculado no seu mapa natal', 'Horóscopo diario calculado sobre tu carta natal'],
    ['Previsão personalizada da semana, todo domingo', 'Previsión personalizada de la semana, cada domingo'],
    ['Avisos antecipados de lua nova, lua cheia e retrógrados', 'Avisos anticipados de luna nueva, luna llena y retrógrados'],
    ['Sua área no site com tudo salvo pra sempre', 'Tu área en el sitio con todo guardado para siempre'],
    ['Cancelamento em 1 clique, sem multa e sem ligação', 'Cancelación en un clic, sin multa ni llamadas'],
    ['Assinar o Plano Lua agora', 'Suscribirme a Plan Luna ahora'],
    ['Acesso liberado em segundos', 'Acceso habilitado en segundos'],
    ['Garantia de 7 dias.', 'Garantía de 7 días.'],
    ['Recebe o mapa, lê os horóscopos da semana. Se não parecer escrito pra você, devolvemos tudo — e o mapa fica com você mesmo assim.', 'Recibís la carta y leés los horóscopos de la semana. Si no parece escrito para vos, te devolvemos todo; la carta sigue siendo tuya.'],
    ['Restam', 'Quedan'],
    ['vagas no lote de hoje', 'lugares en el lote de hoy'],
    ['Antes de você perguntar.', 'Antes de que preguntes.'],
    ['Eu não sei a hora exata que nasci. Dá pra fazer?', 'No sé la hora exacta en que nací. ¿Se puede hacer?'],
    ['Dá. Sem a hora, calculamos o mapa com precisão parcial — Sol, planetas e aspectos saem corretos; Ascendente e casas ficam aproximados. A hora costuma estar na sua certidão de nascimento, e vale muito a pena procurar. Você pode corrigir depois na sua área, e o mapa é recalculado sem custo.', 'Sí. Sin la hora calculamos la carta con precisión parcial: el Sol, los planetas y los aspectos salen correctos; el Ascendente y las casas quedan aproximados. La hora suele figurar en tu partida de nacimiento. Podés corregirla después en tu área y recalculamos la carta sin costo.'],
    ['Como chega o horóscopo?', '¿Cómo llega el horóscopo?'],
    ['Dois lugares: no seu e-mail toda manhã, e salvo na sua área no site. Você não precisa baixar nenhum app nem instalar nada.', 'En dos lugares: a tu e-mail cada mañana y guardado en tu área del sitio. No necesitás descargar ni instalar ninguna aplicación.'],
    ['Isso é feito por robô ou por astrólogo?', '¿Esto lo hace un robot o un astrólogo?'],
    ['O cálculo é astronômico e exato — efemérides reais, posição planetária na data, hora e coordenadas do seu nascimento. A interpretação segue a tradição astrológica e é escrita em português claro. Não é texto genérico reaproveitado: cada dia é recalculado sobre o seu mapa.', 'El cálculo es astronómico y exacto: efemérides reales y posición planetaria según la fecha, la hora y las coordenadas de tu nacimiento. La interpretación sigue la tradición astrológica y está escrita en español claro. No es texto genérico reutilizado: cada día se recalcula sobre tu carta.'],
    ['Posso cancelar quando quiser?', '¿Puedo cancelar cuando quiera?'],
    ['Sim, em um clique dentro da sua área. Sem multa, sem ligação de retenção, sem prazo mínimo. O que você já baixou continua seu.', 'Sí, con un clic dentro de tu área. Sin multa, llamadas de retención ni plazo mínimo. Todo lo que ya descargaste sigue siendo tuyo.'],
    ['O Mapa Astral de brinde é o completo mesmo?', '¿La Carta Astral de regalo es realmente completa?'],
    ['É o completo: Sol, Lua, Ascendente, os 10 planetas nos signos e nas casas, as 12 casas e os aspectos, tudo interpretado. É o mesmo mapa que vendemos avulso — na primeira assinatura ele entra sem custo.', 'Es completa: Sol, Luna, Ascendente, los 10 planetas en los signos y las casas, las 12 casas y los aspectos, todo interpretado. Es la misma carta que vendemos por separado y viene sin costo con la primera suscripción.'],
    ['Meus dados de nascimento ficam guardados?', '¿Guardan mis datos de nacimiento?'],
    ['Ficam salvos só na sua conta, usados apenas pra calcular o que é seu. Não vendemos nem compartilhamos com ninguém. Você pode pedir a exclusão a qualquer momento.', 'Quedan guardados solamente en tu cuenta y se usan para calcular tus lecturas. No los vendemos ni compartimos. Podés pedir su eliminación cuando quieras.'],
    ['Amanhã de manhã', 'Mañana por la mañana'],
    ['já podia ser', 'ya podría estar'],
    ['o seu horóscopo chegando.', 'llegando tu horóscopo.'],
    ['Cada dia que passa é uma leitura sua que você deixou de receber. E os trânsitos da semana que vem não esperam você decidir.', 'Cada día que pasa es una lectura tuya que dejaste de recibir. Los tránsitos de la semana que viene no esperan a que decidas.'],
    ['Começar por ARS ', 'Empezar por ARS '],
    ['Começar por', 'Empezar por'],
    ['Com o Mapa Astral Completo de brinde', 'Con la Carta Astral Completa de regalo'],
    ['Assinar por', 'Suscribirme por']
  ];

  var v2 = [
    ['Seu Horóscopo Personalizado + Mapa Astral Completo | AstroDicas', 'Tu Horóscopo Personalizado + Carta Astral Completa | AstroDicas'],
    ['Condição de lançamento acaba em', 'La condición de lanzamiento termina en'],
    ['Mapa astral + horóscopo diário', 'Carta natal + horóscopo diario'],
    ['O horóscopo que você lê todo dia foi escrito', 'El horóscopo que leés todos los días fue escrito'],
    ['uma vez', 'una vez'],
    ['para', 'para'],
    ['200 milhões de pessoas.', '200 millones de personas.'],
    ['Por alguém que não sabe a sua hora nem a sua cidade.', 'Por alguien que no conoce tu hora ni tu ciudad.'],
    ['Aqui eu faço o seu com seu nome, sua data, hora e cidade — e recalculo todo dia para mostrar como os trânsitos batem na sua vida. Você recebe o primeiro ainda hoje.', 'Acá hago el tuyo con tu nombre, tu fecha, hora y ciudad — y lo recalculo cada día para mostrarte cómo los tránsitos impactan en tu vida. Recibís el primero hoy mismo.'],
    ['Mapa Astral Completo agora + um brinde novo todo mês.', 'Carta Astral Completa ahora + un regalo nuevo todos los meses.'],
    ['QUERO MEU HORÓSCOPO PERSONALIZADO', 'QUIERO MI HORÓSCOPO PERSONALIZADO'],
    ['/mês · cancela quando quiser', '/mes · cancelá cuando quieras'],
    ['Cancela num clique', 'Cancelá con un clic'],
    ['+1.469 mapas calculados', '+1.469 cartas calculadas'],
    ['Camila R.', 'Sofía M.'],
    ['Juliana M.', 'Valentina G.'],
    ['Patrícia L.', 'Martina F.'],
    ['Rita S.', 'Lucía B.'],
    ['Aline P.', 'Julieta C.'],
    ['Fernanda C.', 'Agustina R.'],
    ['Sua transformação', 'Tu transformación'],
    ['Do horóscopo genérico', 'Del horóscopo genérico'],
    ['pro seu céu de verdade', 'a tu cielo real'],
    ['seu céu de verdade', 'tu cielo real'],
    ['pro ', 'a '],
    ['Hoje', 'Hoy'],
    ['Você lê o do seu signo e não sente nada. Parece escrito pra outra pessoa.', 'Leés el de tu signo y no sentís nada. Parece escrito para otra persona.'],
    ['Com a AstroDicas', 'Con AstroDicas'],
    ['Você lê e trava: fala exatamente do que está acontecendo na sua vida hoje.', 'Lo leés y te detenés: habla exactamente de lo que está pasando hoy en tu vida.'],
    ['Você toma decisão importante no escuro e só sente o baque depois.', 'Tomás una decisión importante a ciegas y sentís el impacto después.'],
    ['Você é avisada antes do retrógrado, da lua cheia e dos trânsitos pesados no SEU mapa.', 'Te avisamos antes del retrógrado, la luna llena y los tránsitos intensos en TU carta.'],
    ['Mapa astral com astrólogo custa ARS 200+ e você faz uma vez na vida.', 'Una carta natal con un astrólogo cuesta mucho más y suele hacerse una sola vez.'],
    ['Mapa astral com astrólogo custa R$200+ e você faz uma vez na vida.', 'Una carta natal con un astrólogo cuesta mucho más y suele hacerse una sola vez.'],
    ['Mapa Astral Completo de brinde + leitura nova todo dia, por menos que um lanche.', 'Carta Astral Completa de regalo + una lectura nueva todos los días por menos que una comida.'],
    ['Todo dia', 'Todos los días'],
    ['Leitura nova sempre', 'Siempre una lectura nueva'],
    ['Só sua', 'Solamente tuya'],
    ['Calculada no seu mapa', 'Calculada sobre tu carta'],
    ['Na hora', 'En el momento'],
    ['Acesso em segundos', 'Acceso en segundos'],
    ['Brinde mensal', 'Regalo mensual'],
    ['Guia novo todo mês', 'Una guía nueva cada mes'],
    ['O que você recebe', 'Lo que recibís'],
    ['Tudo isso já no primeiro dia', 'Todo esto desde el primer día'],
    ['Seu horóscopo, todo dia', 'Tu horóscopo, todos los días'],
    ['O primeiro chega hoje mesmo, assim que seu mapa é calculado. Depois é toda manhã: trânsitos do dia cruzados com o seu mapa natal, em português claro. Nunca é texto repetido.', 'El primero llega hoy, apenas se calcula tu carta. Después llega cada mañana: los tránsitos del día cruzados con tu carta natal, en español claro. Nunca es texto repetido.'],
    ['Seu mapa natal em PDF: Sol, Lua, Ascendente, os 10 planetas, as 12 casas e os aspectos — tudo interpretado. É o mesmo que vendemos avulso.', 'Tu carta natal en PDF: Sol, Luna, Ascendente, los 10 planetas, las 12 casas y los aspectos, todo interpretado. Es la misma que vendemos por separado.'],
    ['De brinde hoje', 'De regalo hoy'],
    ['Um brinde novo todo mês', 'Un regalo nuevo todos los meses'],
    ['Todo dia 1º você recebe o', 'Cada día 1 recibís la'],
    [': os trânsitos daquele mês no SEU mapa, o calendário lunar e os melhores dias pra amar, pedir aumento e assinar contrato. Mês novo, guia novo.', ': los tránsitos de ese mes en TU carta, el calendario lunar y los mejores días para el amor, pedir un aumento o firmar un contrato. Mes nuevo, guía nueva.'],
    ['Todo mês, sem custo', 'Todos los meses, sin costo'],
    ['Sua área no site', 'Tu área en el sitio'],
    ['Histórico de todos os horóscopos, seus mapas pra baixar quando quiser, e tudo que comprar depois fica guardado lá.', 'El historial de todos tus horóscopos, tus cartas para descargar cuando quieras y todo lo que compres después guardado ahí.'],
    ['Entrega imediata', 'Entrega inmediata'],
    ['Pagou, recebeu. O acesso cai no seu e-mail na hora, e o Mapa Astral mais o horóscopo de hoje saem em minutos, assim que você manda seus dados de nascimento.', 'Pagaste y recibiste. El acceso llega a tu e-mail en el momento, y la Carta Astral junto con el horóscopo de hoy se generan en minutos cuando completás tus datos de nacimiento.'],
    ['QUERO COMEÇAR AGORA', 'QUIERO EMPEZAR AHORA'],
    ['Quem já recebe', 'Quienes ya lo reciben'],
    ['Não acredite só na gente', 'No te quedes solamente con nuestra palabra'],
    ['Veja o que elas mandaram depois de começar a receber', 'Mirá lo que enviaron después de empezar a recibirlo'],
    ['gente eu li o de hoje de manhã e travei 😳', 'chicas, leí el de esta mañana y me quedé helada 😳'],
    ['falava EXATAMENTE da conversa que eu tive ontem no trabalho', 'hablaba EXACTAMENTE de la conversación que tuve ayer en el trabajo'],
    ['nunca tinha acontecido isso comigo com horóscopo', 'nunca me había pasado esto con un horóscopo'],
    ['o mapa completo sozinho já pagou o ano inteiro 🙌', 'la carta completa por sí sola ya pagó todo el año 🙌'],
    ['tinha pago quase 200 reais num parecido e esse veio bem mais detalhado', 'había pagado mucho más por uno parecido y este llegó con más detalle'],
    ['chegou tudo no e-mail na hora', 'todo llegó al e-mail en el momento'],
    ['me avisou do retrógrado ANTES', 'me avisó del retrógrado ANTES'],
    ['segurei a assinatura de um contrato por causa disso', 'esperé para firmar un contrato por eso'],
    ['no fim deu ruim pra quem assinou 😅 valeu cada centavo', 'al final salió mal para quienes firmaron 😅 valió cada centavo'],
    ['confesso que achei que fosse pouca coisa por esse preço 😅', 'confieso que pensé que sería poco por este precio 😅'],
    ['mas é MUITA coisa, todo dia chega um texto diferente', 'pero es MUCHÍSIMO, cada día llega un texto diferente'],
    ['melhor compra que fiz esse ano', 'la mejor compra que hice este año'],
    ['o guia do mês chegou hoje e eu já marquei tudo na agenda', 'la guía del mes llegó hoy y ya marqué todo en la agenda'],
    ['os dias bons pra conversar com meu chefe vieram certinhos', 'los días buenos para hablar con mi jefe llegaron perfectos'],
    ['nunca tinha visto nada assim tão específico', 'nunca había visto algo tan específico'],
    ['meu mapa explicou coisa que eu pago terapia pra entender 😅', 'mi carta explicó cosas que trabajo en terapia 😅'],
    ['a parte do ascendente me deixou sem palavras', 'la parte del Ascendente me dejó sin palabras'],
    ['já indiquei pra três amigas', 'ya se lo recomendé a tres amigas'],
    ['Escolha seu acesso', 'Elegí tu acceso'],
    ['Quanto vale saber o que', '¿Cuánto vale saber qué'],
    ['o seu céu está fazendo?', 'está haciendo tu cielo?'],
    ['Sem fidelidade. Sem multa. Cancela num clique, quando quiser.', 'Sin permanencia ni multa. Cancelá con un clic cuando quieras.'],
    ['Mais escolhido', 'Más elegido'],
    ['O essencial pra começar hoje', 'Lo esencial para empezar hoy'],
    ['Horóscopo diário calculado no seu mapa', 'Horóscopo diario calculado sobre tu carta'],
    ['Mapa Astral Completo em PDF (brinde)', 'Carta Astral Completa en PDF (regalo)'],
    ['Guia do Mês — brinde novo todo mês', 'Guía del Mes: un regalo nuevo cada mes'],
    ['Sua área no site com tudo salvo', 'Tu área en el sitio con todo guardado'],
    ['Entrega imediata por e-mail', 'Entrega inmediata por e-mail'],
    ['Previsão personalizada da semana', 'Previsión personalizada de la semana'],
    ['Avisos de lua e retrógrados', 'Avisos de luna y retrógrados'],
    ['Calendário Lunar + Guia dos Retrógrados', 'Calendario Lunar + Guía de los Retrógrados'],
    ['Suporte prioritário', 'Soporte prioritario'],
    ['QUERO O PLANO LUA', 'QUIERO PLAN LUNA'],
    ['Você recebe no e-mail o acesso à sua área, já com o mapa liberado. Sem senha pra inventar.', 'Recibís por e-mail el acceso a tu área con la carta ya habilitada. Sin inventar contraseñas.'],
    ['Completo', 'Completo'],
    ['1 mês inteiro do Plano Lua + todos os bônus', '1 mes completo de Plan Luna + todos los bonus'],
    ['Pagamento único · sem recorrência', 'Pago único · sin recurrencia'],
    ['1 mês do Plano Lua completo', '1 mes de Plan Luna completo'],
    ['— horóscopo diário, Mapa Astral e Guia do Mês', '— horóscopo diario, Carta Astral y Guía del Mes'],
    ['Previsão personalizada da semana, todo domingo', 'Previsión personalizada de la semana, cada domingo'],
    ['Avisos de lua nova, cheia e retrógrados', 'Avisos de luna nueva, llena y retrógrados'],
    ['Calendário Lunar do ano', 'Calendario Lunar del año'],
    ['Quero o Premium', 'Quiero Premium'],
    ['Pagamento único, sem assinatura', 'Pago único, sin suscripción'],
    ['Você paga uma vez só. Bônus valendo', 'Pagás una sola vez. Bonus por un valor de'],
    ['liberados junto com o acesso e seus pra sempre.', 'habilitados junto con el acceso y tuyos para siempre.'],
    ['Cancela quando quiser. Sério.', 'Cancelá cuando quieras. En serio.'],
    ['Um clique dentro da sua área e acabou. Sem multa, sem fidelidade, sem prazo mínimo, sem ligação de retenção tentando te convencer. E tudo que você já baixou continua sendo seu pra sempre.', 'Un clic dentro de tu área y listo. Sin multa, permanencia, plazo mínimo ni llamadas de retención. Todo lo que ya descargaste sigue siendo tuyo para siempre.'],
    ['✓ Sem fidelidade', '✓ Sin permanencia'],
    ['✓ Sem multa', '✓ Sin multa'],
    ['✓ 1 clique', '✓ 1 clic'],
    ['✓ Sem ligação', '✓ Sin llamadas'],
    ['Chega no seu e-mail', 'Llega a tu e-mail'],
    ['E fica salvo no site', 'Y queda guardado en el sitio'],
    ['Garantia de 7 dias', 'Garantía de 7 días'],
    ['dias', 'días'],
    ['Garantia incondicional de 7 dias', 'Garantía incondicional de 7 días'],
    ['Recebe o mapa, lê os horóscopos da semana. Se por qualquer motivo achar que não valeu a pena, devolvo 100% do seu dinheiro — sem pergunta, sem burocracia.', 'Recibís la carta y leés los horóscopos de la semana. Si por cualquier motivo sentís que no valió la pena, te devolvemos el 100% de tu dinero, sin preguntas ni burocracia.'],
    ['Reembolso total', 'Reembolso total'],
    ['Sem perguntas', 'Sin preguntas'],
    ['Processo rápido', 'Proceso rápido'],
    ['Consigo cancelar mesmo? Como?', '¿Realmente puedo cancelar? ¿Cómo?'],
    ['Consegue, num clique dentro da sua área — o botão fica visível, não escondido. Sem multa, sem fidelidade, sem prazo mínimo e sem ninguém te ligando pra tentar convencer. O que você já baixou continua seu.', 'Sí, con un clic dentro de tu área: el botón está visible, no escondido. Sin multa, permanencia, plazo mínimo ni llamadas para convencerte. Todo lo que ya descargaste sigue siendo tuyo.'],
    ['O acesso chega na hora?', '¿El acceso llega en el momento?'],
    ['Chega. Assim que o pagamento é aprovado, cai um e-mail com o link da sua área — sem senha pra inventar, é só clicar. Você informa seus dados de nascimento e, em minutos, recebe o Mapa Astral Completo', 'Sí. Cuando se aprueba el pago, recibís un e-mail con el enlace a tu área, sin inventar contraseñas. Completás tus datos de nacimiento y en minutos recibís la Carta Astral Completa'],
    ['e o horóscopo de hoje', 'y el horóscopo de hoy'],
    ['. Não precisa esperar até amanhã.', '. No necesitás esperar hasta mañana.'],
    ['Não sei a hora exata que nasci. Dá pra fazer?', 'No sé la hora exacta en que nací. ¿Se puede hacer?'],
    ['Dá. Sem a hora, Sol, planetas e aspectos saem corretos; Ascendente e casas ficam aproximados. A hora costuma estar na sua certidão de nascimento e vale a pena procurar — você corrige depois na sua área e o mapa é recalculado sem custo.', 'Sí. Sin la hora, el Sol, los planetas y los aspectos salen correctos; el Ascendente y las casas quedan aproximados. La hora suele figurar en tu partida de nacimiento. Podés corregirla después y recalculamos la carta sin costo.'],
    ['É robô ou astrólogo de verdade?', '¿Es un robot o un astrólogo real?'],
    ['O cálculo é astronômico e exato: efemérides reais, posição planetária na data, hora e coordenadas do seu nascimento. A interpretação segue a tradição astrológica, escrita em português claro. Cada dia é recalculado sobre o seu mapa — não é texto reaproveitado.', 'El cálculo es astronómico y exacto: efemérides reales y posición planetaria según la fecha, la hora y las coordenadas de tu nacimiento. La interpretación sigue la tradición astrológica y está escrita en español claro. Cada día se recalcula sobre tu carta; no es texto reutilizado.'],
    ['Como funciona o brinde de todo mês?', '¿Cómo funciona el regalo de cada mes?'],
    ['Todo dia 1º você recebe o Guia do Mês: um PDF calculado no seu mapa com os trânsitos daquele mês, o calendário lunar e os melhores dias pra decisões de amor, dinheiro e trabalho. Não é o mesmo pra todo mundo — é feito pro seu nascimento, igual o horóscopo. Fica salvo na sua área e é seu pra sempre, mesmo se cancelar depois.', 'Cada día 1 recibís la Guía del Mes: un PDF calculado sobre tu carta con los tránsitos de ese mes, el calendario lunar y los mejores días para decisiones de amor, dinero y trabajo. No es igual para todos: está hecho para tu nacimiento, como el horóscopo. Queda guardado en tu área y es tuyo para siempre, incluso si cancelás después.'],
    ['Qual a diferença do Plano Lua pro Premium?', '¿Cuál es la diferencia entre Plan Luna y Premium?'],
    ['O Plano Lua é assinatura mensal: horóscopo diário, Mapa Astral Completo e o Guia do Mês, todo mês, enquanto você quiser. O Premium é', 'Plan Luna es una suscripción mensual: horóscopo diario, Carta Astral Completa y Guía del Mes mientras quieras. Premium es un'],
    [': você leva 1 mês inteiro do Plano Lua mais a previsão semanal, os avisos de lua e retrógrados e os 5 bônus (Mapa do Amor, Mapa da Prosperidade, Calendário Lunar, Guia dos Retrógrados e Manual do Ascendente) — sem assinatura e sem cobrança recorrente.', ': recibís 1 mes completo de Plan Luna, la previsión semanal, avisos de luna y retrógrados y 5 bonus (Mapa del Amor, Mapa de la Prosperidad, Calendario Lunar, Guía de los Retrógrados y Manual del Ascendente), sin suscripción ni cobros recurrentes.'],
    ['E se eu não gostar?', '¿Y si no me gusta?'],
    ['Você tem 7 dias de garantia incondicional. Pede o reembolso e devolvemos 100% — sem perguntas, sem burocracia.', 'Tenés 7 días de garantía incondicional. Pedís el reembolso y te devolvemos el 100%, sin preguntas ni burocracia.'],
    ['Meus dados ficam guardados?', '¿Guardan mis datos?'],
    ['Só na sua conta, usados apenas pra calcular o que é seu. Não vendemos nem compartilhamos com ninguém. Pode pedir exclusão quando quiser.', 'Solamente en tu cuenta y para calcular tus lecturas. No los vendemos ni compartimos. Podés pedir su eliminación cuando quieras.'],
    ['Quantos dias mais', '¿Cuántos días más'],
    ['você vai gastar com o texto errado?', 'vas a perder con el texto equivocado?'],
    ['Você já sabe que aquele horóscopo não é seu. Amanhã ele vai estar lá de novo, igualzinho, falando com 200 milhões de pessoas ao mesmo tempo — e você vai tentar se encaixar de novo.', 'Ya sabés que ese horóscopo no es tuyo. Mañana va a estar ahí otra vez, igual, hablándole a 200 millones de personas al mismo tiempo, y vas a intentar encajar de nuevo.'],
    ['Você já sabe que aquele horóscopo não é seu.', 'Ya sabés que ese horóscopo no es tuyo.'],
    ['Amanhã ele vai estar lá de novo, igualzinho, falando com 200 milhões de pessoas ao mesmo tempo — e você vai tentar se encaixar de novo.', 'Mañana va a estar ahí otra vez, igual, hablándole a 200 millones de personas al mismo tiempo, y vas a intentar encajar de nuevo.'],
    ['Amanhã ele vai estar lá de novo, igualzinho,', 'Mañana va a estar ahí otra vez, igual,'],
    ['falando com 200 milhões de pessoas ao mesmo tempo — e você vai tentar se encaixar de novo.', 'hablándole a 200 millones de personas al mismo tiempo, y vas a intentar encajar de nuevo.'],
    ['Ou o seu chega hoje.', 'O el tuyo llega hoy.'],
    ['Calculado com a sua hora e a sua cidade, escrito só pra você. Leva dois minutos pra decidir isso.', 'Calculado con tu hora y tu ciudad, escrito solamente para vos. Decidirlo lleva dos minutos.'],
    ['COMEÇAR POR ARS ', 'EMPEZAR POR ARS '],
    ['COMEÇAR POR', 'EMPEZAR POR'],
    ['/MÊS', '/MES'],
    ['Brinde novo todo mês · cancela quando quiser', 'Regalo nuevo cada mes · cancelá cuando quieras'],
    ['Compra garantida', 'Compra garantizada'],
    ['Dados protegidos', 'Datos protegidos'],
    ['Conteúdo de astrologia com finalidade de autoconhecimento e entretenimento. Não substitui orientação médica, psicológica, jurídica ou financeira.', 'Contenido de astrología con fines de autoconocimiento y entretenimiento. No reemplaza orientación médica, psicológica, jurídica ni financiera.'],
    ['QUERO MEU MAPA E HORÓSCOPO', 'QUIERO MI CARTA Y HORÓSCOPO'],
    ['ESPERE! Vai deixar tudo isso pra trás?', '¡ESPERÁ! ¿Vas a dejar todo esto atrás?'],
    ['Só com o Plano Lua você', 'Solamente con Plan Luna'],
    ['NÃO leva', 'NO recibís'],
    ['O Premium sai por', 'Premium cuesta'],
    ['. Só agora:', '. Solamente ahora:'],
    ['pagamento único · todos os bônus juntos valem', 'pago único · todos los bonus juntos valen'],
    ['SIM! QUERO O PREMIUM POR ARS ', '¡SÍ! QUIERO PREMIUM POR ARS '],
    ['SIM! QUERO O PREMIUM POR', '¡SÍ! QUIERO PREMIUM POR'],
    ['Não, continuar só com o Plano Lua', 'No, continuar solamente con Plan Luna'],
    ['Peraí! Antes de você ir', '¡Esperá! Antes de que te vayas'],
    ['Deixa eu fazer o menor preço que já fiz do Plano Lua:', 'Quiero ofrecerte el precio más bajo que hicimos para Plan Luna:'],
    ['Horóscopo diário calculado no SEU mapa', 'Horóscopo diario calculado sobre TU carta'],
    ['Mapa Astral Completo em PDF, de brinde', 'Carta Astral Completa en PDF, de regalo'],
    ['De', 'De'],
    ['por só', 'por solamente'],
    ['preço travado enquanto você for assinante — cancela quando quiser', 'precio congelado mientras seas suscriptora · cancelá cuando quieras'],
    ['QUERO O PLANO LUA POR ARS ', 'QUIERO PLAN LUNA POR ARS '],
    ['QUERO O PLANO LUA POR', 'QUIERO PLAN LUNA POR'],
    ['Cancela quando quiser, sem multa', 'Cancelá cuando quieras, sin multa'],
    ['Não, prefiro perder essa condição', 'No, prefiero perder esta condición'],
    ['Slot do vídeo', 'Espacio para el video'],
    ['Preencha VIDEO_MP4 ou VIDEO_YOUTUBE no config.', 'Configurá VIDEO_MP4 o VIDEO_YOUTUBE.'],
    ['Some sozinho se os dois ficarem vazios.', 'Se oculta automáticamente si ambos quedan vacíos.'],
    ['Toque para ativar o som', 'Tocá para activar el sonido'],
    ['acabou de assinar', 'acaba de suscribirse'],
    ['há ', 'hace '],
    [' min', ' min']
  ];

  var pairs = (page === 'v2' ? v2 : v1).concat(common).sort(function (a, b) { return b[0].length - a[0].length; });

  function translate(value) {
    var next = value;
    pairs.forEach(function (pair) { next = next.split(pair[0]).join(pair[1]); });
    return next;
  }

  function translateElement(root) {
    if (!root || root.nodeType !== 1) return;
    if (root.matches('script, style, noscript')) return;
    ['aria-label', 'title', 'placeholder', 'alt'].forEach(function (name) {
      if (root.hasAttribute(name)) root.setAttribute(name, translate(root.getAttribute(name)));
    });
    root.querySelectorAll('[aria-label], [title], [placeholder], [alt]').forEach(function (node) {
      ['aria-label', 'title', 'placeholder', 'alt'].forEach(function (name) {
        if (node.hasAttribute(name)) node.setAttribute(name, translate(node.getAttribute(name)));
      });
    });
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        return node.parentElement && !node.parentElement.closest('script, style, noscript') ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });
    var nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(function (node) {
      var next = translate(node.nodeValue);
      if (next !== node.nodeValue) node.nodeValue = next;
    });
  }

  function apply() {
    document.title = page === 'v2'
      ? 'Tu Horóscopo Personalizado + Carta Astral Completa | AstroDicas'
      : 'Plan Luna — tu horóscopo calculado sobre tu carta natal | AstroDicas';
    var description = document.querySelector('meta[name="description"]');
    if (description) description.content = page === 'v2'
      ? 'Horóscopo diario calculado sobre tu carta natal. Carta Astral Completa de regalo y una guía nueva cada mes.'
      : 'Horóscopo diario calculado sobre tu carta natal, disponible en el sitio y en tu e-mail. Carta Astral Completa de regalo.';
    var ogTitle = document.querySelector('meta[property="og:title"]');
    var ogDescription = document.querySelector('meta[property="og:description"]');
    if (ogTitle) ogTitle.content = page === 'v2' ? 'Tu horóscopo, no el de tu signo' : 'Plan Luna — tu horóscopo, no el de tu signo';
    if (ogDescription) ogDescription.content = page === 'v2'
      ? 'Horóscopo diario calculado sobre tu carta natal. Carta Astral Completa de regalo.'
      : 'Horóscopo diario calculado sobre tu carta natal. Carta Astral Completa de regalo.';
    translateElement(document.body);
    document.querySelectorAll('a[href="/termos"], a[href="/privacidade"], a[href="/suporte"]').forEach(function (link) {
      link.setAttribute('href', '/es' + link.getAttribute('href'));
    });
    if (page === 'v1') {
      var wheelCore = document.querySelector('.wheel-core');
      if (wheelCore) wheelCore.innerHTML = 'Carta<br>Natal<small>calculada, no improvisada</small>';
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', apply, { once: true });
  else apply();

  new MutationObserver(function (mutations) {
    mutations.forEach(function (mutation) {
      mutation.addedNodes.forEach(function (node) {
        if (node.nodeType === 1) translateElement(node);
        else if (node.nodeType === 3 && node.parentElement && !node.parentElement.closest('script, style, noscript')) {
          var next = translate(node.nodeValue);
          if (next !== node.nodeValue) node.nodeValue = next;
        }
      });
    });
  }).observe(document.documentElement, { childList: true, subtree: true });
})();
