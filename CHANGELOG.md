# Changelog — TGClassPlayer

## v5.0.0

Reescrita completa do aplicativo (PT-BR), mantendo a proposta central:
conectar à API do Telegram do usuário e organizar seus cursos (supergrupos)
em uma videoteca premium por **tópicos → sumários → aulas**.

### Adicionado
- **Streaming sob demanda em blocos** (`stream_cache.StreamSession`): baixa
  apenas os blocos solicitados pelo player (HTTP Range), com leitura
  antecipada (*read-ahead*) e *seek* instantâneo. **Não baixa o vídeo inteiro
  e não armazena** o arquivo (cache temporário apagado ao fechar).
- **Player premium HTML5** (`player_html.py` + `player.py`): barra de
  progresso com prévia de buffer, ±10s, velocidade, volume, PiP, tela cheia,
  atalhos de teclado, spinner e tratamento de erros. Fallback para
  QtMultimedia quando o WebEngine não está disponível.
- **Retomar de onde parou** e gravação automática de progresso por aula.
- **Edição total** (`db.py` + `dialogs.py` + menus de contexto): cursos
  (renomear, cor, reordenar, excluir), tópicos/sumários (criar, renomear,
  reordenar, excluir, editar texto) e aulas (título, tópico, hashtags,
  notas, favoritos, assistida/não assistida).
- **Tema premium dark** (`style.py`) e UI de 3 painéis com busca e filtros.
- **Geração de .exe**: `TGClassPlayer.spec` (PyInstaller, coleta automática de
  QtWebEngine/Pyrogram/TgCrypto), `build_exe.bat`, `run_dev.bat`,
  `requirements.txt` e ponto de entrada `TGClassPlayer.py`.
- Documentação `README.md` e `COMO_USAR.txt` em PT-BR.

### Corrigido
- **Travamento (travar) ao assistir/avançar**: causado pelo download
  sequencial do arquivo completo na v4. Agora o carregamento é por blocos
  sob demanda, eliminando a espera e o congelamento.

### Notas técnicas
- SQLite em modo WAL; migrações suaves de schema (`_ensure_column`).
- `replace_videos` **preserva edições do usuário** (título/tópico/favorito/
  progresso) ao re-sincronizar o curso.
- Código validado com `py_compile` e `pyflakes` (limpo) e *smoke tests* de
  `db.py` e do gerador de HTML do player.
