# 🔐 TgPlayer Web — Segurança & Entregáveis

Documento de entrega da transformação **single-user → multiusuário**, com cada
usuário usando a **própria** conta do Telegram e o administrador **sem** acesso
a credenciais individuais.

---

## 1) Diagnóstico da arquitetura anterior

A versão original era **single-user**:

- **Login fixo** por env (`TGWEB_USER` / `TGWEB_PASSWORD`) — uma única conta.
- **API_ID / API_HASH globais** em `settings` (texto puro no SQLite).
- **Uma única sessão Pyrogram** compartilhada (`sessions/tgclassplayer.session`).
- Todos os usuários (na prática, um só) compartilhavam a mesma conta Telegram.
- Não havia separação de responsabilidades para credenciais sensíveis.

### Vulnerabilidades encontradas

| # | Vulnerabilidade | Impacto | Correção |
|---|-----------------|---------|----------|
| V1 | `API_ID`/`API_HASH` em **texto puro** no banco | Vazamento total da credencial Telegram | Cifrados com Fernet via `EncryptionService` |
| V2 | **Sessão única** compartilhada | Qualquer login acessa a mesma conta Telegram | Sessão **por usuário** (session string cifrada) |
| V3 | Login **fixo** por env | Sem isolamento/multiusuário; senha em env | `users(email, password_hash)` + PBKDF2 |
| V4 | Sessão `.session` em disco | Roubo do arquivo = acesso à conta | `in_memory=True` + session string cifrada no BD |
| V10 | **Filesystem persistente** obrigatório (`/var/data`, SQLite, `.session`) | Quebra no Render Free; risco de perda/roubo de arquivo | Persistência 100% no **Postgres (Supabase)**; servidor **stateless** |
| V5 | Sem **rate limiting** no login | Brute force ilimitado | `login_attempts` + janela deslizante (429) |
| V6 | Sem limite no **send-code** do Telegram | Flood/bloqueio da conta do usuário | Rate limit dedicado de send-code |
| V7 | Erros expõem **stack/segredos** | Vazamento de dados em respostas | Handler global sanitiza exceções |
| V8 | Admin enxergaria credenciais | Quebra de privacidade | `safe_view()` remove todo dado sensível |
| V9 | JWT sem expiração robusta | Sessões eternas | JWT HS256 com `exp` (72h padrão) |

---

## 2) O que foi implementado

### Camadas de serviço (separação de responsabilidades)

- **`EncryptionService`** (`services/encryption.py`)
  Única responsável por cifrar/decifrar (Fernet/MultiFernet). Suporta
  `ENCRYPTION_KEY` (única) ou `ENCRYPTION_KEYS` (rotação). Métodos:
  `encrypt`, `decrypt`, `try_decrypt`, `rotate`, `generate_key`.

- **`WebDatabase`** (`services/web_db.py`)
  Esquema multiusuário + queries de rate limit.

- **`TelegramAccountService`** (`services/telegram_account.py`)
  **Único** ponto que cifra/decifra credenciais Telegram. Expõe `safe_view()`
  (sem dados sensíveis) para o painel admin.

- **`TelegramAuthService`** (`services/telegram_auth.py`)
  Clientes Pyrogram **isolados por conta** (in-memory + session string).
  Login (`send_code`/`sign_in`/`check_password`), sync e streaming por usuário.

### Persistência sem filesystem (Postgres/Supabase)

A partir desta versão, **não há nenhuma dependência de disco local** no
servidor. Um adaptador unificado (`services/db_backend.py`) seleciona o backend
em tempo de execução:

- **`DATABASE_URL` definido** (produção) → **PostgreSQL** (Supabase), via
  `psycopg` v3. SQL único com placeholders `?` traduzidos para `%s`; tipos de
  dialeto resolvidos (`BIGSERIAL`/`AUTOINCREMENT`, `DOUBLE PRECISION`/`REAL`,
  `RETURNING id` para `lastrowid`). `client_encoding=UTF8` forçado.
- **sem `DATABASE_URL`** (dev/desktop) → **SQLite** local.

O core web (`services/web_core_db.py`, classe `WebCoreDatabase`) reescreve, de
forma dialect-agnostic, todos os métodos de cursos/aulas/estudo — o core
desktop (`tgplayer.db.Database`) permanece intacto.

O cache de streaming (`/tmp`) é **efêmero** (buffer de bytes do vídeo) e
NÃO é persistência: pode sumir no restart sem perda.

### Banco de dados (novo esquema — mesmo em Postgres ou SQLite)

Tabelas multiusuário (`WebDatabase`):
```
users(id, email UNIQUE, password_hash, is_admin, is_active,
      created_at, updated_at, last_login_at)

telegram_accounts(id, user_id FK→users(id), label,
      encrypted_api_id, encrypted_api_hash, encrypted_session, encrypted_phone,
      tg_user_id, tg_username, tg_first_name, status,
      last_sync_at, created_at, updated_at)            -- 1 user → N contas

login_attempts(id, identifier, success, created_at)     -- rate limiting
```

Tabelas de conteúdo/estudo (`WebCoreDatabase`):
```
settings, courses, subjects, videos, pomodoro_sessions,
tasks, study_log, moov_cache
```

Tudo que é sensível (`api_id`, `api_hash`, `session`, `phone`) é gravado
**apenas** em colunas `encrypted_*` (tokens Fernet `gAAAAA...`) — idem em
Postgres. Senhas em `pbkdf2_sha256` (200k iterações + salt). As credenciais
cifradas sobrevivem a logout/restart do servidor porque vivem no Postgres.

### Privacidade do administrador

O endpoint `/api/admin/overview` retorna **somente**: conta conectada (label/
username), última sincronização, status da conexão, contagem de arquivos e
espaço usado. **Nunca** API_ID, API_HASH, session string, telefone ou tokens.

### Segurança adicional

- **Rate limiting** no login do site (`LOGIN_MAX_FAILURES` / janela) → `429`.
- **Rate limiting** no `send-code` do Telegram (evita flood/bloqueio).
- **Autenticação timing-safe** com mensagem genérica.
- **JWT HS256** com `sub=user_id`, `iat`, `exp` (expiração configurável).
- **Sanitização de exceções** global (sem stack/segredos no corpo da resposta).
- **Logs sem dados sensíveis**.

---

## 3) Migração automática

`web/backend/migrate.py` (idempotente, com `--dry-run`):

1. **Destino** = `DATABASE_URL` (Supabase) ou SQLite local; cria as tabelas.
2. **Fonte legada** (opcional) = SQLite do desktop, se existir e for acessível.
3. Detecta usuário legado (`web_account_user`/`web_account_pwd`) ou admin do env.
4. Cria o usuário real (reaproveita o hash PBKDF2 antigo quando possível).
5. **Cifra** `api_id`/`api_hash` que estavam em texto puro → `telegram_accounts`.
6. Converte a sessão antiga (`.session`) em session string **cifrada**.

```bash
# Lê o SQLite legado e grava cifrado no Postgres do Supabase:
DATABASE_URL="postgresql://...supabase.co:5432/postgres?sslmode=require" \
  ENCRYPTION_KEY=... python -m backend.migrate --dry-run   # conferir
DATABASE_URL="..." ENCRYPTION_KEY=... python -m backend.migrate  # aplicar
```

> Se não houver banco legado (instalação nova no Render), não é preciso migrar:
> os usuários se cadastram direto pelo site e tudo é persistido no Supabase.

---

## 4) Checklist de segurança (produção)

- [ ] `ENCRYPTION_KEY` definida (Fernet) e guardada com segurança (backup offline).
- [ ] `TGWEB_SECRET` (JWT) longo e aleatório; **não** o valor de exemplo.
- [ ] `TGWEB_ALLOW_EPHEMERAL_KEY` **desligado** (sem fallback efêmero).
- [ ] `TGWEB_CORS` restrito ao domínio do frontend (sem `*`).
- [ ] HTTPS em frontend (Pages) e backend (Render) — sem conteúdo misto.
- [ ] `DATABASE_URL` (Supabase) configurada; **sem** disco/`TGPLAYER_DATA`/`/var/data`.
- [ ] Reiniciável: após restart do Render, usuários/credenciais continuam (Supabase).
- [ ] Admin provisionado com senha forte; senha removida do env depois.
- [ ] Rate limits ativos (login e send-code) com valores adequados.
- [ ] Conferido no banco: colunas `encrypted_*` são `gAAAAA...` (nunca texto puro).
- [ ] `/api/admin/overview` não retorna nenhum dado sensível.
- [ ] Migração rodada (se havia dados antigos) e validada.
- [ ] `.env` real **fora** do versionamento (já no `.gitignore`).

---

## 5) Arquivos criados/modificados

**Criados**
- `web/backend/services/__init__.py`
- `web/backend/services/encryption.py`
- `web/backend/services/web_db.py`
- `web/backend/services/db_backend.py` (adaptador Postgres/SQLite unificado)
- `web/backend/services/web_core_db.py` (cursos/aulas/estudo dialect-agnostic)
- `web/backend/services/telegram_account.py`
- `web/backend/services/telegram_auth.py`
- `web/backend/migrate.py`
- `web/DEPLOY.md`, `web/SECURITY.md`

**Modificados**
- `web/backend/config.py` (DATABASE_URL/SUPABASE_DB_URL, STREAM_CACHE_DIR,
  SQLITE_PATH, ENCRYPTION_KEY/JWT/admin/rate-limit/CORS/PORT)
- `web/backend/requirements.txt` (+`psycopg[binary]`)
- `web/backend/auth.py` (multiusuário, PBKDF2, rate limit, JWT por user_id)
- `web/backend/main.py` (rotas multiusuário, painel admin sem dado sensível,
  sanitização de exceções)
- `web/backend/requirements.txt` (+`cryptography`)
- `web/backend/.env.example` (novas variáveis)
- `web/frontend/assets/api.js` (endpoints multiusuário + admin)
- `web/frontend/assets/app.js` (criar conta por e-mail, conexão Telegram por
  usuário, painel de administração)
- `web/frontend/assets/style.css` (estilos do painel admin)
