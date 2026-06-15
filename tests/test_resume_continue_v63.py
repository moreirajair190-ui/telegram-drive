"""Smoke tests v6.3: continuar assistindo, progresso de curso, dimensões e metadados.

Executar:
    python tests/test_resume_continue_v63.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tgplayer.db import Database


def _seed(db: Database) -> None:
    with db.connect() as c:
        c.execute(
            "INSERT INTO courses(chat_id,title,username,added_at) VALUES('100','Curso','meucanal',?)",
            (db.now(),),
        )
        for mid in range(1, 6):
            c.execute(
                "INSERT INTO videos(course_id,chat_id,message_id,title,file_name,duration) "
                "VALUES(1,'100',?,?,?,600000)",
                (mid, f"Aula {mid}", f"aula{mid}.mp4"),
            )


def test_continue_watching() -> None:
    tmp = tempfile.mktemp(suffix=".sqlite3")
    db = Database(tmp)
    _seed(db)
    vids = db.list_videos(1)

    # Aula 1: 40% (em andamento). Aula 2: 95%+ (concluída). Aula 3: 5% (em andamento).
    db.save_progress(vids[0].id, 240000, 600000)
    db.save_progress(vids[1].id, 580000, 600000)
    db.save_progress(vids[2].id, 30000, 600000)

    cont = db.continue_watching()
    ids = {v.id for v in cont}
    assert vids[0].id in ids, "aula 40% deve aparecer em continuar assistindo"
    assert vids[2].id in ids, "aula 5% deve aparecer em continuar assistindo"
    assert vids[1].id not in ids, "aula concluída NÃO deve aparecer"
    print(f"[ok] continuar assistindo: {len(cont)} aulas (apenas em andamento)")
    os.unlink(tmp)


def test_course_progress() -> None:
    tmp = tempfile.mktemp(suffix=".sqlite3")
    db = Database(tmp)
    _seed(db)
    vids = db.list_videos(1)

    done, total = db.course_progress(1)
    assert total == 5 and done == 0, "curso recém-criado: 0/5"

    db.save_progress(vids[0].id, 600000, 600000)  # 100% -> assistida
    db.mark_watched(vids[1].id)                     # marca direto
    done, total = db.course_progress(1)
    assert total == 5 and done == 2, f"esperava 2/5, obtido {done}/{total}"
    print(f"[ok] progresso do curso: {done}/{total} ({int(done/total*100)}%)")
    os.unlink(tmp)


def test_dimensions_and_meta() -> None:
    tmp = tempfile.mktemp(suffix=".sqlite3")
    db = Database(tmp)
    _seed(db)

    # Atualiza dimensões via método de pré-busca (preserva valores existentes).
    db.set_video_dimensions("100", 1, width=1920, height=1080, duration=600)
    v = db.get_video(db.list_videos(1)[0].id)
    assert v.width == 1920 and v.height == 1080, "dimensões devem ser gravadas"
    print("[ok] set_video_dimensions grava width/height")

    # moov_cache só de metadados não apaga moov_offset já existente.
    db.set_moov_cache("100", 1, moov_offset=512, moov_size=4096, located=1)
    db.set_moov_cache("100", 1, width=1280, height=720)  # update parcial
    row = db.get_moov_cache("100", 1)
    assert row["moov_offset"] == 512, "moov_offset deve ser preservado (COALESCE)"
    assert row["width"] == 1280, "width deve ser atualizado"
    print("[ok] set_moov_cache parcial preserva moov_offset (COALESCE)")
    os.unlink(tmp)


def main() -> None:
    test_continue_watching()
    test_course_progress()
    test_dimensions_and_meta()
    print("TODOS OS TESTES v6.3 (resume/continuar/progresso/metadados) PASSARAM")


if __name__ == "__main__":
    main()
