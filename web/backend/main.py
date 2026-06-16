"""TgPlayer Web — Backend FastAPI (MULTIUSUÁRIO).

Cada usuário:
- cria sua conta (e-mail + senha) na plataforma;
- informa o SEU próprio API_ID / API_HASH (https://my.telegram.org);
- recebe o código do Telegram e confirma o login;
- tem sua sessão vinculada APENAS à própria conta (nada compartilhado).

Segurança:
- Dados sensíveis (API_ID/HASH/session/telefone) são SEMPRE cifrados no banco
  via ``EncryptionService`` (Fernet) com a chave ``ENCRYPTION_KEY``.
- JWT por usuário, expiração de sessão, rate limiting e proteção brute-force.
- Sanitização de exceções (nenhum traceback/segredo é devolvido ao cliente).
- Painel administrativo NÃO expõe API_ID/HASH/session/tokens.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

# Garante que `src/` (pacote tgplayer) esteja no path.
_THIS = Path(__file__).resolve()
_REPO_ROOT = _THIS.parents[2]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import aiohttp  # noqa: E402
from fastapi import Depends, FastAPI, HTTPException, Request, status  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import (  # noqa: E402
    FileResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from tgplayer.db import Database  # noqa: E402
from tgplayer.paths import CACHE_DIR, DB_PATH  # noqa: E402

from . import auth, config  # noqa: E402
from .services import (  # noqa: E402
    EncryptionService,
    TelegramAccountService,
    TelegramAuthService,
)
from .services.telegram_auth import SessionRevokedError  # noqa: E402
from .services.web_db import User, WebDatabase  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("tgplayer.web")

FRONTEND_DIR = _THIS.parent.parent / "frontend"

app = FastAPI(title="TgPlayer Web (multiusuário)", version="2.0.0")

_cors_wildcard = "*" in config.CORS_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=not _cors_wildcard,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Recursos globais (instanciados no startup).
core_db: Database = None  # type: ignore[assignment]
web_db: WebDatabase = None  # type: ignore[assignment]
enc: EncryptionService = None  # type: ignore[assignment]
accounts: TelegramAccountService = None  # type: ignore[assignment]
tg: TelegramAuthService = None  # type: ignore[assignment]
bearer = HTTPBearer(auto_error=False)


# =========================================================== ciclo de vida
@app.on_event("startup")
async def _startup() -> None:
    global core_db, web_db, enc, accounts, tg

    # Criptografia — obrigatória. Em dev pode-se permitir chave efêmera.
    try:
        enc = EncryptionService()
    except RuntimeError:
        if config.ALLOW_EPHEMERAL_ENCRYPTION:
            log.warning(
                "ENCRYPTION_KEY ausente — usando chave EFÊMERA (apenas DEV). "
                "Dados cifrados se tornarão ilegíveis após reiniciar!"
            )
            enc = EncryptionService(keys=[EncryptionService.generate_key()])
        else:
            raise

    core_db = Database()
    web_db = WebDatabase(str(DB_PATH))
    accounts = TelegramAccountService(web_db, enc)
    tg = TelegramAuthService(accounts, enc, CACHE_DIR, core_db=core_db)

    # Provisiona admin a partir do .env (uma única vez).
    if config.ADMIN_EMAIL and config.ADMIN_PASSWORD:
        if not web_db.get_user_by_email(config.ADMIN_EMAIL):
            try:
                auth.register_user(
                    web_db, config.ADMIN_EMAIL, config.ADMIN_PASSWORD, is_admin=True
                )
                log.info("Administrador inicial provisionado: %s", config.ADMIN_EMAIL)
            except auth.AuthError as exc:
                log.warning("Não foi possível provisionar admin: %s", exc)

    web_db.prune_login_attempts()


@app.on_event("shutdown")
async def _shutdown() -> None:
    try:
        tg.stop()
    except Exception:  # noqa: BLE001
        pass


# =========================================================== sanitização global
@app.exception_handler(Exception)
async def _sanitize_exceptions(request: Request, exc: Exception) -> Response:
    """Nunca devolve traceback/segredo ao cliente; loga o detalhe no servidor."""
    if isinstance(exc, HTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    log.exception("Erro não tratado em %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Erro interno do servidor."}, status_code=500)


# =========================================================== helpers
async def _call_tg(coro) -> Any:
    return await asyncio.wrap_future(tg.call(coro))


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def require_user(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> User:
    if creds is None or not creds.credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Não autenticado")
    payload = auth.verify_token(creds.credentials)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido ou expirado")
    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido")
    user = web_db.get_user(user_id)
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Conta inexistente ou inativa")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Acesso restrito a administradores")
    return user


def _resolve_account(user: User, account_id: int | None):
    """Retorna a conta Telegram do usuário (a indicada, ou a primeira)."""
    if account_id is not None:
        acc = accounts.get_owned(account_id, user.id)
        if not acc:
            raise HTTPException(404, "Conta Telegram não encontrada")
        return acc
    acc = accounts.ensure_account(user.id)
    return acc


# =========================================================== modelos
class RegisterIn(BaseModel):
    email: str
    password: str


class LoginIn(BaseModel):
    email: str
    password: str


class TelegramCredsIn(BaseModel):
    api_id: str
    api_hash: str
    account_id: int | None = None


class PhoneIn(BaseModel):
    phone: str
    account_id: int | None = None


class CodeIn(BaseModel):
    code: str
    account_id: int | None = None


class PasswordIn(BaseModel):
    password: str
    account_id: int | None = None


class AccountRefIn(BaseModel):
    account_id: int | None = None


class TaskIn(BaseModel):
    text: str
    priority: int = 1
    course_id: int | None = None


# =========================================================== AUTH (site)
@app.get("/api/auth/state")
async def auth_state() -> dict[str, Any]:
    """Diz ao frontend se o registro está aberto e quantos usuários existem."""
    return {
        "registration_open": config.ALLOW_REGISTRATION,
        "has_users": web_db.count_users() > 0,
    }


@app.post("/api/register")
async def register(data: RegisterIn, request: Request) -> dict[str, Any]:
    if not config.ALLOW_REGISTRATION:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cadastro desabilitado.")
    try:
        user = auth.register_user(web_db, data.email, data.password)
    except auth.AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    # Já cria uma conta Telegram vazia para o usuário começar a configurar.
    accounts.ensure_account(user.id)
    token = auth.create_token(user.id, user.email, user.is_admin)
    web_db.touch_last_login(user.id)
    return {"token": token, "user": _user_public(user)}


@app.post("/api/login")
async def login(data: LoginIn, request: Request) -> dict[str, Any]:
    ip = _client_ip(request)
    email = auth.normalize_email(data.email)
    try:
        auth.check_login_rate_limit(web_db, email, ip)
        user = auth.authenticate(web_db, email, data.password)
    except auth.AuthError as exc:
        if exc.status == 401:
            auth.record_login_result(web_db, email, ip, success=False)
        raise HTTPException(exc.status, str(exc)) from exc
    auth.record_login_result(web_db, email, ip, success=True)
    web_db.touch_last_login(user.id)
    token = auth.create_token(user.id, user.email, user.is_admin)
    return {"token": token, "user": _user_public(user)}


@app.get("/api/me")
async def me(user: User = Depends(require_user)) -> dict[str, Any]:
    return {"user": _user_public(user)}


def _user_public(user: User) -> dict[str, Any]:
    """Dados públicos do usuário (sem hash de senha)."""
    return {
        "id": user.id,
        "email": user.email,
        "is_admin": bool(user.is_admin),
        "created_at": user.created_at,
    }


# =========================================================== CONTAS TELEGRAM
@app.get("/api/telegram/accounts")
async def telegram_accounts(user: User = Depends(require_user)) -> list[dict[str, Any]]:
    return [accounts.safe_view(a) for a in accounts.list_for_user(user.id)]


@app.post("/api/telegram/accounts")
async def telegram_account_create(user: User = Depends(require_user)) -> dict[str, Any]:
    acc = accounts.create_account(user.id)
    return accounts.safe_view(acc)


@app.delete("/api/telegram/accounts/{account_id}")
async def telegram_account_delete(account_id: int, user: User = Depends(require_user)) -> dict[str, Any]:
    acc = accounts.get_owned(account_id, user.id)
    if not acc:
        raise HTTPException(404, "Conta não encontrada")
    try:
        await _call_tg(tg.logout(account_id))
    except Exception:  # noqa: BLE001
        pass
    accounts.delete(account_id)
    return {"ok": True}


@app.get("/api/telegram/status")
async def telegram_status(
    account_id: int | None = None, user: User = Depends(require_user)
) -> dict[str, Any]:
    acc = _resolve_account(user, account_id)
    info = await _call_tg(tg.status(acc.id))
    info["account_id"] = acc.id
    return info


@app.post("/api/telegram/credentials")
async def telegram_credentials(data: TelegramCredsIn, user: User = Depends(require_user)) -> dict[str, Any]:
    acc = _resolve_account(user, data.account_id)
    api_id = (data.api_id or "").strip()
    api_hash = (data.api_hash or "").strip()
    if not api_id.isdigit():
        raise HTTPException(400, "API ID deve ser numérico.")
    if len(api_hash) < 8:
        raise HTTPException(400, "API HASH inválido.")
    accounts.set_api_credentials(acc.id, api_id, api_hash)
    return {"ok": True, "account_id": acc.id}


@app.post("/api/telegram/send-code")
async def telegram_send_code(data: PhoneIn, request: Request, user: User = Depends(require_user)) -> dict[str, Any]:
    acc = _resolve_account(user, data.account_id)
    if not accounts.has_api_credentials(acc.id):
        raise HTTPException(400, "Configure API ID/HASH antes de entrar.")
    # Rate limit no envio de código (anti brute-force / FLOOD).
    ip = _client_ip(request)
    ident = f"tgcode:{acc.id}:{ip}"
    if web_db.count_recent_failures(ident, config.TELEGRAM_SENDCODE_WINDOW) >= config.TELEGRAM_SENDCODE_MAX:
        raise HTTPException(429, "Muitas solicitações de código. Aguarde um pouco.")
    web_db.record_login_attempt(ident, success=False)
    try:
        return await _call_tg(tg.send_code(acc.id, data.phone.strip()))
    except SessionRevokedError as exc:
        raise HTTPException(401, "session_revoked") from exc


@app.post("/api/telegram/sign-in")
async def telegram_sign_in(data: CodeIn, user: User = Depends(require_user)) -> dict[str, Any]:
    acc = _resolve_account(user, data.account_id)
    try:
        result = await _call_tg(tg.sign_in(acc.id, data.code.strip()))
    except SessionRevokedError as exc:
        raise HTTPException(401, "session_revoked") from exc
    return result


@app.post("/api/telegram/password")
async def telegram_password(data: PasswordIn, user: User = Depends(require_user)) -> dict[str, Any]:
    acc = _resolve_account(user, data.account_id)
    return await _call_tg(tg.check_password(acc.id, data.password))


@app.post("/api/telegram/logout")
async def telegram_logout(data: AccountRefIn, user: User = Depends(require_user)) -> dict[str, Any]:
    acc = _resolve_account(user, data.account_id)
    return await _call_tg(tg.logout(acc.id))


@app.get("/api/telegram/dialogs")
async def telegram_dialogs(
    account_id: int | None = None, user: User = Depends(require_user)
) -> dict[str, Any]:
    acc = _resolve_account(user, account_id)
    try:
        courses_list = await _call_tg(tg.list_dialog_courses(acc.id))
        return {"ok": True, "courses": courses_list}
    except SessionRevokedError as exc:
        raise HTTPException(401, "session_revoked") from exc


# =========================================================== CURSOS
# NOTA: cursos/aulas/progresso continuam no banco do core (compartilhado a nível
# de instalação). O isolamento crítico (credenciais Telegram + sessão) é por
# usuário/conta. Sincronização e streaming usam SEMPRE o client da conta do
# próprio usuário.
@app.get("/api/courses")
async def courses_list(user: User = Depends(require_user)) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in core_db.list_courses():
        done, total = core_db.course_progress(c.id)
        data = asdict(c)
        data["done"] = done
        data["total"] = total
        data["pct"] = int(done / total * 100) if total else 0
        out.append(data)
    return out


@app.post("/api/courses/add")
async def courses_add(payload: dict[str, Any], user: User = Depends(require_user)) -> dict[str, Any]:
    selected = payload.get("courses") or []
    added = 0
    for course in selected:
        core_db.upsert_course(course)
        added += 1
    return {"added": added}


@app.delete("/api/courses/{course_id}")
async def courses_delete(course_id: int, user: User = Depends(require_user)) -> dict[str, Any]:
    core_db.delete_course(course_id)
    return {"ok": True}


@app.post("/api/courses/{course_id}/color")
async def courses_color(course_id: int, payload: dict[str, Any], user: User = Depends(require_user)) -> dict[str, Any]:
    core_db.set_course_color(course_id, payload.get("color"))
    return {"ok": True}


@app.post("/api/courses/{course_id}/sync")
async def courses_sync(
    course_id: int, payload: dict[str, Any] | None = None, user: User = Depends(require_user)
) -> dict[str, Any]:
    course = core_db.get_course(course_id)
    if not course:
        raise HTTPException(404, "Curso não encontrado")
    body = payload or {}
    acc = _resolve_account(user, body.get("account_id"))
    limit = int(body.get("limit") or 99999)
    try:
        result = await _call_tg(tg.sync_course(acc.id, course.chat_id, limit=limit))
    except SessionRevokedError as exc:
        raise HTTPException(401, "session_revoked") from exc
    new_course_id = core_db.upsert_course(result["chat"])
    _apply_sync(new_course_id, result)
    core_db.touch_course_sync(new_course_id)
    accounts.touch_sync(acc.id)
    done, total = core_db.course_progress(new_course_id)
    return {
        "ok": True,
        "course_id": new_course_id,
        "detected": result.get("detected"),
        "videos": len(result.get("videos") or []),
        "scanned": result.get("scanned"),
        "done": done,
        "total": total,
    }


def _apply_sync(course_id: int, result: dict[str, Any]) -> None:
    topic_to_subject: dict[str, int] = {}
    for s in result.get("subjects") or []:
        tg_id = str(s.get("telegram_topic_id") or "")
        subject_id = core_db.find_or_create_subject(
            course_id,
            s.get("title") or "Matéria",
            telegram_topic_id=tg_id or None,
            summary_text=s.get("summary_text") or "",
            manual=0,
        )
        existing = core_db.get_subject(subject_id)
        if existing and not existing.manual:
            new_title = s.get("title") or "Matéria"
            if new_title and new_title != existing.title:
                core_db.rename_subject(subject_id, new_title)
            if s.get("summary_text"):
                core_db.update_subject_summary(subject_id, s.get("summary_text"))
        topic_to_subject[tg_id] = subject_id

    videos = result.get("videos") or []
    for v in videos:
        tg_id = str(v.get("telegram_topic_id") or "")
        if tg_id and tg_id in topic_to_subject:
            v["subject_id"] = topic_to_subject[tg_id]
    core_db.replace_videos(course_id, videos)


# =========================================================== MATÉRIAS + AULAS
@app.get("/api/courses/{course_id}/subjects")
async def course_subjects(course_id: int, user: User = Depends(require_user)) -> list[dict[str, Any]]:
    return [asdict(s) for s in core_db.list_subjects(course_id)]


@app.get("/api/courses/{course_id}/videos")
async def course_videos(course_id: int, user: User = Depends(require_user)) -> list[dict[str, Any]]:
    return [_video_dict(v) for v in core_db.list_videos(course_id)]


def _video_dict(v) -> dict[str, Any]:
    data = asdict(v)
    course = core_db.get_course(v.course_id) if v.course_id else None
    username = getattr(course, "username", None) if course else None
    tg_url, web_url = tg.telegram_message_urls(username, v.chat_id, v.message_id)
    data["tg_url"] = tg_url
    data["tme_url"] = web_url
    data["watched"] = bool(v.watched_at)
    return data


# =========================================================== AÇÕES DE AULA
@app.post("/api/videos/{video_id}/watched")
async def video_watched(video_id: int, user: User = Depends(require_user)) -> dict[str, Any]:
    core_db.mark_watched(video_id)
    return {"ok": True}


@app.post("/api/videos/{video_id}/unwatched")
async def video_unwatched(video_id: int, user: User = Depends(require_user)) -> dict[str, Any]:
    core_db.mark_unwatched(video_id)
    return {"ok": True}


@app.post("/api/videos/{video_id}/favorite")
async def video_favorite(video_id: int, user: User = Depends(require_user)) -> dict[str, Any]:
    fav = core_db.toggle_favorite(video_id)
    return {"ok": True, "favorite": fav}


@app.post("/api/videos/{video_id}/progress")
async def video_progress(video_id: int, payload: dict[str, Any], user: User = Depends(require_user)) -> dict[str, Any]:
    core_db.save_progress(
        video_id, int(payload.get("position_ms") or 0), payload.get("duration_ms")
    )
    return {"ok": True}


# =========================================================== STREAMING
@app.post("/api/videos/{video_id}/prepare-stream")
async def prepare_stream(video_id: int, payload: dict[str, Any] | None = None, user: User = Depends(require_user)) -> dict[str, Any]:
    v = core_db.get_video(video_id)
    if not v:
        raise HTTPException(404, "Aula não encontrada")
    acc = _resolve_account(user, (payload or {}).get("account_id"))
    course = core_db.get_course(v.course_id) if v.course_id else None
    payload_stream = {
        "chat_id": v.chat_id,
        "message_id": v.message_id,
        "file_name": v.file_name,
        "title": v.title,
        "mime_type": v.mime_type,
        "size": v.size,
        "duration": v.duration,
        "width": v.width,
        "height": v.height,
        "course_id": v.course_id,
        "chat_username": getattr(course, "username", None) if course else None,
        "start_position_ms": int(v.position_ms or 0),
    }
    try:
        result = await _call_tg(tg.prepare_stream(acc.id, payload_stream))
    except SessionRevokedError as exc:
        raise HTTPException(401, "session_revoked") from exc
    token = result["token"]
    return {
        "token": token,
        "stream_url": f"/api/stream/{token}",
        "mime_type": v.mime_type or "video/mp4",
        "title": v.title,
        "duration": v.duration,
        "start_position_ms": int(v.position_ms or 0),
    }


@app.api_route("/api/stream/{token}", methods=["GET", "HEAD"])
async def stream_proxy(token: str, request: Request) -> Response:
    """Proxy do streaming (suporta HTTP Range). O token isola a conta."""
    port = getattr(tg, "port", None)
    if not port:
        raise HTTPException(503, "Servidor de streaming não está pronto")
    upstream = f"http://127.0.0.1:{port}/stream/{token}/video"
    headers = {}
    if "range" in request.headers:
        headers["Range"] = request.headers["range"]

    session = aiohttp.ClientSession()
    try:
        resp = await session.request(request.method, upstream, headers=headers)
    except Exception as exc:  # noqa: BLE001
        await session.close()
        raise HTTPException(502, "Falha no streaming") from exc

    out_headers = {}
    for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
        if h in resp.headers:
            out_headers[h] = resp.headers[h]
    out_headers.setdefault("Accept-Ranges", "bytes")

    if request.method == "HEAD":
        await resp.release()
        await session.close()
        return Response(status_code=resp.status, headers=out_headers)

    async def _iter():
        try:
            async for chunk in resp.content.iter_chunked(256 * 1024):
                yield chunk
        finally:
            await resp.release()
            await session.close()

    return StreamingResponse(_iter(), status_code=resp.status, headers=out_headers)


# =========================================================== CONTINUAR ASSISTINDO
@app.get("/api/continue")
async def continue_watching(user: User = Depends(require_user)) -> list[dict[str, Any]]:
    return [_video_dict(v) for v in core_db.continue_watching(12)]


# =========================================================== ACOMPANHAMENTO
@app.get("/api/study/dashboard")
async def study_dashboard(user: User = Depends(require_user)) -> dict[str, Any]:
    today = core_db.today_study_seconds()
    week = core_db.week_study_seconds()
    streak = core_db.study_streak_days()
    pomos = core_db.count_pomodoros_today()
    done_videos, total_videos = core_db.video_totals()
    by_day = [{"day": d, "seconds": s} for d, s in core_db.study_seconds_by_day(7)]
    by_course = core_db.course_completion_stats()
    recent = core_db.recent_completed_videos(8)
    goal = core_db.get_setting("weekly_goal_hours") or "10"
    return {
        "today_seconds": today,
        "week_seconds": week,
        "streak_days": streak,
        "pomodoros_today": pomos,
        "videos_done": done_videos,
        "videos_total": total_videos,
        "by_day": by_day,
        "by_course": by_course,
        "recent": recent,
        "weekly_goal_hours": float(goal),
    }


@app.post("/api/study/goal")
async def study_goal(payload: dict[str, Any], user: User = Depends(require_user)) -> dict[str, Any]:
    core_db.set_setting("weekly_goal_hours", str(payload.get("hours") or 10))
    return {"ok": True}


@app.post("/api/study/pomodoro")
async def study_pomodoro(payload: dict[str, Any], user: User = Depends(require_user)) -> dict[str, Any]:
    minutes = int(payload.get("minutes") or 25)
    seconds = minutes * 60
    core_db.add_pomodoro_session(seconds, kind="foco", course_id=payload.get("course_id"))
    core_db.log_study_time(seconds, course_id=payload.get("course_id"))
    return {"ok": True}


@app.post("/api/study/log")
async def study_log(payload: dict[str, Any], user: User = Depends(require_user)) -> dict[str, Any]:
    core_db.log_study_time(int(payload.get("seconds") or 0), payload.get("course_id"))
    return {"ok": True}


# =========================================================== TAREFAS
@app.get("/api/tasks")
async def tasks_list(user: User = Depends(require_user)) -> list[dict[str, Any]]:
    return core_db.list_tasks(include_done=True)


@app.post("/api/tasks")
async def tasks_add(data: TaskIn, user: User = Depends(require_user)) -> dict[str, Any]:
    tid = core_db.add_task(data.text, priority=data.priority, due_date=None, course_id=data.course_id)
    return {"ok": True, "id": tid}


@app.post("/api/tasks/{task_id}/toggle")
async def tasks_toggle(task_id: int, user: User = Depends(require_user)) -> dict[str, Any]:
    done = core_db.toggle_task(task_id)
    return {"ok": True, "done": done}


@app.delete("/api/tasks/{task_id}")
async def tasks_delete(task_id: int, user: User = Depends(require_user)) -> dict[str, Any]:
    core_db.delete_task(task_id)
    return {"ok": True}


# =========================================================== ADMIN (privacidade)
@app.get("/api/admin/overview")
async def admin_overview(admin: User = Depends(require_admin)) -> dict[str, Any]:
    """Painel administrativo SEM dados sensíveis.

    Mostra apenas: conta conectada, última sincronização, status da conexão,
    quantidade de arquivos/cursos e espaço utilizado (estimado). NUNCA expõe
    API_ID, API_HASH, session string, telefone ou tokens.
    """
    users_out: list[dict[str, Any]] = []
    for u in web_db.list_users():
        accs = accounts.list_for_user(u.id)
        acc_views = []
        for a in accs:
            view = accounts.safe_view(a)
            # Métricas agregadas seguras por conta conectada.
            files = _account_file_count(a)
            view["files"] = files["files"]
            view["bytes_used"] = files["bytes"]
            acc_views.append(view)
        users_out.append(
            {
                "id": u.id,
                "email": u.email,
                "is_admin": bool(u.is_admin),
                "is_active": bool(u.is_active),
                "created_at": u.created_at,
                "last_login_at": u.last_login_at,
                "accounts": acc_views,
            }
        )
    return {
        "users_count": len(users_out),
        "accounts_count": len(web_db.list_all_accounts()),
        "users": users_out,
    }


def _account_file_count(account) -> dict[str, int]:
    """Estatísticas agregadas (arquivos/bytes) das contas Telegram conectadas.

    Como cursos/aulas são por instalação (não por conta), retornamos um total
    de aulas/espaço da instalação apenas para a primeira conta conectada, para
    fins de painel. Nada sensível é exposto.
    """
    try:
        done, total = core_db.video_totals()
    except Exception:  # noqa: BLE001
        total = 0
    # Estima espaço somando o tamanho das aulas conhecidas.
    bytes_used = 0
    try:
        with core_db.connect() as conn:
            row = conn.execute("SELECT COALESCE(SUM(size),0) AS s, COUNT(*) AS n FROM videos").fetchone()
            bytes_used = int(row["s"] or 0)
            total = int(row["n"] or 0)
    except Exception:  # noqa: BLE001
        pass
    return {"files": total, "bytes": bytes_used}


@app.post("/api/admin/users/{user_id}/active")
async def admin_set_active(user_id: int, payload: dict[str, Any], admin: User = Depends(require_admin)) -> dict[str, Any]:
    if user_id == admin.id:
        raise HTTPException(400, "Você não pode desativar a si mesmo.")
    target = web_db.get_user(user_id)
    if not target:
        raise HTTPException(404, "Usuário não encontrado")
    web_db.set_user_active(user_id, bool(payload.get("active", True)))
    return {"ok": True}


# =========================================================== FRONTEND
@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "version": app.version}


if FRONTEND_DIR.exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(FRONTEND_DIR / "index.html"))

    @app.exception_handler(404)
    async def spa_fallback(request: Request, exc) -> Response:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return FileResponse(str(FRONTEND_DIR / "index.html"))
