/* ============================================================ TgPlayer Web API
   Cliente leve para a API REST. Guarda o token JWT no localStorage.
   A URL base pode ser sobrescrita (window.TGWEB_API_BASE) para quando o
   frontend está hospedado na Cloudflare e o backend em outro domínio.
   ============================================================ */
(function () {
  const API_BASE = (window.TGWEB_API_BASE || "").replace(/\/$/, "");
  const TOKEN_KEY = "tgweb_token";

  const api = {
    get token() { return localStorage.getItem(TOKEN_KEY) || ""; },
    set token(v) { v ? localStorage.setItem(TOKEN_KEY, v) : localStorage.removeItem(TOKEN_KEY); },

    base(path) { return API_BASE + path; },

    /*
     * Lê o corpo de uma Response UMA ÚNICA VEZ.
     *
     * Regra de ouro do Fetch API: o body é um ReadableStream que só pode ser
     * consumido uma vez. Chamar res.json() e depois res.text() (ou vice-versa)
     * na MESMA Response dispara erros como:
     *   - "Response.text: Body has already been consumed"  (Firefox)
     *   - "Body stream already read" / "body stream already read" (Chrome)
     *   - "Failed to execute 'text' on 'Response': body stream already read"
     *   - "ReadableStream locked" / "Cannot read body after it has been consumed"
     *
     * Por isso lemos SEMPRE como texto (uma leitura só) e tentamos converter
     * para JSON em memória. Assim nunca tocamos no stream mais de uma vez.
     * Retorna: { text, json } onde json é null quando não for JSON válido.
     */
    async _readBodyOnce(res) {
      let text = "";
      try {
        text = await res.text();
      } catch (e) {
        // Stream indisponível/abortado: degrada para corpo vazio em vez de quebrar.
        return { text: "", json: null };
      }
      let json = null;
      const ct = (res.headers.get("content-type") || "").toLowerCase();
      if (text && (ct.includes("application/json") || text.trimStart()[0] === "{" || text.trimStart()[0] === "[")) {
        try { json = JSON.parse(text); } catch (e) { json = null; }
      }
      return { text, json };
    },

    async request(method, path, body) {
      const headers = { "Content-Type": "application/json" };
      if (this.token) headers["Authorization"] = "Bearer " + this.token;
      const res = await fetch(API_BASE + path, {
        method,
        headers,
        body: body != null ? JSON.stringify(body) : undefined,
      });

      // Lê o corpo UMA só vez, independentemente do status. Tudo (sucesso,
      // 401 e demais erros) reaproveita este resultado em memória.
      const { text, json } = await this._readBodyOnce(res);

      if (res.status === 401) {
        // session_revoked é tratado pela UI; token expirado desloga.
        if (!text.includes("session_revoked")) {
          this.token = "";
          window.dispatchEvent(new CustomEvent("tgweb:logout"));
        }
        const detail = (json && (json.detail || json.message || json.error)) || text;
        const err = new Error(detail || "Não autenticado");
        err.status = 401;
        // err.body mantém o texto cru para o app.js detectar "session_revoked".
        err.body = text;
        throw err;
      }

      if (!res.ok) {
        const detail = (json && (json.detail || json.message || json.error)) || text;
        const err = new Error(detail || ("Erro " + res.status));
        err.status = res.status;
        err.body = text;
        throw err;
      }

      // Sucesso: devolve JSON quando houver, senão o texto cru.
      return json != null ? json : text;
    },

    get(p) { return this.request("GET", p); },
    post(p, b) { return this.request("POST", p, b); },
    del(p) { return this.request("DELETE", p); },

    // ---- endpoints (multiusuário)
    authState() { return this.get("/api/auth/state"); },
    register(email, password) { return this.post("/api/register", { email, password }); },
    login(email, password) { return this.post("/api/login", { email, password }); },
    me() { return this.get("/api/me"); },

    // Conta(s) Telegram do usuário. account_id é opcional (usa a 1ª por padrão).
    tgAccounts() { return this.get("/api/telegram/accounts"); },
    tgCreateAccount() { return this.post("/api/telegram/accounts", {}); },
    tgDeleteAccount(id) { return this.del("/api/telegram/accounts/" + id); },
    tgStatus(account_id) { return this.get("/api/telegram/status" + (account_id ? ("?account_id=" + account_id) : "")); },
    tgCredentials(api_id, api_hash, account_id) { return this.post("/api/telegram/credentials", { api_id, api_hash, account_id }); },
    tgSendCode(phone, account_id) { return this.post("/api/telegram/send-code", { phone, account_id }); },
    tgSignIn(code, account_id) { return this.post("/api/telegram/sign-in", { code, account_id }); },
    tgPassword(password, account_id) { return this.post("/api/telegram/password", { password, account_id }); },
    tgLogout(account_id) { return this.post("/api/telegram/logout", { account_id }); },
    tgDialogs(account_id) { return this.get("/api/telegram/dialogs" + (account_id ? ("?account_id=" + account_id) : "")); },

    // Admin (sem dados sensíveis)
    adminOverview() { return this.get("/api/admin/overview"); },
    adminSetActive(user_id, active) { return this.post("/api/admin/users/" + user_id + "/active", { active }); },

    courses() { return this.get("/api/courses"); },
    addCourses(courses) { return this.post("/api/courses/add", { courses }); },
    deleteCourse(id) { return this.del("/api/courses/" + id); },
    syncCourse(id, limit) { return this.post("/api/courses/" + id + "/sync", { limit }); },
    subjects(id) { return this.get("/api/courses/" + id + "/subjects"); },
    videos(id) { return this.get("/api/courses/" + id + "/videos"); },
    tree(id) { return this.get("/api/courses/" + id + "/tree"); },

    markWatched(id) { return this.post("/api/videos/" + id + "/watched", {}); },
    markUnwatched(id) { return this.post("/api/videos/" + id + "/unwatched", {}); },
    toggleFav(id) { return this.post("/api/videos/" + id + "/favorite", {}); },
    saveProgress(id, position_ms, duration_ms) { return this.post("/api/videos/" + id + "/progress", { position_ms, duration_ms }); },
    prepareStream(id) { return this.post("/api/videos/" + id + "/prepare-stream", {}); },
    streamUrl(token) { return API_BASE + "/api/stream/" + token; },

    continueWatching() { return this.get("/api/continue"); },
    dashboard() { return this.get("/api/study/dashboard"); },
    setGoal(hours) { return this.post("/api/study/goal", { hours }); },
    logPomodoro(minutes, course_id) { return this.post("/api/study/pomodoro", { minutes, course_id }); },

    tasks() { return this.get("/api/tasks"); },
    addTask(text, priority, course_id) { return this.post("/api/tasks", { text, priority, course_id }); },
    toggleTask(id) { return this.post("/api/tasks/" + id + "/toggle", {}); },
    deleteTask(id) { return this.del("/api/tasks/" + id); },
  };

  window.api = api;
})();
