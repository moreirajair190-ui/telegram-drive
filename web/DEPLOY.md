# 🚀 Deploy do TgPlayer Web (multiusuário)

Arquitetura de produção:

- **Frontend** (HTML/CSS/JS estático) → **Cloudflare Pages**
- **Backend** (FastAPI + Pyrogram) → **Render** (Web Service)

```
┌───────────────────────────┐        ┌─────────────────────────────────┐
│ Cloudflare Pages (estático)│  HTTPS │ Render Web Service (FastAPI)     │
│ web/frontend               │ ─────► │ web/backend                       │
│  index.html + assets/*     │        │  - JWT por usuário (HS256)        │
└───────────────────────────┘        │  - Credenciais Telegram CIFRADAS  │
                                      │    (Fernet / ENCRYPTION_KEY)      │
                                      │  - SQLite em disco persistente    │
                                      └─────────────────────────────────┘
```

Cada usuário usa a **própria** conta do Telegram (API ID/HASH próprios). O
administrador **não** precisa conhecer nem gerenciar credenciais individuais.

---

## 1) Gerar os segredos (faça isto primeiro)

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

## 2) Backend no Render

### 2.1 Criar o serviço

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

### 2.2 Disco persistente (IMPORTANTE)

O banco SQLite e a sessão precisam sobreviver a reinícios/deploys.

1. No serviço → **Disks** → **Add Disk**.
   - **Mount Path**: `/var/data`
   - **Size**: 1 GB (suficiente para começar)
2. Adicione a variável de ambiente `TGPLAYER_DATA=/var/data`.

### 2.3 Variáveis de ambiente (Environment)

| Variável                     | Valor / Exemplo                                  | Obrigatória |
|------------------------------|--------------------------------------------------|:-----------:|
| `ENCRYPTION_KEY`             | (saída do Fernet.generate_key)                   | ✅ |
| `TGWEB_SECRET`               | (saída do token_hex(32))                         | ✅ |
| `TGPLAYER_DATA`              | `/var/data`                                      | ✅ |
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

### 2.4 (Opcional) Migrar dados antigos

Se você já usava a versão single-user, rode a migração **uma vez**:

```bash
# Localmente, apontando para o mesmo TGPLAYER_DATA, ou via Render Shell:
ENCRYPTION_KEY=... TGWEB_SECRET=... TGPLAYER_DATA=/var/data \
  python -m backend.migrate            # use --dry-run antes para conferir
```

A migração é **idempotente** e:
- cria a conta de usuário real (reaproveitando o hash antigo quando possível);
- **cifra** o `api_id`/`api_hash` que estavam em texto puro;
- converte a sessão antiga (`.session`) em session string **cifrada**.

---

## 3) Frontend no Cloudflare Pages

### 3.1 Apontar o frontend para o backend

Edite `web/frontend/assets/config.js` e informe a URL pública do Render:

```js
// web/frontend/assets/config.js
window.TGWEB_API_BASE = "https://SEU-SERVICO.onrender.com";
```

> Em desenvolvimento local (frontend e backend no mesmo host) deixe `""`.

### 3.2 Criar o projeto no Pages

1. Cloudflare → **Workers & Pages** → **Create application** → **Pages** →
   **Connect to Git** → selecione o repositório.
2. Build settings:
   - **Framework preset**: `None`
   - **Build command**: *(deixe vazio — é estático, sem build)*
   - **Build output directory**: `web/frontend`
3. **Save and Deploy**.

Seu site ficará em `https://SEU-PROJETO.pages.dev`.

### 3.3 Liberar o CORS

Volte ao Render e ajuste `TGWEB_CORS` para o domínio exato do Pages
(ex.: `https://SEU-PROJETO.pages.dev`). Aceita múltiplos domínios separados
por vírgula. Evite `*` em produção.

---

## 4) Domínio próprio (opcional)

- **Frontend**: Cloudflare Pages → **Custom domains** → adicione `app.seudominio.com`.
- **Backend**: Render → **Settings** → **Custom Domains** → `api.seudominio.com`.
- Atualize `TGWEB_API_BASE` (frontend) e `TGWEB_CORS` (backend) com os domínios finais.

---

## 5) Checklist pós-deploy

- [ ] `https://SEU-SERVICO.onrender.com/api/health` retorna `{"ok": true}`.
- [ ] O site Pages carrega e a tela de **Criar conta** aparece (primeiro acesso).
- [ ] Consigo criar conta, conectar **minha** conta do Telegram e listar diálogos.
- [ ] No banco, `encrypted_api_id`/`encrypted_api_hash`/`encrypted_session` são
      tokens Fernet (`gAAAAA...`), **nunca** texto puro.
- [ ] Login do admin → painel mostra usuários/contas **sem** dados sensíveis.
- [ ] `TGWEB_CORS` aponta para o domínio do Pages (não `*`).
- [ ] Disco persistente montado e `TGPLAYER_DATA` configurado.

---

## 6) Local (dev) — tudo num processo só

```bash
cd web
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt

export ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')"
export TGWEB_SECRET="$(python -c 'import secrets;print(secrets.token_hex(32))')"
export PYTHONPATH="$(pwd)/../src:$(pwd)"

uvicorn backend.main:app --reload --port 8800
# abra http://localhost:8800  (o backend também serve o frontend estático)
```
