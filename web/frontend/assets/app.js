/* ============================================================ TgPlayer Web SPA
   Vanilla JS, sem build. Render simples por views. Estado em memória.
   ============================================================ */
(function () {
  const app = document.getElementById("app");
  const State = {
    view: "dashboard",
    courses: [],
    currentCourse: null,
    subjects: [],
    videos: [],
    activeSubject: "all",
    search: "",
    tg: { connected: false, me: null, has_credentials: false },
  };

  // ---------------------------------------------------------------- helpers
  const $ = (sel, el = document) => el.querySelector(sel);
  const h = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function toast(msg, type = "") {
    const box = document.getElementById("toasts");
    const t = h(`<div class="toast ${type}">${esc(msg)}</div>`);
    box.appendChild(t);
    setTimeout(() => { t.style.opacity = "0"; t.style.transform = "translateX(40px)"; t.style.transition = "0.3s"; }, 3200);
    setTimeout(() => t.remove(), 3600);
  }

  function fmtDuration(sec) {
    sec = Math.round(sec || 0);
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    if (h) return `${h}h${String(m).padStart(2, "0")}`;
    if (m) return `${m}min`;
    return `${s}s`;
  }
  function fmtSize(bytes) {
    if (!bytes) return "";
    const u = ["B", "KB", "MB", "GB"]; let i = 0; let n = bytes;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(n < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
  }
  function fmtHrs(sec) { return (sec / 3600).toFixed(1).replace(".0", "") + "h"; }
  function kindLabel(c) {
    if (c.is_forum) return "🗂️ Fórum";
    if ((c.chat_type || "").toUpperCase() === "CHANNEL") return "📢 Canal";
    return "👥 Grupo";
  }

  // ---------------------------------------------------------------- theme
  function initTheme() {
    const saved = localStorage.getItem("tgweb_theme") || "dark";
    document.body.dataset.theme = saved;
  }
  function toggleTheme() {
    const next = document.body.dataset.theme === "dark" ? "light" : "dark";
    document.body.dataset.theme = next;
    localStorage.setItem("tgweb_theme", next);
  }

  // ================================================================ LOGIN
  function renderLogin(errMsg) {
    app.innerHTML = "";
    const card = h(`
      <div class="login-wrap">
        <div class="login-card">
          <div class="login-logo">▶</div>
          <h1>TgPlayer</h1>
          <p class="sub">Suas videoaulas do Telegram, organizadas e lindas.</p>
          ${errMsg ? `<div class="login-err">${esc(errMsg)}</div>` : ""}
          <form id="loginForm">
            <div class="field">
              <label>Usuário</label>
              <input id="lu" type="text" autocomplete="username" placeholder="seu usuário" required />
            </div>
            <div class="field">
              <label>Senha</label>
              <input id="lp" type="password" autocomplete="current-password" placeholder="••••••••" required />
            </div>
            <button class="btn btn-primary btn-block btn-lg" type="submit">Entrar</button>
          </form>
          <p class="login-hint">Acesso restrito · login e senha definidos por você.</p>
        </div>
      </div>`);
    app.appendChild(card);
    $("#loginForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = $("button", card); btn.disabled = true; btn.textContent = "Entrando...";
      try {
        const r = await api.login($("#lu").value.trim(), $("#lp").value);
        api.token = r.token;
        await boot();
      } catch (err) {
        renderLogin(err.message || "Falha no login");
      }
    });
  }

  // ================================================================ SHELL
  const NAV = [
    { id: "dashboard", ic: "📊", label: "Acompanhamento" },
    { id: "courses", ic: "🎬", label: "Aulas" },
    { id: "files", ic: "🗂️", label: "Arquivos" },
  ];

  function renderShell() {
    app.innerHTML = "";
    const tg = State.tg;
    const shell = h(`
      <div class="shell">
        <aside class="sidebar" id="sidebar">
          <div class="brand">
            <div class="brand-mark">▶</div>
            <div class="brand-name">TgPlayer<small>Web Player</small></div>
          </div>
          <nav class="nav">
            ${NAV.map((n) => `<button class="nav-item ${State.view === n.id ? "active" : ""}" data-view="${n.id}"><span class="ic">${n.ic}</span>${n.label}</button>`).join("")}
          </nav>
          <div class="sidebar-foot">
            <div class="tg-status" id="tgStatus">
              <span class="tg-dot ${tg.connected ? "on" : "off"}"></span>
              <span class="name">${tg.connected ? esc(tg.me && (tg.me.first_name || tg.me.username) || "Conectado") : "Telegram desconectado"}</span>
              <span>⚙️</span>
            </div>
            <button class="nav-item" id="themeBtn"><span class="ic">🌓</span>Alternar tema</button>
            <button class="nav-item" id="logoutBtn"><span class="ic">🚪</span>Sair</button>
          </div>
        </aside>
        <div class="main">
          <header class="topbar">
            <button class="icon-btn menu-toggle" id="menuBtn">☰</button>
            <h2 id="pageTitle"></h2>
            <div class="spacer"></div>
            <div class="search-box" id="searchBox" style="display:none">
              <span>🔍</span>
              <input id="searchInput" placeholder="Buscar aula..." />
            </div>
            <button class="icon-btn" id="themeBtnTop" title="Tema">🌓</button>
          </header>
          <main class="content" id="content"></main>
        </div>
      </div>`);
    app.appendChild(shell);

    shell.querySelectorAll(".nav-item[data-view]").forEach((b) =>
      b.addEventListener("click", () => { State.view = b.dataset.view; renderView(); }));
    $("#themeBtn").addEventListener("click", toggleTheme);
    $("#themeBtnTop").addEventListener("click", toggleTheme);
    $("#logoutBtn").addEventListener("click", () => { api.token = ""; renderLogin(); });
    $("#tgStatus").addEventListener("click", openTelegramModal);
    $("#menuBtn").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
    const si = $("#searchInput");
    if (si) si.addEventListener("input", (e) => { State.search = e.target.value.toLowerCase(); renderLessons(); });
    renderView();
  }

  function setTitle(t, showSearch) {
    $("#pageTitle").textContent = t;
    const sb = $("#searchBox"); if (sb) sb.style.display = showSearch ? "flex" : "none";
    document.querySelectorAll(".nav-item[data-view]").forEach((b) =>
      b.classList.toggle("active", b.dataset.view === State.view));
  }

  function renderView() {
    if ($("#sidebar")) $("#sidebar").classList.remove("open");
    if (State.view === "dashboard") renderDashboard();
    else if (State.view === "courses") renderCourses();
    else if (State.view === "files") renderFiles();
  }

  function loading(el) { el.innerHTML = `<div class="spinner"></div>`; }

  // ================================================================ DASHBOARD
  let pomoTimer = null, pomoLeft = 25 * 60, pomoRunning = false;

  async function renderDashboard() {
    setTitle("Acompanhamento", false);
    const c = $("#content"); loading(c);
    let data, tasks;
    try { data = await api.dashboard(); tasks = await api.tasks(); }
    catch (e) { c.innerHTML = `<div class="empty"><div class="big">📊</div><h3>Não foi possível carregar</h3><p class="muted">${esc(e.message)}</p></div>`; return; }

    const goalSec = data.weekly_goal_hours * 3600;
    const goalPct = goalSec ? Math.min(100, Math.round(data.week_seconds / goalSec * 100)) : 0;
    const maxDay = Math.max(1, ...data.by_day.map((d) => d.seconds));
    const dayNames = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"];

    c.innerHTML = `
      <div class="metrics">
        <div class="metric"><div class="label">⏱️ Hoje</div><div class="value">${fmtHrs(data.today_seconds)}</div></div>
        <div class="metric"><div class="label">📅 Semana</div><div class="value">${fmtHrs(data.week_seconds)} <small>/ ${data.weekly_goal_hours}h</small></div></div>
        <div class="metric"><div class="label">🔥 Sequência</div><div class="value">${data.streak_days} <small>dias</small></div></div>
        <div class="metric"><div class="label">✅ Aulas</div><div class="value">${data.videos_done} <small>/ ${data.videos_total}</small></div></div>
        <div class="metric"><div class="label">🍅 Pomodoros hoje</div><div class="value">${data.pomodoros_today}</div></div>
      </div>

      <div class="panel" style="margin-bottom:18px">
        <div class="row between">
          <div class="section-title" style="margin:0">🎯 Meta semanal <span class="pill">${goalPct}%</span></div>
          <button class="btn btn-sm" id="editGoal">Editar meta</button>
        </div>
        <div class="progress-bar" style="height:10px;margin-top:14px"><div class="fill" style="width:${goalPct}%"></div></div>
      </div>

      <div class="panels-2">
        <div class="panel">
          <div class="section-title" style="margin-top:0">📈 Horas estudadas (7 dias)</div>
          <div class="bar-chart">
            ${data.by_day.map((d) => {
              const dt = new Date(d.day + "T00:00:00");
              const pct = Math.round(d.seconds / maxDay * 100);
              return `<div class="bar-col">
                <div class="bar-val">${d.seconds ? fmtHrs(d.seconds) : ""}</div>
                <div class="bar" style="height:${Math.max(4, pct)}%" title="${fmtHrs(d.seconds)}"></div>
                <div class="bar-label">${dayNames[dt.getDay()]}</div>
              </div>`;
            }).join("")}
          </div>
        </div>
        <div class="panel pomodoro">
          <div class="section-title" style="margin-top:0;justify-content:center">🍅 Estudar agora</div>
          <div class="pomo-time" id="pomoTime">25:00</div>
          <div class="pomo-controls">
            <button class="btn btn-primary" id="pomoStart">▶ Iniciar</button>
            <button class="btn" id="pomoReset">Resetar</button>
          </div>
          <p class="muted mt" style="font-size:13px">Sessão de foco de 25 min. Ao concluir, conta no seu progresso.</p>
        </div>
      </div>

      <div class="panels-2 mt">
        <div class="panel">
          <div class="section-title" style="margin-top:0">📚 Progresso por curso</div>
          <div id="courseStats">
            ${data.by_course.length ? data.by_course.map((s) => `
              <div class="course-stat">
                <div class="top"><span>${esc(s.title)}</span><span class="pct">${s.done}/${s.total} · ${s.pct}%</span></div>
                <div class="progress-bar"><div class="fill" style="width:${s.pct}%"></div></div>
              </div>`).join("") : `<p class="muted">Nenhum curso ainda. Vá em <b>Aulas</b> para adicionar.</p>`}
          </div>
        </div>
        <div class="panel">
          <div class="row between"><div class="section-title" style="margin-top:0">📝 Tarefas</div></div>
          <form id="taskForm" class="row" style="margin:6px 0 12px">
            <input id="taskInput" class="field" style="margin:0;flex:1" placeholder="Nova tarefa..." />
            <button class="btn btn-primary btn-sm" type="submit">+</button>
          </form>
          <div id="taskList">
            ${tasks.length ? tasks.map((t) => taskRow(t)).join("") : `<p class="muted">Sem tarefas. Adicione uma! ✨</p>`}
          </div>
        </div>
      </div>

      <div class="panel mt">
        <div class="section-title" style="margin-top:0">🕘 Atividade recente</div>
        ${data.recent.length ? data.recent.map((r) => `
          <div class="lesson-row" style="margin-bottom:8px">
            <div class="lesson-thumb watched">✓</div>
            <div class="lesson-info"><div class="t">${esc(r.title)}</div><div class="m"><span>${esc(r.course_title || "")}</span></div></div>
          </div>`).join("") : `<p class="muted">Comece a assistir para ver sua atividade aqui.</p>`}
      </div>`;

    // pomodoro
    updatePomoUI();
    $("#pomoStart").addEventListener("click", togglePomodoro);
    $("#pomoReset").addEventListener("click", () => { stopPomodoro(); pomoLeft = 25 * 60; updatePomoUI(); });
    $("#editGoal").addEventListener("click", async () => {
      const v = prompt("Meta de horas por semana:", data.weekly_goal_hours);
      if (v && !isNaN(v)) { await api.setGoal(parseFloat(v)); toast("Meta atualizada ✓", "ok"); renderDashboard(); }
    });
    // tasks
    $("#taskForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const txt = $("#taskInput").value.trim(); if (!txt) return;
      await api.addTask(txt, 1, State.currentCourse ? State.currentCourse.id : null);
      renderDashboard();
    });
    c.querySelectorAll("[data-task]").forEach((el) => {
      el.querySelector(".task-ck").addEventListener("click", async () => { await api.toggleTask(+el.dataset.task); renderDashboard(); });
      el.querySelector(".task-del").addEventListener("click", async () => { await api.deleteTask(+el.dataset.task); renderDashboard(); });
    });
  }

  function taskRow(t) {
    return `<div class="task-row ${t.done ? "done" : ""}" data-task="${t.id}">
      <div class="task-ck ${t.done ? "done" : ""}">${t.done ? "✓" : ""}</div>
      <div class="txt">${esc(t.text)}</div>
      <button class="task-del icon-btn btn-ghost" style="width:auto;height:auto;background:none;border:none">🗑️</button>
    </div>`;
  }

  function updatePomoUI() {
    const el = $("#pomoTime"); if (!el) return;
    const m = Math.floor(pomoLeft / 60), s = pomoLeft % 60;
    el.textContent = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    const btn = $("#pomoStart"); if (btn) btn.textContent = pomoRunning ? "⏸ Pausar" : "▶ Iniciar";
  }
  function togglePomodoro() { pomoRunning ? stopPomodoro() : startPomodoro(); updatePomoUI(); }
  function startPomodoro() {
    pomoRunning = true;
    pomoTimer = setInterval(async () => {
      pomoLeft--;
      if (pomoLeft <= 0) {
        stopPomodoro();
        try { await api.logPomodoro(25, State.currentCourse ? State.currentCourse.id : null); } catch (e) {}
        toast("🍅 Pomodoro concluído! +25min", "ok");
        pomoLeft = 25 * 60;
        if (State.view === "dashboard") renderDashboard();
      }
      updatePomoUI();
    }, 1000);
  }
  function stopPomodoro() { pomoRunning = false; if (pomoTimer) clearInterval(pomoTimer); pomoTimer = null; }

  // ================================================================ COURSES
  async function renderCourses() {
    if (State.currentCourse) return renderLessons();
    setTitle("Aulas", false);
    const c = $("#content"); loading(c);
    try { State.courses = await api.courses(); }
    catch (e) { c.innerHTML = errorBox(e); return; }

    if (!State.courses.length) {
      c.innerHTML = `
        <div class="empty">
          <div class="big">🎬</div>
          <h3>Nenhum curso ainda</h3>
          <p class="muted">Adicione grupos/canais do seu Telegram para começar.</p>
          <button class="btn btn-primary btn-lg mt" id="addBtn">➕ Adicionar cursos do Telegram</button>
        </div>`;
      $("#addBtn").addEventListener("click", openAddCoursesModal);
      return;
    }

    c.innerHTML = `
      <div class="row between" style="margin-bottom:18px">
        <p class="muted">${State.courses.length} curso(s) · clique para abrir</p>
        <button class="btn btn-primary" id="addBtn">➕ Adicionar cursos</button>
      </div>
      <div class="courses-grid">
        ${State.courses.map((co) => courseCard(co)).join("")}
      </div>`;
    $("#addBtn").addEventListener("click", openAddCoursesModal);
    c.querySelectorAll("[data-course]").forEach((el) => {
      const id = +el.dataset.course;
      el.querySelector(".open").addEventListener("click", () => openCourse(id));
      el.querySelector(".sync").addEventListener("click", (e) => { e.stopPropagation(); syncCourse(id); });
      el.querySelector(".del").addEventListener("click", async (e) => {
        e.stopPropagation();
        if (confirm("Excluir este curso e todas as aulas indexadas? (o conteúdo no Telegram não é afetado)")) {
          await api.deleteCourse(id); toast("Curso removido", "ok"); renderCourses();
        }
      });
    });
  }

  function courseCard(co) {
    const color = co.color || "#7c5cff";
    return `<div class="course-card" data-course="${co.id}">
      <div class="course-cover open" style="background:linear-gradient(135deg, ${color}, ${color}aa);cursor:pointer">
        <span class="kind">${kindLabel(co)}</span>
      </div>
      <div class="body">
        <div class="title open" style="cursor:pointer">${esc(co.title)}</div>
        <div class="meta">${co.total ? `${co.total} aulas · ${co.done} assistidas` : "sem sincronizar"}</div>
        ${co.total ? `<div class="progress-bar"><div class="fill" style="width:${co.pct}%"></div></div>` : ""}
        <div class="actions">
          <button class="btn btn-sm open" style="flex:1">▶ Abrir</button>
          <button class="btn btn-sm sync" title="Sincronizar">🔄</button>
          <button class="btn btn-sm del btn-danger" title="Excluir">🗑️</button>
        </div>
      </div>
    </div>`;
  }

  async function openCourse(id) {
    State.currentCourse = State.courses.find((c) => c.id === id);
    State.activeSubject = "all"; State.search = "";
    const c = $("#content"); loading(c);
    try {
      State.subjects = await api.subjects(id);
      State.videos = await api.videos(id);
    } catch (e) { c.innerHTML = errorBox(e); return; }
    renderLessons();
  }

  function renderLessons() {
    const co = State.currentCourse;
    setTitle(co.title, true);
    const c = $("#content");

    const counts = {};
    State.videos.forEach((v) => { const k = v.subject_id || 0; counts[k] = (counts[k] || 0) + 1; });

    let vids = State.videos;
    if (State.activeSubject !== "all") vids = vids.filter((v) => String(v.subject_id || 0) === String(State.activeSubject));
    if (State.search) vids = vids.filter((v) => (v.title || "").toLowerCase().includes(State.search) || (v.file_name || "").toLowerCase().includes(State.search));

    c.innerHTML = `
      <div class="row between" style="margin-bottom:16px">
        <button class="btn btn-sm btn-ghost" id="backBtn">← Voltar aos cursos</button>
        <div class="row">
          <span class="muted">${co.done}/${co.total} assistidas</span>
          <button class="btn btn-sm sync">🔄 Sincronizar</button>
        </div>
      </div>
      <div class="lessons-layout">
        <div class="panel">
          <div class="section-title" style="margin-top:0;font-size:14px">Matérias</div>
          <div class="subject-list">
            <button class="subject-item ${State.activeSubject === "all" ? "active" : ""}" data-subj="all">
              <span>📚 Todas</span><span class="count">${State.videos.length}</span>
            </button>
            ${State.subjects.map((s) => `
              <button class="subject-item ${String(State.activeSubject) === String(s.id) ? "active" : ""}" data-subj="${s.id}">
                <span>${esc(s.title)}</span><span class="count">${counts[s.id] || 0}</span>
              </button>`).join("")}
            ${counts[0] ? `<button class="subject-item ${State.activeSubject === "0" ? "active" : ""}" data-subj="0"><span>Sem matéria</span><span class="count">${counts[0]}</span></button>` : ""}
          </div>
        </div>
        <div>
          ${vids.length ? vids.map((v) => lessonRow(v)).join("") : `<div class="empty"><div class="big">🎞️</div><h3>Nenhuma aula aqui</h3><p class="muted">${State.videos.length ? "Tente outra matéria ou busca." : "Clique em 🔄 Sincronizar para buscar as aulas do Telegram."}</p></div>`}
        </div>
      </div>`;

    $("#backBtn").addEventListener("click", () => { State.currentCourse = null; renderCourses(); });
    c.querySelector(".sync").addEventListener("click", () => syncCourse(co.id));
    c.querySelectorAll("[data-subj]").forEach((b) => b.addEventListener("click", () => { State.activeSubject = b.dataset.subj; renderLessons(); }));
    c.querySelectorAll("[data-video]").forEach((el) => {
      const v = State.videos.find((x) => x.id === +el.dataset.video);
      el.querySelector(".play")?.addEventListener("click", () => openPlayer(v));
      el.querySelector(".tg")?.addEventListener("click", () => openInTelegram(v));
      el.querySelector(".watch")?.addEventListener("click", async () => {
        if (v.watched) { await api.markUnwatched(v.id); v.watched = false; }
        else { await api.markWatched(v.id); v.watched = true; }
        renderLessons();
      });
    });
  }

  function lessonRow(v) {
    const prog = Math.round((v.progress || 0) * 100);
    return `<div class="lesson-row" data-video="${v.id}">
      <div class="lesson-thumb ${v.watched ? "watched" : ""}">${v.watched ? "✓" : "🎬"}</div>
      <div class="lesson-info">
        <div class="t">${esc(v.title || v.file_name)}</div>
        <div class="m">
          ${v.duration ? `<span>⏱ ${fmtDuration(v.duration)}</span>` : ""}
          ${v.size ? `<span>💾 ${fmtSize(v.size)}</span>` : ""}
          ${v.width ? `<span>📐 ${v.width}×${v.height}</span>` : ""}
        </div>
        ${prog > 1 && prog < 99 ? `<div class="lesson-progress"><div class="fill" style="width:${prog}%"></div></div>` : ""}
      </div>
      <div class="lesson-actions">
        <button class="btn btn-sm btn-primary play" title="Assistir no navegador">▶</button>
        <button class="btn btn-sm tg" title="Abrir no Telegram (64Gram/Desktop)">📲</button>
        <button class="btn btn-sm watch" title="${v.watched ? "Marcar como não vista" : "Marcar como vista"}">${v.watched ? "↩" : "✓"}</button>
      </div>
    </div>`;
  }

  async function syncCourse(id) {
    toast("Sincronizando... isso pode levar um tempo", "");
    try {
      const r = await api.syncCourse(id, 99999);
      toast(`✅ ${r.videos} aula(s) · ${r.detected}`, "ok");
      State.courses = await api.courses();
      if (State.currentCourse && State.currentCourse.id === id) await openCourse(r.course_id || id);
      else renderCourses();
    } catch (e) {
      if (String(e.body || e.message).includes("session_revoked")) return handleSessionRevoked();
      toast("Erro ao sincronizar: " + e.message, "err");
    }
  }

  // ================================================================ FILES
  function renderFiles() {
    setTitle("Arquivos", false);
    const c = $("#content");
    c.innerHTML = `
      <div class="empty">
        <div class="big">🗂️</div>
        <h3>Arquivos dos seus chats</h3>
        <p class="muted">Em breve: navegue por todos os arquivos (PDF, áudios, imagens) dos seus cursos<br/>como uma nuvem. Por enquanto, use a aba <b>Aulas</b> para os vídeos.</p>
        <button class="btn btn-primary btn-lg mt" data-go="courses">Ir para Aulas</button>
      </div>`;
    c.querySelector("[data-go]").addEventListener("click", () => { State.view = "courses"; renderView(); });
  }

  function errorBox(e) {
    return `<div class="empty"><div class="big">⚠️</div><h3>Algo deu errado</h3><p class="muted">${esc(e.message)}</p></div>`;
  }

  // ================================================================ MODAL base
  function openModal(node) {
    const bk = h(`<div class="modal-backdrop"></div>`);
    bk.appendChild(node);
    bk.addEventListener("click", (e) => { if (e.target === bk) closeModal(bk); });
    document.body.appendChild(bk);
    const x = node.querySelector(".close-x"); if (x) x.addEventListener("click", () => closeModal(bk));
    return bk;
  }
  function closeModal(bk) {
    const v = bk.querySelector("video");
    if (v) { try { v.pause(); } catch (e) {} }
    bk.remove();
  }

  // ================================================================ PLAYER (web)
  async function openPlayer(v) {
    const modal = h(`
      <div class="modal wide">
        <div class="modal-head"><h3>${esc(v.title || v.file_name)}</h3><button class="close-x">✕</button></div>
        <div class="modal-body">
          <div class="player-wrap" id="pw"><div class="spinner"></div></div>
          <div class="player-meta">
            <div class="player-actions">
              <button class="btn tg">📲 Abrir no Telegram (64Gram/Desktop)</button>
              <button class="btn watch">${v.watched ? "↩ Marcar não vista" : "✓ Marcar como vista"}</button>
            </div>
            <p class="muted mt" style="font-size:13px">💡 O streaming roda pela sua conexão. Se preferir o app nativo, use o botão acima.</p>
          </div>
        </div>
      </div>`);
    const bk = openModal(modal);
    modal.querySelector(".tg").addEventListener("click", () => openInTelegram(v));
    modal.querySelector(".watch").addEventListener("click", async () => {
      if (v.watched) { await api.markUnwatched(v.id); v.watched = false; } else { await api.markWatched(v.id); v.watched = true; }
      toast(v.watched ? "Marcada como vista ✓" : "Marcada como não vista", "ok");
    });

    try {
      const s = await api.prepareStream(v.id);
      const url = api.streamUrl(s.token);
      const pw = modal.querySelector("#pw");
      pw.innerHTML = "";
      const video = h(`<video controls autoplay playsinline></video>`);
      video.src = url;
      pw.appendChild(video);
      // salva progresso a cada 10s
      let last = 0;
      video.addEventListener("timeupdate", () => {
        if (video.currentTime - last > 10) {
          last = video.currentTime;
          api.saveProgress(v.id, Math.round(video.currentTime * 1000), Math.round((video.duration || 0) * 1000)).catch(() => {});
        }
      });
      if (s.start_position_ms) video.currentTime = s.start_position_ms / 1000;
      video.addEventListener("ended", async () => { await api.markWatched(v.id); v.watched = true; toast("Aula concluída ✓", "ok"); });
    } catch (e) {
      if (String(e.body || e.message).includes("session_revoked")) { closeModal(bk); return handleSessionRevoked(); }
      modal.querySelector("#pw").innerHTML = `<div style="padding:40px;text-align:center;color:#fff">⚠️ ${esc(e.message)}<br/><small style="opacity:.7">Tente abrir no Telegram com o botão abaixo.</small></div>`;
    }
  }

  // ---- abrir no Telegram Desktop / 64Gram (deep link tg://)
  function openInTelegram(v) {
    if (v.tg_url) {
      // tg:// abre o app nativo (Telegram Desktop, 64Gram e derivados).
      window.location.href = v.tg_url;
      // fallback web depois de um tempo, caso o app não esteja instalado.
      if (v.tme_url) setTimeout(() => window.open(v.tme_url, "_blank"), 1200);
      toast("Abrindo no Telegram...", "");
    } else if (v.tme_url) {
      window.open(v.tme_url, "_blank");
    } else {
      toast("Este chat é privado e não tem link direto. Use o player web.", "err");
    }
  }

  // ================================================================ TELEGRAM modal
  async function openTelegramModal() {
    const st = State.tg;
    const modal = h(`
      <div class="modal">
        <div class="modal-head"><h3>📲 Conta do Telegram</h3><button class="close-x">✕</button></div>
        <div class="modal-body" id="tgBody"></div>
      </div>`);
    const bk = openModal(modal);
    const body = modal.querySelector("#tgBody");

    if (st.connected) {
      body.innerHTML = `
        <div class="row" style="gap:14px;margin-bottom:18px">
          <div class="brand-mark" style="width:52px;height:52px">👤</div>
          <div><div style="font-weight:700;font-size:16px">${esc((st.me && (st.me.first_name || st.me.username)) || "Conectado")}</div>
          <div class="muted" style="font-size:13px">Sua conta está conectada ✓</div></div>
        </div>
        <button class="btn btn-danger btn-block" id="tgOut">Desconectar do Telegram</button>`;
      body.querySelector("#tgOut").addEventListener("click", async () => {
        await api.tgLogout(); toast("Desconectado", "ok"); State.tg.connected = false; closeModal(bk); refreshTgStatus();
      });
      return;
    }

    // fluxo de login: credenciais -> telefone -> código -> (senha 2FA)
    renderTgStep(body, bk, st.has_credentials ? "phone" : "creds");
  }

  function renderTgStep(body, bk, step, ctx = {}) {
    if (step === "creds") {
      body.innerHTML = `
        <p class="muted" style="margin-bottom:16px;font-size:13.5px">Conecte sua conta usando seu <b>API ID</b> e <b>API HASH</b> (pegue em <a href="https://my.telegram.org" target="_blank" style="color:var(--brand)">my.telegram.org</a>).</p>
        <div class="field"><label>API ID</label><input id="apiId" placeholder="1234567" /></div>
        <div class="field"><label>API HASH</label><input id="apiHash" placeholder="abcdef0123..." /></div>
        <button class="btn btn-primary btn-block" id="next">Continuar</button>`;
      body.querySelector("#next").addEventListener("click", async () => {
        const id = body.querySelector("#apiId").value.trim(), hash = body.querySelector("#apiHash").value.trim();
        if (!id || !hash) return toast("Preencha API ID e API HASH", "err");
        try {
          const r = await api.tgCredentials(id, hash);
          if (r.authorized) { toast("Conectado ✓", "ok"); closeModal(bk); refreshTgStatus(); return; }
          renderTgStep(body, bk, "phone");
        } catch (e) { toast(e.message, "err"); }
      });
    } else if (step === "phone") {
      body.innerHTML = `
        <p class="muted" style="margin-bottom:16px;font-size:13.5px">Informe o telefone com DDI da sua conta Telegram.</p>
        <div class="field"><label>Telefone</label><input id="phone" placeholder="+5547999999999" value="+55" /></div>
        <button class="btn btn-primary btn-block" id="next">Enviar código</button>
        <p class="login-hint">Para evitar bloqueios, peça o código só uma vez.</p>`;
      body.querySelector("#next").addEventListener("click", async () => {
        const phone = body.querySelector("#phone").value.trim();
        try {
          const r = await api.tgSendCode(phone);
          if (r.flood_wait) return toast(`Aguarde ${Math.ceil(r.flood_wait / 60)} min antes de pedir outro código.`, "err");
          renderTgStep(body, bk, "code");
        } catch (e) { toast(e.message, "err"); }
      });
    } else if (step === "code") {
      body.innerHTML = `
        <p class="muted" style="margin-bottom:16px;font-size:13.5px">Digite o código enviado no seu Telegram. Nunca compartilhe esse código.</p>
        <div class="field"><label>Código</label><input id="code" placeholder="1 2 3 4 5" inputmode="numeric" /></div>
        <button class="btn btn-primary btn-block" id="next">Confirmar</button>`;
      body.querySelector("#next").addEventListener("click", async () => {
        try {
          const r = await api.tgSignIn(body.querySelector("#code").value.trim());
          if (r.needs_password) return renderTgStep(body, bk, "password");
          if (r.authorized) { toast("Conectado ✓", "ok"); closeModal(bk); refreshTgStatus(); }
        } catch (e) { toast(e.message, "err"); }
      });
    } else if (step === "password") {
      body.innerHTML = `
        <p class="muted" style="margin-bottom:16px;font-size:13.5px">Sua conta tem verificação em duas etapas. Digite sua senha 2FA.</p>
        <div class="field"><label>Senha 2FA</label><input id="pw2" type="password" /></div>
        <button class="btn btn-primary btn-block" id="next">Confirmar</button>`;
      body.querySelector("#next").addEventListener("click", async () => {
        try {
          const r = await api.tgPassword(body.querySelector("#pw2").value);
          if (r.authorized) { toast("Conectado ✓", "ok"); closeModal(bk); refreshTgStatus(); }
        } catch (e) { toast(e.message, "err"); }
      });
    }
  }

  // ================================================================ ADD COURSES
  async function openAddCoursesModal() {
    if (!State.tg.connected) { toast("Conecte sua conta do Telegram primeiro", "err"); return openTelegramModal(); }
    const modal = h(`
      <div class="modal">
        <div class="modal-head"><h3>➕ Adicionar cursos</h3><button class="close-x">✕</button></div>
        <div class="modal-body"><div class="spinner"></div></div>
        <div class="modal-foot"><button class="btn" id="cancel">Cancelar</button><button class="btn btn-primary" id="confirm" disabled>Adicionar (0)</button></div>
      </div>`);
    const bk = openModal(modal);
    const body = modal.querySelector(".modal-body");
    modal.querySelector("#cancel").addEventListener("click", () => closeModal(bk));

    let dialogs = [];
    try { const r = await api.tgDialogs(); dialogs = r.courses || []; }
    catch (e) {
      if (String(e.body || e.message).includes("session_revoked")) { closeModal(bk); return handleSessionRevoked(); }
      body.innerHTML = errorBox(e); return;
    }

    const selected = new Set();
    body.innerHTML = `
      <input class="field" id="filter" placeholder="🔍 Filtrar grupos/canais..." style="margin-bottom:12px" />
      <div class="dialog-list">
        ${dialogs.map((d, i) => `
          <div class="dialog-opt" data-i="${i}">
            <div class="ck"></div>
            <div class="t">${esc(d.title)}</div>
            <span class="badge">${d.is_forum ? "Fórum" : (d.chat_type === "CHANNEL" ? "Canal" : "Grupo")}</span>
          </div>`).join("")}
      </div>`;

    const confirmBtn = modal.querySelector("#confirm");
    function refreshConfirm() { confirmBtn.textContent = `Adicionar (${selected.size})`; confirmBtn.disabled = selected.size === 0; }
    body.querySelectorAll(".dialog-opt").forEach((el) => {
      el.addEventListener("click", () => {
        const i = +el.dataset.i;
        if (selected.has(i)) { selected.delete(i); el.classList.remove("sel"); el.querySelector(".ck").textContent = ""; }
        else { selected.add(i); el.classList.add("sel"); el.querySelector(".ck").textContent = "✓"; }
        refreshConfirm();
      });
    });
    body.querySelector("#filter").addEventListener("input", (e) => {
      const q = e.target.value.toLowerCase();
      body.querySelectorAll(".dialog-opt").forEach((el) => {
        const d = dialogs[+el.dataset.i];
        el.style.display = d.title.toLowerCase().includes(q) ? "flex" : "none";
      });
    });
    confirmBtn.addEventListener("click", async () => {
      const chosen = [...selected].map((i) => dialogs[i]);
      await api.addCourses(chosen);
      toast(`${chosen.length} curso(s) adicionado(s) ✓`, "ok");
      closeModal(bk);
      State.courses = await api.courses();
      renderCourses();
    });
  }

  // ================================================================ helpers globais
  async function refreshTgStatus() {
    try { State.tg = await api.tgStatus(); } catch (e) {}
    if ($("#tgStatus")) renderShell();
  }

  function handleSessionRevoked() {
    State.tg.connected = false;
    toast("Sua sessão do Telegram expirou. Conecte novamente.", "err");
    openTelegramModal();
  }

  // ================================================================ BOOT
  async function boot() {
    try { State.tg = await api.tgStatus(); }
    catch (e) { if (e.status === 401) return renderLogin(); State.tg = { connected: false, has_credentials: false }; }
    renderShell();
  }

  window.addEventListener("tgweb:logout", () => renderLogin());

  initTheme();
  if (api.token) boot(); else renderLogin();
})();
