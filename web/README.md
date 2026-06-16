# TgPlayer Web 🎬

Versão **web** do TgPlayer: um painel bonito, com login/senha fixo, que se conecta
à **sua** conta do Telegram para organizar e assistir suas videoaulas.

Os vídeos podem ser abertos de duas formas:

1. **No app do Telegram** (64Gram / Telegram Desktop / derivados) através de links
   `tg://` — abre direto no aplicativo do PC.
2. **No navegador**, com um player HTML5 que faz streaming via backend (com suporte
   a *seek*/Range e salvamento automático de progresso).

---

## 🧱 Arquitetura

```
┌────────────────────────┐         ┌──────────────────────────────────┐
│  Frontend (estático)    │  HTTPS  │  Backend FastAPI (no SEU PC/VPS)  │
│  HTML + CSS + JS         │ ──────► │  - Login JWT (usuário/senha fixo) │
│  (Cloudflare Pages OU    │         │  - Conexão Pyrogram c/ Telegram   │
│   servido pelo backend)  │ ◄────── │  - Banco SQLite (cursos/aulas)    │
└────────────────────────┘         │  - Proxy de streaming de vídeo    │
                                     └──────────────────────────────────┘
```

> ⚠️ **Por que o backend não roda na Cloudflare?**
> A Cloudflare Pages/Workers não consegue rodar Pyrogram (MTProto/TCP persistente)
> nem fazer streaming de arquivos grandes do Telegram. Por isso o backend roda na
> sua máquina (ou um VPS) e a Cloudflare hospeda só o frontend, apontando para ele.
> Você também pode rodar **tudo junto** (o backend já serve o frontend) — é o jeito
> mais simples.

---

## 🚀 Modo simples (tudo junto, recomendado para começar)

O backend já serve o frontend na mesma porta. Não precisa de Cloudflare.

```bash
# 1) Instale as dependências (de preferência num venv)
python3 -m venv .venv && source .venv/bin/activate
pip install -r web/backend/requirements.txt

# 2) Configure (copie o exemplo e edite)
cp web/backend/.env.example web/backend/.env
# edite web/backend/.env: defina TGWEB_USER, TGWEB_PASSWORD, TGWEB_SECRET,
# TGWEB_API_ID e TGWEB_API_HASH (pegue em https://my.telegram.org)

# 3) Suba o servidor
bash web/run_web.sh
```

Abra **http://localhost:8800**, faça login com o usuário/senha que você definiu,
e conecte sua conta do Telegram pelo botão de status (rodapé da barra lateral).

---

## ⚙️ Configuração (.env)

Copie `web/backend/.env.example` para `web/backend/.env` e ajuste:

| Variável         | Descrição                                              | Padrão        |
|------------------|--------------------------------------------------------|---------------|
| `TGWEB_USER`     | Usuário fixo do login do site                          | `admin`       |
| `TGWEB_PASSWORD` | Senha fixa do login do site                            | `tgplayer123` |
| `TGWEB_SECRET`   | Segredo para assinar o JWT (troque por algo aleatório) | *(gerado)*    |
| `TGWEB_API_ID`   | API ID do Telegram (my.telegram.org)                   | —             |
| `TGWEB_API_HASH` | API HASH do Telegram (my.telegram.org)                 | —             |
| `TGWEB_HOST`     | Host de bind                                           | `0.0.0.0`     |
| `TGWEB_PORT`     | Porta do backend                                       | `8800`        |
| `TGWEB_CORS`     | Origens permitidas (separadas por vírgula) ou `*`      | `*`           |

> 🔐 **Importante:** troque `TGWEB_PASSWORD` e `TGWEB_SECRET`. Em produção, **não**
> deixe `TGWEB_CORS=*`; liste o domínio do seu frontend (ex.: `https://meu-site.pages.dev`).
> Você pode obter `TGWEB_API_ID` / `TGWEB_API_HASH` em https://my.telegram.org → *API development tools*.

---

## ☁️ Publicar na Cloudflare Pages (frontend) + backend separado

Use isto se quiser que o site (frontend) fique numa URL pública da Cloudflare e o
backend rode no seu PC/VPS.

### 1. Suba o backend no seu PC/VPS e exponha por HTTPS

O backend precisa estar acessível pela internet via HTTPS. Opções:

- **Cloudflare Tunnel** (grátis, recomendado — não precisa abrir portas):
  ```bash
  # no PC onde roda o backend:
  bash web/run_web.sh           # backend em :8800
  cloudflared tunnel --url http://localhost:8800
  # ele te dá uma URL https://xxxx.trycloudflare.com
  ```
- **VPS** com domínio próprio + Nginx/Caddy fazendo TLS na frente do :8800.

Anote a URL pública do backend, ex.: `https://meu-backend.exemplo.com`.

> Defina no `.env` do backend: `TGWEB_CORS=https://SEU-SITE.pages.dev`

### 2. Configure o frontend para apontar ao backend

Edite **`web/frontend/assets/config.js`**:

```js
window.TGWEB_API_BASE = "https://meu-backend.exemplo.com";
```

### 3. Publique a pasta `web/frontend` na Cloudflare Pages

**Pela interface (mais fácil):**
1. Cloudflare Dashboard → *Workers & Pages* → *Create* → *Pages* → *Upload assets*.
2. Faça upload do **conteúdo da pasta `web/frontend`** (index.html, assets/, _redirects, _headers).
3. Deploy. Pronto — sua URL será algo como `https://meu-tgplayer.pages.dev`.

**Pela CLI (Wrangler):**
```bash
npm i -g wrangler
wrangler pages deploy web/frontend --project-name=meu-tgplayer
```

> Não há etapa de *build* — é tudo estático (HTML/CSS/JS puro). O arquivo
> `_redirects` já faz o fallback de SPA para o `index.html`.

### 4. Acesse o site

Abra `https://meu-tgplayer.pages.dev`, faça login e conecte o Telegram.
O frontend conversa com o seu backend pela URL configurada no `config.js`.

---

## 🎥 Como os vídeos abrem

- **Botão "Abrir no Telegram"** → usa `tg://resolve?...` (canais públicos) ou
  `tg://privatepost?channel=...&post=...` (privados). Abre no 64Gram / Telegram
  Desktop. Se não houver app registrado para `tg://`, cai no `https://t.me/...`.
- **Botão "Assistir aqui"** → player HTML5 no navegador. O backend prepara um
  *stream* do Telegram e faz proxy com suporte a Range (você pode arrastar a barra).
  O progresso é salvo automaticamente a cada ~10s.

---

## 🔌 Endpoints principais da API

Todos sob `/api`, protegidos por JWT (header `Authorization: Bearer <token>`),
exceto `/api/login` e `/api/health`.

- `POST /api/login` — `{username, password}` → `{token}`
- `GET  /api/telegram/status` — estado da conexão
- `POST /api/telegram/send-code` / `sign-in` / `password` / `logout`
- `GET  /api/telegram/dialogs` — lista chats para adicionar como cursos
- `GET  /api/courses` — cursos + progresso
- `POST /api/courses` / `DELETE /api/courses/{id}` / `POST /api/courses/{id}/sync`
- `GET  /api/courses/{id}/subjects` / `.../videos`
- `POST /api/videos/{id}/watched|unwatched|favorite|progress`
- `POST /api/videos/{id}/prepare-stream` → token de streaming
- `GET  /api/stream/{token}` — proxy do vídeo (Range)
- `GET  /api/study/dashboard` / `goal` / `pomodoro` / `log`
- `GET/POST/PATCH/DELETE /api/tasks`
- `GET  /api/health`

---

## 🛠️ Rodar em desenvolvimento

```bash
PYTHONPATH="$PWD/src:$PWD" \
TGWEB_USER=admin TGWEB_PASSWORD=test123 \
python3 -m uvicorn web.backend.main:app --host 0.0.0.0 --port 8800 --reload
```

O frontend é servido pelo próprio backend em `http://localhost:8800`.

---

## ❓ FAQ

**Posso usar a mesma sessão do app desktop?**
Sim — o backend reutiliza o mesmo banco SQLite e o mesmo nome de sessão Pyrogram
(`tgclassplayer`). Se já estava logado no desktop, normalmente conecta direto.

**Recebi `AUTH_KEY_UNREGISTERED`?**
O backend detecta sessão revogada e limpa os arquivos de sessão automaticamente,
pedindo um novo login (código). Basta reconectar pela interface.

**Esqueci a senha do site.**
Edite `TGWEB_PASSWORD` no `.env` e reinicie o backend.
