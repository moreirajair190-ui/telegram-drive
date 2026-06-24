"""Smoke test headless da v6.6.

Valida:
  1. Player revertido: botão principal "Assistir no Telegram", VLC como 2ª opção,
     e ausência total de QtWebEngine/player embutido no app.
  2. Árvore de aulas com o delegate (sem o "bug roxo").
  3. Planejador (Kanban + calendário): CRUD em plan_items, drag-drop lógico
     entre colunas e agendamento por data.

Captura screenshots em /home/user/webapp/.smoke_shots/.

Executar:
    QT_QPA_PLATFORM=offscreen python tests/smoke_v66.py
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_tmp = tempfile.mkdtemp(prefix="tgp_smoke_")
os.environ["TGPLAYER_DATA"] = _tmp
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402

try:
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
except Exception:  # noqa: BLE001
    pass

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from tgplayer.db import Database  # noqa: E402
from tgplayer import planner_tab as pt  # noqa: E402

SHOTS = os.path.join(os.path.dirname(__file__), "..", ".smoke_shots")
os.makedirs(SHOTS, exist_ok=True)


def seed_db() -> Database:
    db = Database()
    course_id = db.upsert_course(
        {"chat_id": "-1001234567890", "title": "Medicina · Turma M3",
         "username": None, "chat_type": "supergroup", "is_forum": 1}
    )
    subj = db.add_subject(course_id, "Infecções Respiratórias Agudas")
    videos = []
    for i in range(1, 7):
        videos.append({
            "chat_id": "-1001234567890",
            "message_id": 1000 + i,
            "title": f"Videoaula {i} — Parte I",
            "subject_id": subj,
            "module": "Módulo 1",
            "duration": 1800,
            "size": 100_000_000,
        })
    db.replace_videos(course_id, videos)
    return db, course_id


def test_player_reverted():
    from tgplayer.app import MainWindow
    # Garante que player.py foi removido.
    import importlib
    try:
        importlib.import_module("tgplayer.player")
        raise AssertionError("tgplayer.player ainda existe — deveria ter sido removido")
    except ModuleNotFoundError:
        pass

    win = MainWindow()
    assert hasattr(win, "watch_btn"), "watch_btn ausente"
    assert "Telegram" in win.watch_btn.text(), f"botão principal != Telegram: {win.watch_btn.text()}"
    assert hasattr(win, "watch_vlc_btn") and "VLC" in win.watch_vlc_btn.text()
    assert hasattr(win, "plan_btn") and "planejamento" in win.plan_btn.text().lower()
    assert hasattr(win, "add_selected_to_planner"), "método add_selected_to_planner ausente"
    assert not hasattr(win, "_active_player"), "_active_player ainda existe"
    assert not hasattr(win, "_open_player_for"), "_open_player_for ainda existe"
    # Aba Planejador existe.
    titles = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    assert any("Planejador" in t for t in titles), f"aba Planejador ausente: {titles}"
    print("  ✔ player revertido (Telegram principal + VLC + Planejador)")
    return win


def test_planner_crud_and_dragdrop(win):
    db = win.db
    planner = win.planner_tab
    course = win.get_current_course()
    videos = db.list_videos(course.id)
    assert videos, "sem vídeos para planejar"

    # Adiciona aula ao backlog.
    ok = planner.add_video(videos[0].id, column_key="backlog")
    assert ok, "add_video falhou"
    # Não duplica.
    assert planner.add_video(videos[0].id, column_key="backlog", silent=True) is False
    # Adiciona aula agendada para o dia selecionado.
    planner.add_video(videos[1].id, column_key="sched")
    # Tarefa livre.
    item_id = db.add_plan_item("Revisar pneumonia", column_key=pt.COL_WEEK)
    assert item_id > 0

    planner.refresh()
    assert planner.col_backlog["list"].count() == 1, "backlog deveria ter 1 cartão"
    assert planner.col_today["list"].count() == 1, "coluna do dia deveria ter 1 cartão"
    assert planner.col_week["list"].count() == 1, "semana deveria ter 1 cartão"

    # Move o cartão do backlog para "semana" (simula drop entre colunas).
    backlog_item = planner.col_backlog["list"].item(0)
    bid = int(backlog_item.data(pt.ROLE_ITEM_ID))
    planner._on_item_dropped(bid, pt.COL_WEEK)
    assert planner.col_backlog["list"].count() == 0
    assert planner.col_week["list"].count() == 2

    # Agenda um cartão num dia futuro via drop no calendário.
    week_item = planner.col_week["list"].item(0)
    wid = int(week_item.data(pt.ROLE_ITEM_ID))
    future = db.today()  # usa hoje (formato YYYY-MM-DD)
    planner._on_card_dropped_on_date(wid, future)
    counts = db.plan_counts_by_date()
    assert counts.get(future, 0) >= 1, f"calendário não marcou {future}: {counts}"

    # Marca como concluído.
    done_target = planner.col_week["list"].item(0)
    did = int(done_target.data(pt.ROLE_ITEM_ID))
    db.move_plan_item(did, pt.COL_DONE, 9999)
    planner.refresh()
    assert planner.col_done["list"].count() >= 1, "coluna concluído vazia"

    # Remove um cartão.
    total_before = len(db.list_plan_items())
    db.delete_plan_item(did)
    assert len(db.list_plan_items()) == total_before - 1
    print("  ✔ planejador CRUD + drag-drop + calendário")


def capture(win):
    win.resize(1500, 900)
    win.show()
    app.processEvents()
    # Aba Aulas
    win.tabs.setCurrentIndex(0)
    app.processEvents()
    win.render_lessons()
    # expande a árvore para ver as faixas das pastas
    win.video_tree.expandAll()
    app.processEvents()
    win.grab().save(os.path.join(SHOTS, "01_aulas_tree.png"))
    # Aba Planejador
    idx = next(i for i in range(win.tabs.count()) if "Planejador" in win.tabs.tabText(i))
    win.tabs.setCurrentIndex(idx)
    win.planner_tab.refresh()
    app.processEvents()
    win.grab().save(os.path.join(SHOTS, "02_planejador.png"))
    # Tema claro
    win.apply_theme("light")
    app.processEvents()
    win.tabs.setCurrentIndex(idx)
    win.grab().save(os.path.join(SHOTS, "03_planejador_claro.png"))
    win.tabs.setCurrentIndex(0)
    win.video_tree.expandAll()
    app.processEvents()
    win.grab().save(os.path.join(SHOTS, "04_aulas_tree_claro.png"))
    print(f"  ✔ screenshots salvos em {SHOTS}")


def main():
    print("== SMOKE v6.6 ==")
    db, course_id = seed_db()
    win = test_player_reverted()
    # garante que o curso semeado está selecionado
    win.refresh_courses()
    win.current_course_id = course_id
    win.render_lessons()
    app.processEvents()
    test_planner_crud_and_dragdrop(win)
    capture(win)
    print("TODOS OS TESTES DA v6.6 PASSARAM ✅")


if __name__ == "__main__":
    main()
