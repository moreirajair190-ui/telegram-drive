"""Smoke test da retomada/assistida no banco (db.save_progress).

Executar:
    python tests/test_resume_progress.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tgplayer.db import Database


def main() -> None:
    tmp = tempfile.mktemp(suffix=".sqlite3")
    db = Database(tmp)
    with db.connect() as c:
        c.execute(
            "INSERT INTO courses(chat_id,title,added_at) VALUES('1','C',?)",
            (db.now(),),
        )
        c.execute(
            "INSERT INTO videos(course_id,chat_id,message_id,title,file_name,duration) "
            "VALUES(1,'1',10,'A','a.mp4',600000)"
        )
    vid = db.list_videos(1)[0]

    # 50% -> guarda posição, não marca como assistida.
    db.save_progress(vid.id, 300000, 600000)
    v = db.get_video(vid.id)
    assert v.position_ms == 300000 and v.watched_at is None
    print("[ok] retoma na posição salva (50%); ainda não assistida")

    # 93% -> marca como assistida.
    db.save_progress(vid.id, 558000, 600000)
    v = db.get_video(vid.id)
    assert v.watched_at is not None and v.progress >= 0.92
    print("[ok] marcada como assistida ao passar de ~92%")

    os.unlink(tmp)
    print("TODOS OS TESTES DE PROGRESSO/RETOMADA PASSARAM")


if __name__ == "__main__":
    main()
