/* ============================================================ TgPlayer Web — Config
   Aponta o frontend para o backend (API).

   COMO PREENCHER:
   - Se o backend SERVE o próprio frontend (mesma origem), deixe "".
   - Se você publica APENAS o frontend na Cloudflare Pages e o backend roda
     em OUTRO domínio (ex.: Render), coloque aqui a URL pública do backend:
        window.TGWEB_API_BASE = "https://SEU-BACKEND.onrender.com";

   IMPORTANTE: sem isso, o frontend no Cloudflare envia POST /api/login para o
   PRÓPRIO domínio do Cloudflare (que só serve HTML estático), resultando em
   "405 Method Not Allowed". A API precisa apontar para o Render.
   ============================================================ */

// >>> URL do backend no Render (sem barra no final).
window.TGWEB_API_BASE = "https://telegram-drive-yzg9.onrender.com";

/* ---------------------------------------------------------------------------
   Proteção contra configuração esquecida.
   Se o site estiver rodando em um domínio do Cloudflare Pages (*.pages.dev ou
   domínio customizado) mas a API ainda apontar para si mesma (""), as chamadas
   POST cairiam no SPA fallback e dariam 405. Detectamos isso e avisamos no
   console com uma mensagem clara, em vez de falhar de forma silenciosa.
--------------------------------------------------------------------------- */
(function () {
  try {
    var base = (window.TGWEB_API_BASE || "").trim();
    var host = window.location.hostname || "";
    var isCloudflarePages = /\.pages\.dev$/i.test(host);

    // Ainda com o placeholder não substituído.
    if (base.indexOf("SEU-BACKEND") !== -1) {
      console.error(
        "[TgPlayer] CONFIG PENDENTE: edite web/frontend/assets/config.js e " +
        "defina window.TGWEB_API_BASE com a URL do seu backend no Render " +
        "(ex.: https://meu-app.onrender.com). Sem isso o login dá erro 405."
      );
    } else if (isCloudflarePages && base === "") {
      console.error(
        "[TgPlayer] O frontend está no Cloudflare Pages mas TGWEB_API_BASE " +
        "está vazio. As chamadas /api iriam para o próprio Cloudflare (erro 405). " +
        "Defina a URL do backend (Render) em config.js."
      );
    }

    // Normaliza: remove barra final para evitar // nas rotas.
    if (base) window.TGWEB_API_BASE = base.replace(/\/+$/, "");
  } catch (e) {
    /* nunca quebrar o boot por causa da checagem */
  }
})();
