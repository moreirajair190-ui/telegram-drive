"""TgPlayer Web — Backend FastAPI.

Reaproveita a lógica do app desktop (`src/tgplayer`):
- `Database`        -> mesmo banco SQLite (cursos, matérias, aulas, progresso,
                       pomodoro, tarefas, estatísticas).
- `TelegramService` -> login na SUA conta, sincronização, streaming HTTP e
                       geração de links tg:// / t.me.

Expõe uma API REST protegida por JWT (login/senha fixo) e serve o frontend.

Para abrir os vídeos, o frontend usa:
- ▶ "Assistir no navegador": player HTML5 que consome /api/stream/... (proxy
  para o streaming local do TelegramService).
- 📲 "Abrir no Telegram (64Gram/Desktop)": deep link tg:// gerado pelo backend.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

# Garante que `src/` (pacote tgplayer) esteja no path, esteja onde estiver.
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
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from tgplayer.db import Database  # noqa: E402
from tgplayer.telegram_service import SessionRevokedError, TelegramService  # noqa: E402

from . import auth, config  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tgplayer.web")

FRONTEND_DIR = _THIS.parent.parent / "frontend"

app = FastAPI(title="TgPlayer Web", version="1.0.0")
# Auth é via Bearer token (não cookies), então não precisamos de credentials.
# E "allow_origins=['*']" com "allow_credentials=True" é rejeitado pelos browsers,
# por isso só ligamos credentials quando há origens específicas listadas.
_cors_wildcard = "*" in config.CORS_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=not _cors_wildcard,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Recursos globais (instanciados no startup).
db: Database = None  # type: ignore[assignment]
service: TelegramService = None  # type: ignore[assignment]
bearer = HTTPBearer(auto_error=False)


# =========================================================== ciclo de vida
@app.on_event("startup")
async def _startup() -> None:
    global db, service
    db = Database()
    service = TelegramService(db=db)

    # Reaproveita API ID/HASH: prioridade para env; senão usa o que já está
    # salvo no banco do app desktop.
    api_id = config.TELEGRAM_API_ID or db.get_setting("api_id") or ""
    api_hash = config.TELEGRAM_API_HASH or db.get_setting("api_hash") or ""
    if api_id and api_hash:
        db.set_setting("api_id", str(api_id))
        db.set_setting("api_hash", str(api_hash))
        try:
            fut = service.call(service.ensure_connected(api_id, api_hash))
            result = await asyncio.wrap_future(fut)
            log.info("Telegram: %s", "conectado" if result.get("authorized") else "aguardando login")
        except Exception:  # noqa: BLE001
            log.exception("Falha ao conectar no Telegram no startup (seguindo mesmo assim)")
    else:
        log.warning("Sem API ID/HASH configurados — defina em web/backend/.env ou faça login.")


@app.on_event("shutdown")
async def _shutdown() -> None:
    try:
        service.stop()
    except Exception:  # noqa: BLE001
        pass


# =========================================================== helpers
async def _call(coro) -> Any:
    """Executa uma corrotina no event loop do TelegramService e aguarda."""
    return await asyncio.wrap_future(service.call(coro))


def require_auth(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> str:
    if creds is None or not creds.credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Não autenticado")
    payload = auth.verify_token(creds.credentials)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido ou expirado")
    return str(payload.get("sub"))


# =========================================================== modelos
class LoginIn(BaseModel):
    username: str
    password: str


class SetupIn(BaseModel):
    username: str
    password: str
    api_id: str | None = None
    api_hash: str | None = None


class TelegramCredsIn(BaseModel):
    api_id: str
    api_hash: str


class PhoneIn(BaseModel):
    phone: str


class CodeIn(BaseModel):
    code: str


class PasswordIn(BaseModel):
    password: str


class TaskIn(BaseModel):
    text: str
    priority: int = 1
    course_id: int | None = None


# =========================================================== AUTH (site)
@app.get("/api/auth/state")
async def auth_state() -> dict[str, Any]:
    """Diz ao frontend se já existe conta (mostra login) ou não (mostra
    a tela de "Criar conta"). Também informa se há env fixo configurado."""
    has_account = auth.account_exists(db)
    # Conta fixa por env conta como "já existe conta" para fins de fluxo,
    # exceto se ainda estiver no padrão de desenvolvimento.
    env_account = bool(
        config.WEB_USERNAME
        and config.WEB_PASSWORD
        and not (config.WEB_USERNAME == "admin" and config.WEB_PASSWORD == "tgplayer123")
    )
    return {
        "account_exists": bool(has_account or env_account),
        "created_in_db": bool(has_account),
    }


@app.post("/api/setup")
async def setup_account(data: SetupIn) -> dict[str, Any]:
    """Cria a conta do site (login/senha) na primeira utilização.

    Opcionalmente já recebe os dados do Telegram (API ID/HASH) e os salva,
    deixando tudo pronto para o usuário só inserir o telefone/código depois.
    Só permite criar enquanto NÃO existir conta no banco (evita sobrescrever).
    """
    if auth.account_exists(db):
        raise HTTPException(status.HTTP_409_CONFLICT, "Uma conta já foi criada. Faça login.")

    username = (data.username or "").strip()
    password = data.password or ""
    if len(username) < 3:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Usuário precisa ter ao menos 3 caracteres.")
    if len(password) < 6:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Senha precisa ter ao menos 6 caracteres.")

    auth.create_account(db, username, password)

    # Se o usuário já forneceu os dados do Telegram, salva-os.
    api_id = (data.api_id or "").strip()
    api_hash = (data.api_hash or "").strip()
    if api_id and api_hash:
        db.set_setting("api_id", api_id)
        db.set_setting("api_hash", api_hash)

    token = auth.create_token(username)
    return {"token": token, "user": username, "has_telegram_creds": bool(api_id and api_hash)}


@app.post("/api/login")
async def login(data: LoginIn) -> dict[str, Any]:
    if not auth.check_credentials(db, data.username, data.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuário ou senha inválidos")
    token = auth.create_token(data.username)
    return {"token": token, "user": data.username}


# =========================================================== TELEGRAM (conta)
@app.get("/api/telegram/status")
async def telegram_status(_: str = Depends(require_auth)) -> dict[str, Any]:
    """Estado da conexão com a SUA conta do Telegram."""
    api_id = config.TELEGRAM_API_ID or db.get_setting("api_id") or ""
    api_hash = config.TELEGRAM_API_HASH or db.get_setting("api_hash") or ""
    info: dict[str, Any] = {
        "has_credentials": bool(api_id and api_hash),
        "connected": False,
        "me": None,
    }
    if not (api_id and api_hash):
        return info
    try:
        me = await _call(service.get_me())
        if me:
            info["connected"] = True
            info["me"] = me
    except Exception:  # noqa: BLE001
        info["connected"] = False
    return info


@app.post("/api/telegram/credentials")
async def telegram_credentials(data: TelegramCredsIn, _: str = Depends(require_auth)) -> dict[str, Any]:
    db.set_setting("api_id", data.api_id.strip())
    db.set_setting("api_hash", data.api_hash.strip())
    result = await _call(service.ensure_connected(data.api_id.strip(), data.api_hash.strip()))
    return result


@app.post("/api/telegram/send-code")
async def telegram_send_code(data: PhoneIn, _: str = Depends(require_auth)) -> dict[str, Any]:
    return await _call(service.send_code(data.phone.strip()))


@app.post("/api/telegram/sign-in")
async def telegram_sign_in(data: CodeIn, _: str = Depends(require_auth)) -> dict[str, Any]:
    return await _call(service.sign_in(data.code.strip()))


@app.post("/api/telegram/password")
async def telegram_password(data: PasswordIn, _: str = Depends(require_auth)) -> dict[str, Any]:
    return await _call(service.check_password(data.password))


@app.post("/api/telegram/logout")
async def telegram_logout(_: str = Depends(require_auth)) -> dict[str, Any]:
    return await _call(service.logout())


@app.get("/api/telegram/dialogs")
async def telegram_dialogs(_: str = Depends(require_auth)) -> dict[str, Any]:
    """Lista grupos/canais para o usuário escolher quais virar cursos."""
    try:
        courses = await _call(service.list_dialog_courses())
        return {"ok": True, "courses": courses}
    except SessionRevokedError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"session_revoked:{exc}") from exc


# =========================================================== CURSOS
@app.get("/api/courses")
async def courses(_: str = Depends(require_auth)) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in db.list_courses():
        done, total = db.course_progress(c.id)
        data = asdict(c)
        data["done"] = done
        data["total"] = total
        data["pct"] = int(done / total * 100) if total else 0
        out.append(data)
    return out


@app.post("/api/courses/add")
async def courses_add(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
    """Adiciona cursos a partir da seleção de grupos/canais do Telegram."""
    selected = payload.get("courses") or []
    added = 0
    for course in selected:
        db.upsert_course(course)
        added += 1
    return {"added": added}


@app.delete("/api/courses/{course_id}")
async def courses_delete(course_id: int, _: str = Depends(require_auth)) -> dict[str, Any]:
    db.delete_course(course_id)
    return {"ok": True}


@app.post("/api/courses/{course_id}/color")
async def courses_color(course_id: int, payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
    db.set_course_color(course_id, payload.get("color"))
    return {"ok": True}


@app.post("/api/courses/{course_id}/sync")
async def courses_sync(course_id: int, payload: dict[str, Any] | None = None, _: str = Depends(require_auth)) -> dict[str, Any]:
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "Curso não encontrado")
    limit = int((payload or {}).get("limit") or 99999)
    try:
        result = await _call(service.sync_course(course.chat_id, limit=limit))
    except SessionRevokedError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"session_revoked:{exc}") from exc
    # Aplica resultado no banco (matérias + vídeos).
    new_course_id = db.upsert_course(result["chat"])
    _apply_sync(new_course_id, result)
    db.touch_course_sync(new_course_id)
    done, total = db.course_progress(new_course_id)
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
    """Cria/atualiza matérias e vincula vídeos (versão web do _apply_sync_result)."""
    topic_to_subject: dict[str, int] = {}
    for s in result.get("subjects") or []:
        tg_id = str(s.get("telegram_topic_id") or "")
        subject_id = db.find_or_create_subject(
            course_id,
            s.get("title") or "Matéria",
            telegram_topic_id=tg_id or None,
            summary_text=s.get("summary_text") or "",
            manual=0,
        )
        existing = db.get_subject(subject_id)
        if existing and not existing.manual:
            new_title = s.get("title") or "Matéria"
            if new_title and new_title != existing.title:
                db.rename_subject(subject_id, new_title)
            if s.get("summary_text"):
                db.update_subject_summary(subject_id, s.get("summary_text"))
        topic_to_subject[tg_id] = subject_id

    videos = result.get("videos") or []
    for v in videos:
        tg_id = str(v.get("telegram_topic_id") or "")
        if tg_id and tg_id in topic_to_subject:
            v["subject_id"] = topic_to_subject[tg_id]
    db.replace_videos(course_id, videos)


# =========================================================== MATÉRIAS + AULAS
@app.get("/api/courses/{course_id}/subjects")
async def course_subjects(course_id: int, _: str = Depends(require_auth)) -> list[dict[str, Any]]:
    return [asdict(s) for s in db.list_subjects(course_id)]


@app.get("/api/courses/{course_id}/videos")
async def course_videos(course_id: int, _: str = Depends(require_auth)) -> list[dict[str, Any]]:
    return [_video_dict(v) for v in db.list_videos(course_id)]


def _video_dict(v) -> dict[str, Any]:
    data = asdict(v)
    # Gera links tg:// e t.me para abrir no app (64Gram / Desktop) ou web.
    course = db.get_course(v.course_id) if v.course_id else None
    username = getattr(course, "username", None) if course else None
    tg_url, web_url = service.telegram_message_urls(username, v.chat_id, v.message_id)
    data["tg_url"] = tg_url
    data["tme_url"] = web_url
    data["watched"] = bool(v.watched_at)
    return data


# =========================================================== AÇÕES DE AULA
@app.post("/api/videos/{video_id}/watched")
async def video_watched(video_id: int, _: str = Depends(require_auth)) -> dict[str, Any]:
    db.mark_watched(video_id)
    return {"ok": True}


@app.post("/api/videos/{video_id}/unwatched")
async def video_unwatched(video_id: int, _: str = Depends(require_auth)) -> dict[str, Any]:
    db.mark_unwatched(video_id)
    return {"ok": True}


@app.post("/api/videos/{video_id}/favorite")
async def video_favorite(video_id: int, _: str = Depends(require_auth)) -> dict[str, Any]:
    fav = db.toggle_favorite(video_id)
    return {"ok": True, "favorite": fav}


@app.post("/api/videos/{video_id}/progress")
async def video_progress(video_id: int, payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
    db.save_progress(
        video_id,
        int(payload.get("position_ms") or 0),
        payload.get("duration_ms"),
    )
    return {"ok": True}


# =========================================================== STREAMING (player web)
@app.post("/api/videos/{video_id}/prepare-stream")
async def prepare_stream(video_id: int, _: str = Depends(require_auth)) -> dict[str, Any]:
    """Prepara o streaming de uma aula e devolve a URL de proxy para o player web."""
    v = db.get_video(video_id)
    if not v:
        raise HTTPException(404, "Aula não encontrada")
    course = db.get_course(v.course_id) if v.course_id else None
    payload = {
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
        result = await _call(service.prepare_stream(payload))
    except SessionRevokedError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"session_revoked:{exc}") from exc
    token = result["token"]
    # O frontend consome SEMPRE pelo proxy do backend (mesma origem da API),
    # evitando problemas de CORS/127.0.0.1 quando hospedado na Cloudflare.
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
    """Proxy do streaming do TelegramService (suporta HTTP Range)."""
    port = getattr(service, "port", None)
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
        raise HTTPException(502, f"Falha no streaming: {exc}") from exc

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
async def continue_watching(_: str = Depends(require_auth)) -> list[dict[str, Any]]:
    return [_video_dict(v) for v in db.continue_watching(12)]


# =========================================================== ACOMPANHAMENTO
@app.get("/api/study/dashboard")
async def study_dashboard(_: str = Depends(require_auth)) -> dict[str, Any]:
    today = db.today_study_seconds()
    week = db.week_study_seconds()
    streak = db.study_streak_days()
    pomos = db.count_pomodoros_today()
    done_videos, total_videos = db.video_totals()
    by_day = [{"day": d, "seconds": s} for d, s in db.study_seconds_by_day(7)]
    by_course = db.course_completion_stats()
    recent = db.recent_completed_videos(8)
    goal = db.get_setting("weekly_goal_hours") or "10"
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
async def study_goal(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
    db.set_setting("weekly_goal_hours", str(payload.get("hours") or 10))
    return {"ok": True}


@app.post("/api/study/pomodoro")
async def study_pomodoro(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
    minutes = int(payload.get("minutes") or 25)
    seconds = minutes * 60
    db.add_pomodoro_session(seconds, kind="foco", course_id=payload.get("course_id"))
    db.log_study_time(seconds, course_id=payload.get("course_id"))
    return {"ok": True}


@app.post("/api/study/log")
async def study_log(payload: dict[str, Any], _: str = Depends(require_auth)) -> dict[str, Any]:
    db.log_study_time(int(payload.get("seconds") or 0), payload.get("course_id"))
    return {"ok": True}


# =========================================================== TAREFAS
@app.get("/api/tasks")
async def tasks_list(_: str = Depends(require_auth)) -> list[dict[str, Any]]:
    return db.list_tasks(include_done=True)


@app.post("/api/tasks")
async def tasks_add(data: TaskIn, _: str = Depends(require_auth)) -> dict[str, Any]:
    tid = db.add_task(data.text, priority=data.priority, due_date=None, course_id=data.course_id)
    return {"ok": True, "id": tid}


@app.post("/api/tasks/{task_id}/toggle")
async def tasks_toggle(task_id: int, _: str = Depends(require_auth)) -> dict[str, Any]:
    done = db.toggle_task(task_id)
    return {"ok": True, "done": done}


@app.delete("/api/tasks/{task_id}")
async def tasks_delete(task_id: int, _: str = Depends(require_auth)) -> dict[str, Any]:
    db.delete_task(task_id)
    return {"ok": True}


# =========================================================== FRONTEND (estático)
@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "version": app.version}


if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(FRONTEND_DIR / "index.html"))

    @app.exception_handler(404)
    async def spa_fallback(request: Request, exc) -> Response:
        # SPA: rotas não-API caem no index.html.
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return FileResponse(str(FRONTEND_DIR / "index.html"))


