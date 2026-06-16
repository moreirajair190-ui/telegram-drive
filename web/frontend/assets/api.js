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

    async request(method, path, body) {
      const headers = { "Content-Type": "application/json" };
      if (this.token) headers["Authorization"] = "Bearer " + this.token;
      const res = await fetch(API_BASE + path, {
        method,
        headers,
        body: body != null ? JSON.stringify(body) : undefined,
      });
      if (res.status === 401) {
        const txt = await res.text().catch(() => "");
        // session_revoked é tratado pela UI; token expirado desloga.
        if (!txt.includes("session_revoked")) {
          this.token = "";
          window.dispatchEvent(new CustomEvent("tgweb:logout"));
        }
        const err = new Error(txt || "Não autenticado");
        err.status = 401;
        err.body = txt;
        throw err;
      }
      if (!res.ok) {
        let detail = "";
        try { detail = (await res.json()).detail; } catch (e) { detail = await res.text(); }
        const err = new Error(detail || ("Erro " + res.status));
        err.status = res.status;
        throw err;
      }
      const ct = res.headers.get("content-type") || "";
      return ct.includes("application/json") ? res.json() : res.text();
    },

    get(p) { return this.request("GET", p); },
    post(p, b) { return this.request("POST", p, b); },
    del(p) { return this.request("DELETE", p); },

    // ---- endpoints
    login(username, password) { return this.post("/api/login", { username, password }); },
    tgStatus() { return this.get("/api/telegram/status"); },
    tgCredentials(api_id, api_hash) { return this.post("/api/telegram/credentials", { api_id, api_hash }); },
    tgSendCode(phone) { return this.post("/api/telegram/send-code", { phone }); },
    tgSignIn(code) { return this.post("/api/telegram/sign-in", { code }); },
    tgPassword(password) { return this.post("/api/telegram/password", { password }); },
    tgLogout() { return this.post("/api/telegram/logout", {}); },
    tgDialogs() { return this.get("/api/telegram/dialogs"); },

    courses() { return this.get("/api/courses"); },
    addCourses(courses) { return this.post("/api/courses/add", { courses }); },
    deleteCourse(id) { return this.del("/api/courses/" + id); },
    syncCourse(id, limit) { return this.post("/api/courses/" + id + "/sync", { limit }); },
    subjects(id) { return this.get("/api/courses/" + id + "/subjects"); },
    videos(id) { return this.get("/api/courses/" + id + "/videos"); },

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
