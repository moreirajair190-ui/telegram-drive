# 🚀 Deploy do TgPlayer Web (multiusuário)

Arquitetura de produção (SEM filesystem persistente no servidor):

- **Frontend** (HTML/CSS/JS estático) → **Cloudflare Pages**
- **Backend** (FastAPI + Pyrogram) → **Render Free** (Web Service)
- **Banco de dados** (persistência) → **Supabase Postgres**

```
┌───────────────────────────┐      ┌──────────────────────────────────┐      ┌─────────────────────┐
│ Cloudflare Pages (estático)│ HTTPS│ Render Web Service (FastAPI)      │ TLS  │ Supabase Postgres   │
│ web/frontend               │ ────►│ web/backend                       │ ────►│ usuários, contas    │
│  index.html + assets/*     │      │  - JWT por usuário (HS256)        │      │ Telegram CIFRADAS,  │
└───────────────────────────┘      │  - Credenciais Telegram CIFRADAS  │      │ sessões cifradas,   │
                                   │    (Fernet / ENCRYPTION_KEY)      │      │ cursos/aulas/estudo │
                                   │  - SEM disco local (stream em /tmp)│      └─────────────────────┘
                                   └──────────────────────────────────┘
```

> 🚫 **Não é mais necessário** disco persistente do Render, `/var/data`,
> `TGPLAYER_DATA`, SQLite no servidor, Railway nem VPS. Toda a persistência
> vive no Postgres do Supabase. O Render Free pode dormir/reiniciar à vontade
> que **nada** é perdido.

Cada usuário usa a **própria** conta do Telegram (API ID/HASH próprios). O
administrador **não** precisa conhecer nem gerenciar credenciais individuais.

---

## 1) Criar o banco no Supabase (faça isto primeiro)

1. Crie um projeto em https://supabase.com (plano gratuito serve).
2. **Project Settings → Database → Connection string**. Copie a URI. Você pode
   usar a conexão direta (porta `5432`) ou a *connection pooling* (porta `6543`,
   recomendada para serverless/Free). Formato:
   ```
   postgresql://postgres:SUA_SENHA@db.SEU_PROJ.supabase.co:5432/postgres?sslmode=require
   ```
3. Guarde essa string — ela vira a variável `DATABASE_URL` no Render.

> O backend cria **todas as tabelas automaticamente** no primeiro boot
> (`users`, `telegram_accounts`, `login_attempts`, `settings`, `courses`,
> `subjects`, `videos`, `pomodoro_sessions`, `tasks`, `study_log`,
> `moov_cache`). Não é preciso rodar SQL manualmente.

---

## 2) Gerar os segredos

No seu computador, com Python + `cryptography` instalado:

```bash
# Chave de criptografia (Fernet) — cifra API_ID/API_HASH/session/telefone
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Segredo do JWT
python -c "import secrets; print(secrets.token_hex(32))"
```

> ⚠️ Guarde a `ENCRYPTION_KEY` com cuidado. Se ela for perdida ou trocada,
> **todas** as credenciais já cifradas se tornam ilegíveis e os usuários
> precisarão reconectar o Telegram.

---

## 3) Backend no Render Free

### 3.1 Criar o serviço

1. Faça push do repositório para o GitHub.
2. No Render → **New +** → **Web Service** → conecte o repositório.
3. Configurações (use **uma** das opções abaixo — ambas funcionam):

   **Opção A — Root Directory = `web/backend` (recomendada)**
   - **Root Directory**: `web/backend`
   - **Runtime**: Python 3
   - **Build Command**:
     ```bash
     pip install -r requirements.txt
     ```
   - **Start Command**:
     ```bash
     uvicorn main:app --host 0.0.0.0 --port $PORT
     ```

   **Opção B — Root Directory = `web`**
   - **Root Directory**: `web`
   - **Build Command**:
     ```bash
     pip install -r backend/requirements.txt
     ```
   - **Start Command**:
     ```bash
     uvicorn backend.main:app --host 0.0.0.0 --port $PORT
     ```

   > O Render injeta a variável `PORT` automaticamente; o `config.py` já a lê.
   > O backend usa **imports absolutos** e ajusta o `sys.path` internamente,
   > então inicia corretamente nas duas opções (e também via
   > `python -m uvicorn web.backend.main:app` a partir da raiz do repositório).

### 3.2 Banco de dados — SEM disco persistente

**Não adicione disco** no Render. A persistência é 100% no Supabase. Basta
configurar a variável `DATABASE_URL` (próxima seção) com a string do Supabase.
O cache de streaming usa `/tmp` (efêmero) e não precisa de disco.

### 3.3 Variáveis de ambiente (Environment)

| Variável                     | Valor / Exemplo                                  | Obrigatória |
|------------------------------|--------------------------------------------------|:-----------:|
| `DATABASE_URL`               | `postgresql://postgres:SENHA@db.xxx.supabase.co:5432/postgres?sslmode=require` | ✅ |
| `ENCRYPTION_KEY`             | (saída do Fernet.generate_key)                   | ✅ |
| `TGWEB_SECRET`               | (saída do token_hex(32))                         | ✅ |
| `TGWEB_CORS`                 | `https://SEU-PROJETO.pages.dev`                  | ✅ |
| `TGWEB_ADMIN_EMAIL`          | `admin@seudominio.com`                           | opcional |
| `TGWEB_ADMIN_PASSWORD`       | (senha forte)                                    | opcional |
| `TGWEB_ALLOW_REGISTRATION`   | `1` (aberto) / `0` (fechado)                     | opcional |
| `TGWEB_TOKEN_HOURS`          | `72`                                             | opcional |
| `TGWEB_LOGIN_MAX_FAILURES`   | `5`                                              | opcional |
| `TGWEB_LOGIN_WINDOW_SECONDS` | `900`                                            | opcional |
| `TGWEB_TG_SENDCODE_MAX`      | `4`                                              | opcional |
| `TGWEB_TG_SENDCODE_WINDOW`   | `3600`                                           | opcional |
| `TGWEB_PASSWORD_MIN_LENGTH`  | `8`                                              | opcional |

> Defina `TGWEB_ADMIN_EMAIL` + `TGWEB_ADMIN_PASSWORD` para que um administrador
> seja provisionado no primeiro boot. Depois você pode remover a senha do env.
>
> `SUPABASE_DB_URL` é aceito como sinônimo de `DATABASE_URL`. O `sslmode=require`
> é adicionado automaticamente se você esquecer.

### 3.4 (Opcional) Migrar dados antigos (de uma instalação SQLite local)

Se você já usava a versão single-user **com SQLite local**, rode a migração
**uma vez**, na sua máquina, apontando para o Supabase de destino:

```bash
# Roda localmente: LÊ o SQLite legado e GRAVA cifrado no Postgres do Supabase.
ENCRYPTION_KEY=... TGWEB_SECRET=... \
  DATABASE_URL="postgresql://...supabase.co:5432/postgres?sslmode=require" \
  TGPLAYER_DATA=/caminho/do/seu/data_antigo \
  python -m backend.migrate            # use --dry-run antes para conferir
```

A migração é **idempotente** e:
- cria a conta de usuário real (reaproveitando o hash antigo quando possível);
- **cifra** o `api_id`/`api_hash` que estavam em texto puro;
- converte a sessão antiga (`.session`) em session string **cifrada**.

---

## 4) Frontend no Cloudflare Pages

### 4.1 Apontar o frontend para o backend

Edite `web/frontend/assets/config.js` e informe a URL pública do Render:

```js
// web/frontend/assets/config.js
window.TGWEB_API_BASE = "https://SEU-SERVICO.onrender.com";
```

> Em desenvolvimento local (frontend e backend no mesmo host) deixe `""`.

### 4.2 Criar o projeto no Pages

1. Cloudflare → **Workers & Pages** → **Create application** → **Pages** →
   **Connect to Git** → selecione o repositório.
2. Build settings:
   - **Framework preset**: `None`
   - **Build command**: *(deixe vazio — é estático, sem build)*
   - **Build output directory**: `web/frontend`
3. **Save and Deploy**.

Seu site ficará em `https://SEU-PROJETO.pages.dev`.

### 4.3 Liberar o CORS

Volte ao Render e ajuste `TGWEB_CORS` para o domínio exato do Pages
(ex.: `https://SEU-PROJETO.pages.dev`). Aceita múltiplos domínios separados
por vírgula. Evite `*` em produção.

---

## 5) Domínio próprio (opcional)

- **Frontend**: Cloudflare Pages → **Custom domains** → adicione `app.seudominio.com`.
- **Backend**: Render → **Settings** → **Custom Domains** → `api.seudominio.com`.
- Atualize `TGWEB_API_BASE` (frontend) e `TGWEB_CORS` (backend) com os domínios finais.

---

## 6) Checklist pós-deploy

- [ ] `https://SEU-SERVICO.onrender.com/api/health` retorna `{"ok": true}`.
- [ ] O site Pages carrega e a tela de **Criar conta** aparece (primeiro acesso).
- [ ] Consigo criar conta, conectar **minha** conta do Telegram e listar diálogos.
- [ ] No banco, `encrypted_api_id`/`encrypted_api_hash`/`encrypted_session` são
      tokens Fernet (`gAAAAA...`), **nunca** texto puro.
- [ ] Login do admin → painel mostra usuários/contas **sem** dados sensíveis.
- [ ] `TGWEB_CORS` aponta para o domínio do Pages (não `*`).
- [ ] `DATABASE_URL` (Supabase) configurada — **sem** disco/`TGPLAYER_DATA`.
- [ ] Reiniciar o serviço no Render e confirmar que usuários/credenciais
      **continuam** lá (persistência no Supabase).

---

## 7) Local (dev) — tudo num processo só

```bash
cd web
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt

export ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')"
export TGWEB_SECRET="$(python -c 'import secrets;print(secrets.token_hex(32))')"
export PYTHONPATH="$(pwd)/../src:$(pwd)"

# Sem DATABASE_URL -> usa SQLite local automaticamente (./backend/tgplayer_web.sqlite3).
# Para testar contra Postgres, exporte DATABASE_URL apontando para o Supabase.
uvicorn backend.main:app --reload --port 8800
# abra http://localhost:8800  (o backend também serve o frontend estático)
```
