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

### Banco de dados (novo esquema)

```
users(id, email UNIQUE, password_hash, is_admin, is_active,
      created_at, updated_at, last_login_at)

telegram_accounts(id, user_id FK→users(id), label,
      encrypted_api_id, encrypted_api_hash, encrypted_session, encrypted_phone,
      tg_user_id, tg_username, tg_first_name, status,
      last_sync_at, created_at, updated_at)            -- 1 user → N contas

login_attempts(id, identifier, success, created_at)     -- rate limiting
```

Tudo que é sensível (`api_id`, `api_hash`, `session`, `phone`) é gravado
**apenas** em colunas `encrypted_*` (tokens Fernet `gAAAAA...`). Senhas em
`pbkdf2_sha256` (200k iterações + salt).

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

1. Cria as novas tabelas se não existirem.
2. Detecta usuário legado (`web_account_user`/`web_account_pwd`) ou admin do env.
3. Cria o usuário real (reaproveita o hash PBKDF2 antigo quando possível).
4. **Cifra** `api_id`/`api_hash` que estavam em texto puro → `telegram_accounts`.
5. Converte a sessão antiga (`.session`) em session string **cifrada**.

```bash
python -m backend.migrate --dry-run   # conferir
python -m backend.migrate             # aplicar
```

---

## 4) Checklist de segurança (produção)

- [ ] `ENCRYPTION_KEY` definida (Fernet) e guardada com segurança (backup offline).
- [ ] `TGWEB_SECRET` (JWT) longo e aleatório; **não** o valor de exemplo.
- [ ] `TGWEB_ALLOW_EPHEMERAL_KEY` **desligado** (sem fallback efêmero).
- [ ] `TGWEB_CORS` restrito ao domínio do frontend (sem `*`).
- [ ] HTTPS em frontend (Pages) e backend (Render) — sem conteúdo misto.
- [ ] Disco persistente montado; `TGPLAYER_DATA` apontando para ele.
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
- `web/backend/services/telegram_account.py`
- `web/backend/services/telegram_auth.py`
- `web/backend/migrate.py`
- `web/DEPLOY.md`, `web/SECURITY.md`

**Modificados**
- `web/backend/config.py` (ENCRYPTION_KEY/JWT/admin/rate-limit/CORS/PORT)
- `web/backend/auth.py` (multiusuário, PBKDF2, rate limit, JWT por user_id)
- `web/backend/main.py` (rotas multiusuário, painel admin sem dado sensível,
  sanitização de exceções)
- `web/backend/requirements.txt` (+`cryptography`)
- `web/backend/.env.example` (novas variáveis)
- `web/frontend/assets/api.js` (endpoints multiusuário + admin)
- `web/frontend/assets/app.js` (criar conta por e-mail, conexão Telegram por
  usuário, painel de administração)
- `web/frontend/assets/style.css` (estilos do painel admin)
