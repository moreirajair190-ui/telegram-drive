/* ============================================================ TgPlayer Web SPA
   Vanilla JS, sem build. Render simples por views. Estado em memória.
   ============================================================ */
(function () {
  const app = document.getElementById("app");
  const State = {
    view: "dashboard",
    user: null,
    courses: [],
    currentCourse: null,
    subjects: [],
    videos: [],
    tree: null,          // árvore de pastas/subpastas do curso atual
    openFolders: null,   // Set com os caminhos de pastas abertas (por curso)
    activeSubject: "all",
    search: "",
    tg: { connected: false, me: null, has_credentials: false },
    // Cache leve em memória para evitar refetch (performance).
    _treeCache: {},
  };

  // Velocidade de reprodução preferida (persistida).
  let playbackRate = parseFloat(localStorage.getItem("tgweb_rate") || "1") || 1;

  // debounce util — evita re-render a cada tecla na busca (anti-travamento).
  function debounce(fn, ms) {
    let t = null;
    return function (...args) {
      if (t) clearTimeout(t);
      t = setTimeout(() => { t = null; fn.apply(this, args); }, ms);
    };
  }

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
  function fmtBytes(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + " B";
    const units = ["KB", "MB", "GB", "TB"];
    let i = -1;
    do { n /= 1024; i++; } while (n >= 1024 && i < units.length - 1);
    return n.toFixed(n >= 10 || i === 0 ? 0 : 1) + " " + units[i];
  }
  function kindLabel(c) {
    if (c.is_forum) return "🗂️ Fórum";
    if ((c.chat_type || "").toUpperCase() === "CHANNEL") return "📢 Canal";
    return "👥 Grupo";
  }

  // ---------------------------------------------------------------- theme
  function initTheme() {
    const saved = localStorage.getItem("tgweb_theme") || "light";
    document.body.dataset.theme = saved;
  }
  function toggleTheme() {
    const next = document.body.dataset.theme === "dark" ? "light" : "dark";
    document.body.dataset.theme = next;
    localStorage.setItem("tgweb_theme", next);
  }

  // ================================================================ LOGIN
  function renderLogin(errMsg, st) {
    const canRegister = !st || st.registration_open !== false;
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
              <label>E-mail</label>
              <input id="lu" type="email" autocomplete="username" placeholder="voce@exemplo.com" required />
            </div>
            <div class="field">
              <label>Senha</label>
              <input id="lp" type="password" autocomplete="current-password" placeholder="••••••••" required />
            </div>
            <button class="btn btn-primary btn-block btn-lg" type="submit">Entrar</button>
          </form>
          ${canRegister ? `<p class="login-hint">Não tem conta ainda? <a href="#" id="goSetup">Criar conta</a></p>` : ""}
        </div>
      </div>`);
    app.appendChild(card);
    const goSetup = $("#goSetup");
    if (goSetup) goSetup.addEventListener("click", (e) => { e.preventDefault(); renderRegister(); });
    $("#loginForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = $("#loginForm button[type=submit]"); btn.disabled = true; btn.textContent = "Entrando...";
      try {
        const r = await api.login($("#lu").value.trim(), $("#lp").value);
        api.token = r.token;
        State.user = r.user || null;
        await boot();
      } catch (err) {
        renderLogin(err.message || "Falha no login");
      }
    });
  }

  // ================================================================ CRIAR CONTA
  // Cadastro apenas com e-mail + senha. Os dados do Telegram (API ID/HASH) são
  // informados DEPOIS, já logado, na conexão da conta — cada usuário usa a SUA
  // própria conta Telegram e suas credenciais são SEMPRE cifradas no servidor.
  function renderRegister(errMsg) {
    app.innerHTML = "";
    const card = h(`
      <div class="login-wrap">
        <div class="login-card">
          <div class="login-logo">▶</div>
          <h1>Criar sua conta</h1>
          <p class="sub">Crie seu acesso à plataforma. Depois você conectará sua própria conta do Telegram.</p>
          ${errMsg ? `<div class="login-err">${esc(errMsg)}</div>` : ""}
          <form id="setupForm">
            <div class="field">
              <label>E-mail</label>
              <input id="su" type="email" autocomplete="username" placeholder="voce@exemplo.com" required />
            </div>
            <div class="field">
              <label>Senha (mín. 8 caracteres, com letras e números)</label>
              <input id="sp" type="password" autocomplete="new-password" placeholder="••••••••" minlength="8" required />
            </div>
            <div class="field">
              <label>Confirmar senha</label>
              <input id="sp2" type="password" autocomplete="new-password" placeholder="••••••••" minlength="8" required />
            </div>
            <button class="btn btn-primary btn-block btn-lg" type="submit">Criar conta e entrar</button>
          </form>
          <p class="login-hint">Já tem conta? <a href="#" id="goLogin">Fazer login</a></p>
        </div>
      </div>`);
    app.appendChild(card);
    $("#goLogin").addEventListener("click", (e) => { e.preventDefault(); renderLogin(); });
    $("#setupForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const u = $("#su").value.trim();
      const p = $("#sp").value;
      const p2 = $("#sp2").value;
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(u)) return renderRegister("E-mail inválido.");
      if (p.length < 8) return renderRegister("Senha precisa ter ao menos 8 caracteres.");
      if (p !== p2) return renderRegister("As senhas não coincidem.");
      const btn = $("#setupForm button[type=submit]"); btn.disabled = true; btn.textContent = "Criando...";
      try {
        const r = await api.register(u, p);
        api.token = r.token;
        State.user = r.user || null;
        toast("Conta criada! Agora conecte seu Telegram.", "ok");
        await boot();
      } catch (err) {
        renderRegister(err.message || "Falha ao criar conta");
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
            ${State.user && State.user.is_admin ? `<button class="nav-item ${State.view === "admin" ? "active" : ""}" data-view="admin"><span class="ic">🛡️</span>Administração</button>` : ""}
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
    if (si) {
      const onSearch = debounce(() => { renderLessons(); }, 180);
      si.addEventListener("input", (e) => { State.search = e.target.value.toLowerCase(); onSearch(); });
    }
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
    // Sair de um curso ao trocar de aba (evita estado preso).
    if (State.view !== "courses") State.currentCourse = null;
    if (State.view === "dashboard") renderDashboard();
    else if (State.view === "courses") renderCourses();
    else if (State.view === "files") renderFiles();
    else if (State.view === "admin") renderAdmin();
  }

  // ================================================================ ADMIN
  // Painel administrativo. Mostra APENAS dados não sensíveis: conta conectada,
  // última sincronização, status, contagem de arquivos e espaço usado.
  // NUNCA exibe API_ID, API_HASH, session string, telefone ou tokens.
  async function renderAdmin() {
    setTitle("Administração", false);
    const c = $("#content"); loading(c);
    if (!(State.user && State.user.is_admin)) {
      c.innerHTML = `<div class="empty"><div class="big">🔒</div><h3>Acesso restrito</h3><p class="muted">Você não tem permissão para ver esta página.</p></div>`;
      return;
    }
    let data;
    try { data = await api.adminOverview(); }
    catch (e) { c.innerHTML = `<div class="empty"><div class="big">🛡️</div><h3>Não foi possível carregar</h3><p class="muted">${esc(e.message)}</p></div>`; return; }

    const statusLabel = (s, connected) => connected ? "🟢 Conectada" : (s === "pending" ? "🟡 Pendente" : "⚪ Desconectada");

    const rows = data.users.map((u) => {
      const accs = (u.accounts || []);
      const accHtml = accs.length
        ? accs.map((a) => `
            <div class="admin-acc">
              <div class="admin-acc-head">
                <b>${esc(a.label || a.tg_username || a.tg_first_name || ("Conta #" + a.id))}</b>
                <span class="badge">${statusLabel(a.status, a.connected)}</span>
              </div>
              <div class="admin-acc-meta muted">
                <span>📁 ${a.files || 0} arquivos</span>
                <span>💾 ${fmtBytes(a.bytes_used || 0)}</span>
                <span>🔄 ${a.last_sync_at ? esc(a.last_sync_at) : "nunca sincronizado"}</span>
              </div>
            </div>`).join("")
        : `<div class="muted" style="font-size:13px">Nenhuma conta do Telegram conectada.</div>`;
      return `
        <div class="admin-card">
          <div class="admin-user-head">
            <div>
              <div style="font-weight:700">${esc(u.email)} ${u.is_admin ? '<span class="badge">admin</span>' : ""}</div>
              <div class="muted" style="font-size:12.5px">Criado: ${esc(u.created_at || "—")} • Último acesso: ${esc(u.last_login_at || "—")}</div>
            </div>
            <label class="admin-toggle">
              <input type="checkbox" data-uid="${u.id}" ${u.is_active ? "checked" : ""} ${u.is_admin ? "disabled" : ""} />
              <span>${u.is_active ? "Ativo" : "Inativo"}</span>
            </label>
          </div>
          <div class="admin-accs">${accHtml}</div>
        </div>`;
    }).join("");

    c.innerHTML = `
      <div class="metrics">
        <div class="metric"><div class="label">👥 Usuários</div><div class="value">${data.users_count}</div></div>
        <div class="metric"><div class="label">📲 Contas Telegram</div><div class="value">${data.accounts_count}</div></div>
      </div>
      <div class="admin-note muted" style="margin:8px 0 18px;font-size:13px">
        🔐 Por privacidade, este painel <b>não</b> exibe API ID, API HASH, session string, telefone ou tokens. Esses dados ficam cifrados no servidor.
      </div>
      <div class="admin-list">${rows || '<div class="empty"><div class="big">👤</div><h3>Sem usuários</h3></div>'}</div>`;

    c.querySelectorAll(".admin-toggle input[data-uid]").forEach((cb) => {
      cb.addEventListener("change", async () => {
        const uid = Number(cb.dataset.uid);
        try {
          await api.adminSetActive(uid, cb.checked);
          toast(cb.checked ? "Usuário ativado" : "Usuário desativado", "ok");
          renderAdmin();
        } catch (e) { toast(e.message, "err"); cb.checked = !cb.checked; }
      });
    });
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
    State.openFolders = new Set();
    const c = $("#content"); loading(c);
    try {
      // Usa cache quando disponível (performance) e revalida em segundo plano.
      const cached = State._treeCache[id];
      if (cached) {
        State.tree = cached;
        renderLessons();
        api.tree(id).then((fresh) => {
          State._treeCache[id] = fresh; State.tree = fresh;
          if (State.currentCourse && State.currentCourse.id === id) renderLessons();
        }).catch(() => {});
        return;
      }
      State.tree = await api.tree(id);
      State._treeCache[id] = State.tree;
    } catch (e) { c.innerHTML = errorBox(e); return; }
    renderLessons();
  }

  // Conta vídeos visíveis após o filtro de busca, recursivamente.
  function _countMatching(nodes) {
    let n = 0;
    for (const node of nodes) {
      if (node.type === "video") { if (_matchSearch(node)) n++; }
      else n += _countMatching(node.nodes || []);
    }
    return n;
  }
  function _matchSearch(v) {
    if (!State.search) return true;
    const q = State.search;
    return (v.title || "").toLowerCase().includes(q) || (v.file_name || "").toLowerCase().includes(q);
  }

  // Auto-expande as duas primeiras camadas na primeira abertura.
  function _autoExpand(nodes, path, depth) {
    if (depth > 1) return;
    for (let i = 0; i < nodes.length; i++) {
      const node = nodes[i];
      if (node.type !== "folder") continue;
      const p = path + "/" + i;
      if (depth === 0) State.openFolders.add(p);
      _autoExpand(node.nodes || [], p, depth + 1);
    }
  }

  function renderLessons() {
    const co = State.currentCourse;
    const tree = State.tree;
    setTitle(co.title, true);
    const c = $("#content");

    const total = (tree && tree.total) || 0;
    const done = (tree && tree.done) || 0;

    if (!tree || !tree.nodes || !tree.nodes.length) {
      c.innerHTML = `
        <div class="row between" style="margin-bottom:16px">
          <button class="btn btn-sm btn-ghost" id="backBtn">← Voltar aos cursos</button>
          <button class="btn btn-sm btn-primary sync">🔄 Sincronizar</button>
        </div>
        <div class="empty"><div class="big">🎞️</div><h3>Nenhuma aula ainda</h3>
        <p class="muted">Clique em <b>🔄 Sincronizar</b> para indexar as aulas e montar as pastas do Telegram.</p></div>`;
      $("#backBtn").addEventListener("click", () => { State.currentCourse = null; State.tree = null; renderCourses(); });
      c.querySelector(".sync").addEventListener("click", () => syncCourse(co.id));
      return;
    }

    // Na primeira render do curso, abre o primeiro nível.
    if (State.openFolders.size === 0 && !State.search) _autoExpand(tree.nodes, "", 0);

    const pct = total ? Math.round(done / total * 100) : 0;
    c.innerHTML = `
      <div class="lessons-head">
        <button class="btn btn-sm btn-ghost" id="backBtn">← Voltar aos cursos</button>
        <div class="lessons-head-right">
          <div class="course-progress-mini" title="${done} de ${total} aulas assistidas">
            <div class="cpm-bar"><div class="cpm-fill" style="width:${pct}%"></div></div>
            <span class="cpm-label">${done}/${total}</span>
          </div>
          <button class="btn btn-sm" id="expandAll" title="Expandir tudo">⊞</button>
          <button class="btn btn-sm" id="collapseAll" title="Recolher tudo">⊟</button>
          <button class="btn btn-sm btn-primary sync">🔄 Sincronizar</button>
        </div>
      </div>
      <div class="explorer" id="explorer">
        ${renderNodes(tree.nodes, "", 0)}
      </div>`;

    $("#backBtn").addEventListener("click", () => { State.currentCourse = null; State.tree = null; renderCourses(); });
    c.querySelector(".sync").addEventListener("click", () => syncCourse(co.id));
    $("#expandAll").addEventListener("click", () => { _setAllFolders(tree.nodes, "", true); renderLessons(); });
    $("#collapseAll").addEventListener("click", () => { State.openFolders.clear(); renderLessons(); });

    bindExplorer(c.querySelector("#explorer"));
  }

  // Marca todas as pastas (recursivo) como abertas/fechadas.
  function _setAllFolders(nodes, path, open) {
    nodes.forEach((node, i) => {
      if (node.type !== "folder") return;
      const p = path + "/" + i;
      if (open) State.openFolders.add(p); else State.openFolders.delete(p);
      _setAllFolders(node.nodes || [], p, open);
    });
  }

  // Renderiza nós (pasta/aula) recursivamente como uma árvore tipo Explorer.
  function renderNodes(nodes, path, depth) {
    let html = "";
    nodes.forEach((node, i) => {
      const p = path + "/" + i;
      if (node.type === "folder") {
        const matching = State.search ? _countMatching(node.nodes || []) : (node.count || 0);
        if (State.search && matching === 0) return; // some na busca
        const open = State.openFolders.has(p) || (State.search && matching > 0);
        const icon = open ? "📂" : "📁";
        html += `
          <div class="tree-folder" data-path="${p}" style="--depth:${depth}">
            <button class="folder-head ${open ? "open" : ""}" data-toggle="${p}">
              <span class="caret">${open ? "▾" : "▸"}</span>
              <span class="folder-icon">${icon}</span>
              <span class="folder-name">${esc(node.title)}</span>
              <span class="folder-count">${matching}</span>
            </button>
            <div class="folder-body" ${open ? "" : "hidden"}>
              ${open ? renderNodes(node.nodes || [], p, depth + 1) : ""}
            </div>
          </div>`;
      } else {
        if (!_matchSearch(node)) return;
        html += lessonRow(node, depth);
      }
    });
    return html;
  }

  function bindExplorer(root) {
    if (!root) return;
    // Toggle de pastas (event delegation evita N listeners — performance).
    root.addEventListener("click", (e) => {
      const head = e.target.closest("[data-toggle]");
      if (head) {
        const p = head.getAttribute("data-toggle");
        if (State.openFolders.has(p)) State.openFolders.delete(p);
        else State.openFolders.add(p);
        renderLessons();
        return;
      }
      const row = e.target.closest("[data-video]");
      if (!row) return;
      const id = +row.dataset.video;
      const v = _findVideoNode(State.tree.nodes, id);
      if (!v) return;
      if (e.target.closest(".play")) { openPlayer(v); }
      else if (e.target.closest(".tg")) { openInTelegram(v); }
      else if (e.target.closest(".watch")) { toggleWatchedNode(v); }
      else { openPlayer(v); } // clique na linha = assistir
    });
  }

  function _findVideoNode(nodes, id) {
    for (const node of nodes) {
      if (node.type === "video") { if (node.id === id) return node; }
      else { const f = _findVideoNode(node.nodes || [], id); if (f) return f; }
    }
    return null;
  }

  async function toggleWatchedNode(v) {
    const next = !v.watched;
    v.watched = next; // otimista
    // Atualiza só a linha sem re-render completo (anti-travamento).
    const row = document.querySelector(`.lesson-row[data-video="${v.id}"]`);
    if (row) {
      row.classList.toggle("done", next);
      const thumb = row.querySelector(".lesson-thumb");
      if (thumb) { thumb.classList.toggle("watched", next); thumb.textContent = next ? "✓" : "🎬"; }
      const wb = row.querySelector(".watch");
      if (wb) wb.textContent = next ? "↩" : "✓";
    }
    try { next ? await api.markWatched(v.id) : await api.markUnwatched(v.id); }
    catch (e) { v.watched = !next; toast("Não foi possível atualizar", "err"); }
  }

  function lessonRow(v, depth = 0) {
    const prog = Math.round((v.progress || 0) * 100);
    return `<div class="lesson-row ${v.watched ? "done" : ""}" data-video="${v.id}" style="--depth:${depth}">
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
    const overlay = openSyncOverlay("Sincronizando aulas…",
      "Buscando mensagens no Telegram e montando as pastas. Isso pode levar um tempo em canais grandes.");
    try {
      const r = await api.syncCourse(id, 99999);
      closeSyncOverlay(overlay);
      toast(`✅ ${r.videos} aula(s) sincronizada(s)`, "ok");
      // Invalida o cache da árvore deste curso.
      delete State._treeCache[r.course_id || id];
      State.courses = await api.courses();
      if (State.currentCourse && State.currentCourse.id === id) {
        State.openFolders = new Set();
        await openCourse(r.course_id || id);
      } else {
        renderCourses();
      }
    } catch (e) {
      closeSyncOverlay(overlay);
      if (String(e.body || e.message).includes("session_revoked")) return handleSessionRevoked();
      toast("Erro ao sincronizar: " + e.message, "err");
    }
  }

  // ---- Overlay animado de sincronização (feedback claro durante a espera) ----
  function openSyncOverlay(title, subtitle) {
    const node = h(`
      <div class="sync-overlay">
        <div class="sync-card">
          <div class="sync-anim">
            <div class="sync-ring"></div>
            <div class="sync-ring r2"></div>
            <div class="sync-ico">🔄</div>
          </div>
          <h3 class="sync-title">${esc(title)}</h3>
          <p class="sync-sub">${esc(subtitle || "")}</p>
          <div class="sync-bar"><div class="sync-bar-fill"></div></div>
          <p class="sync-steps" id="syncStep">Conectando…</p>
        </div>
      </div>`);
    document.body.appendChild(node);
    // Mensagens rotativas para dar sensação de progresso (sem travar).
    const steps = [
      "Conectando ao Telegram…",
      "Lendo o histórico do canal…",
      "Detectando o menu/sumário…",
      "Montando pastas e subpastas…",
      "Casando aulas pelas hashtags…",
      "Quase lá, finalizando…",
    ];
    let i = 0;
    const stepEl = node.querySelector("#syncStep");
    node._timer = setInterval(() => {
      i = Math.min(i + 1, steps.length - 1);
      if (stepEl) stepEl.textContent = steps[i];
    }, 2200);
    return node;
  }
  function closeSyncOverlay(node) {
    if (!node) return;
    if (node._timer) clearInterval(node._timer);
    node.classList.add("closing");
    setTimeout(() => node.remove(), 220);
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
  const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];

  async function openPlayer(v) {
    const modal = h(`
      <div class="modal wide player-modal">
        <div class="modal-head"><h3>${esc(v.title || v.file_name)}</h3><button class="close-x">✕</button></div>
        <div class="modal-body">
          <div class="player-wrap" id="pw">
            <div class="player-loading">
              <div class="spinner"></div>
              <p class="muted" id="plStatus">Preparando streaming…</p>
            </div>
          </div>
          <div class="player-meta">
            <div class="player-controls">
              <div class="speed-control" id="speedCtl" title="Velocidade de reprodução">
                <span class="speed-ico">⚡</span>
                <select id="speedSel">
                  ${SPEEDS.map((s) => `<option value="${s}" ${s === playbackRate ? "selected" : ""}>${s}×</option>`).join("")}
                </select>
              </div>
              <div class="player-actions">
                <button class="btn tg">📲 Abrir no Telegram</button>
                <button class="btn watch">${v.watched ? "↩ Marcar não vista" : "✓ Marcar como vista"}</button>
              </div>
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

    // Atalhos de teclado (espaço = play/pause, setas = seek 5s).
    const onKey = (e) => {
      const vid = modal.querySelector("video");
      if (!vid) return;
      if (e.key === " ") { e.preventDefault(); vid.paused ? vid.play() : vid.pause(); }
      else if (e.key === "ArrowRight") { vid.currentTime += 5; }
      else if (e.key === "ArrowLeft") { vid.currentTime -= 5; }
    };
    document.addEventListener("keydown", onKey);
    bk.addEventListener("remove", () => document.removeEventListener("keydown", onKey), { once: true });
    const _origRemove = bk.remove.bind(bk);
    bk.remove = () => { document.removeEventListener("keydown", onKey); _origRemove(); };

    try {
      const s = await api.prepareStream(v.id);
      const url = api.streamUrl(s.token);
      const pw = modal.querySelector("#pw");
      pw.innerHTML = "";
      // preload="auto" + faststart no backend → começa a tocar antes.
      const video = h(`<video controls autoplay playsinline preload="auto"></video>`);
      video.src = url;
      video.playbackRate = playbackRate;
      pw.appendChild(video);

      // Aplica/persiste a velocidade escolhida.
      const sel = modal.querySelector("#speedSel");
      sel.addEventListener("change", () => {
        playbackRate = parseFloat(sel.value) || 1;
        video.playbackRate = playbackRate;
        localStorage.setItem("tgweb_rate", String(playbackRate));
        toast(`Velocidade: ${playbackRate}×`, "");
      });
      // Mantém a taxa mesmo se o navegador resetar ao recarregar buffer.
      video.addEventListener("ratechange", () => {
        if (Math.abs(video.playbackRate - playbackRate) > 0.01) video.playbackRate = playbackRate;
      });

      // salva progresso a cada 15s (menos chamadas = mais leve)
      let last = 0;
      video.addEventListener("timeupdate", () => {
        if (video.currentTime - last > 15) {
          last = video.currentTime;
          api.saveProgress(v.id, Math.round(video.currentTime * 1000), Math.round((video.duration || 0) * 1000)).catch(() => {});
        }
      });
      if (s.start_position_ms) {
        video.addEventListener("loadedmetadata", () => { video.currentTime = s.start_position_ms / 1000; }, { once: true });
      }
      video.addEventListener("ended", async () => {
        try { await api.markWatched(v.id); } catch (e) {}
        v.watched = true; toast("Aula concluída ✓", "ok");
      });
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
          // Credenciais são cifradas no servidor. Em seguida, login por telefone.
          await api.tgCredentials(id, hash, ctx.account_id);
          renderTgStep(body, bk, "phone", ctx);
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
          const r = await api.tgSendCode(phone, ctx.account_id);
          if (r.flood_wait) return toast(`Aguarde ${Math.ceil(r.flood_wait / 60)} min antes de pedir outro código.`, "err");
          renderTgStep(body, bk, "code", ctx);
        } catch (e) { toast(e.message, "err"); }
      });
    } else if (step === "code") {
      body.innerHTML = `
        <p class="muted" style="margin-bottom:16px;font-size:13.5px">Digite o código enviado no seu Telegram. Nunca compartilhe esse código.</p>
        <div class="field"><label>Código</label><input id="code" placeholder="1 2 3 4 5" inputmode="numeric" /></div>
        <button class="btn btn-primary btn-block" id="next">Confirmar</button>`;
      body.querySelector("#next").addEventListener("click", async () => {
        try {
          const r = await api.tgSignIn(body.querySelector("#code").value.trim(), ctx.account_id);
          if (r.needs_password) return renderTgStep(body, bk, "password", ctx);
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
          const r = await api.tgPassword(body.querySelector("#pw2").value, ctx.account_id);
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
      closeModal(bk);
      const overlay = openSyncOverlay("Adicionando cursos…", "Salvando os canais/grupos selecionados.");
      try {
        await api.addCourses(chosen);
        closeSyncOverlay(overlay);
        toast(`${chosen.length} curso(s) adicionado(s) ✓`, "ok");
        State.courses = await api.courses();
        renderCourses();
      } catch (e) {
        closeSyncOverlay(overlay);
        toast("Erro ao adicionar: " + e.message, "err");
      }
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
    // Carrega usuário e status do Telegram EM PARALELO (mais rápido no boot).
    const [meRes, tgRes] = await Promise.allSettled([api.me(), api.tgStatus()]);

    if (meRes.status === "fulfilled") {
      State.user = (meRes.value && meRes.value.user) || null;
    } else if (meRes.reason && meRes.reason.status === 401) {
      api.token = ""; return startAuth();
    }

    if (tgRes.status === "fulfilled") {
      State.tg = tgRes.value;
    } else if (tgRes.reason && tgRes.reason.status === 401) {
      api.token = ""; return startAuth();
    } else {
      State.tg = { connected: false, has_credentials: false };
    }
    renderShell();
  }

  window.addEventListener("tgweb:logout", () => startAuth());

  // Decide entre tela de login (conta existe) ou criar conta (primeira vez).
  async function startAuth() {
    try {
      const st = await api.authState();
      if (!st.has_users && st.registration_open) renderRegister();
      else renderLogin(null, st);
    } catch (e) {
      // Se nem o estado conseguimos buscar, mostra login (backend offline mostra erro).
      renderLogin();
    }
  }

  initTheme();
  if (api.token) boot(); else startAuth();
})();
