# Changelog — TgPlayer

## v6.2.0

> **O projeto agora se chama TgPlayer** (antes: TGClassPlayer). O nome do
> executável, do entry point (`TgPlayer.py`) e do pacote Python (`tgplayer`)
> foram atualizados. **Seus dados são preservados**: login do Telegram, banco
> (`tgclassplayer.sqlite3`), progresso e configurações continuam funcionando —
> em modo `.exe`, se já existir a pasta antiga `%LOCALAPPDATA%\TGClassPlayer`,
> ela é reaproveitada automaticamente.

Foco desta versão: **consertar e turbinar o PLAYER INTERNO** (o que abre em
"Assistir agora"), que antes carregava devagar, ficava preso em `00:00 / 00:00`
com tela preta e tinha uma interface crua.

### 🚀 Carregamento muito mais rápido (sem travar em 00:00)

- **Pré-busca do índice `moov` do MP4.** Ao preparar o stream, o servidor local
  agora baixa **imediatamente** o **bloco 0** (início) **e os 2 últimos blocos**
  do arquivo. Em MP4 *não-faststart* o átomo `moov` (necessário para o player
  começar) fica **no fim**; antes o player ia ao fim, baixava blocos e só então
  voltava ao início — a causa raiz da espera enorme. Agora o índice já está
  pronto quando o player precisa dele.
- **Primeiro byte rápido (yield parcial).** O download de cada bloco passou a
  **gravar em disco incrementalmente** e a marcar quantos bytes já estão
  prontos. O `read_range` **libera ~256 KiB iniciais sem esperar** o bloco
  inteiro de 2 MiB — o vídeo começa a fluir em segundos.
- **Mais paralelismo, com cancelamento.** `Semaphore(3 → 6)` e
  `READ_AHEAD_BLOCKS (4 → 6)`. Todos os downloads em andamento são
  **cancelados ao fechar** a janela (libera rede; o cache é apagado).
- **Overlay "Carregando aula… NN%".** Em vez de tela preta morta, há um overlay
  com **porcentagem real de buffer** e, após ~6 s, o botão **"Está demorando?
  Abrir no VLC"**.

#### Por que a Opção 1 (e não só a 2 ou a 3)?

Foram avaliadas as 3 arquiteturas propostas:

- **Opção 1 — buffer de partida + readahead agressivo no servidor local
  (IMPLEMENTADA como base).** É a que resolve a *causa raiz* (moov no fim +
  espera do bloco inteiro) sem mudar o fluxo de streaming sob demanda já
  existente, **sem gravar o vídeo em disco permanentemente** e sem dependências
  novas obrigatórias. Melhor custo/benefício e risco baixo.
- **Opção 2 — pré-download dos primeiros N MiB para arquivo temporário.**
  Ajudaria a partida, mas **não resolve** o problema do `moov` no fim (que pode
  estar muito além dos primeiros N MiB) e tende a "baixar para depois tocar",
  contrariando o streaming sob demanda. Descartada como base; a pré-busca da
  *cauda* da Opção 1 cobre o ganho pretendido.
- **Opção 3 — libVLC embarcado na nossa janela (IMPLEMENTADA como backend
  preferencial quando disponível).** Ganha a **velocidade do VLC** dentro do
  app (via `set_hwnd`/`winId`), **sem abrir o VLC externo**. É **opcional**
  (`pip install python-vlc`): se a libVLC não estiver presente, o player cai
  automaticamente para o QMediaPlayer. Combinada com a Opção 1, dá o melhor
  resultado.

**Ordem de backends do player:** libVLC embarcado → QMediaPlayer (codecs
nativos do SO) → QtWebEngine (fallback final).

### 🎨 Interface premium

- Barra de controles **flutuante com auto-hide (~3 s)** em tela cheia.
- **Seek bar premium** desenhada à mão: faixa de **buffer carregado**, faixa
  reproduzida em gradiente, **thumb** com realce no hover e **tooltip de tempo**.
- Botões grandes e legíveis: **play**, **±10s**, **volume + mudo**,
  **velocidade 0.5–2x**, **tela cheia** e **Abrir no VLC**. **Cabeçalho** com o
  título e selo do backend em uso. Alto contraste.
- **Atalhos:** `Espaço` (play/pause), `←/→` (±10s), `↑/↓` (volume), `F` (tela
  cheia), `Esc` (sair da tela cheia), `M` (mudo).
- O **fallback QtWebEngine** continua intacto.

### ⏪ Retomada confiável

- Progresso salvo **a cada ~5 s** e **ao fechar**.
- Ao reabrir, o player **retoma exatamente** na posição salva quando a mídia
  carrega; se faltar **menos de 5 s** para o fim, **recomeça do zero**.
- Aviso discreto **"Retomando de mm:ss"**.
- Aula marcada como **assistida ✅** ao passar de **~92%**.

### 🔒 Privacidade / entrega

- O vídeo **nunca** é armazenado permanentemente: o cache é apagado ao fechar.
- `python-vlc` é **opcional** (documentado no `requirements.txt` e no README).
- O `.exe` continua compilando (spec atualizado; `python-vlc` coletado só se
  existir).

---

## v6.1.0

Correções a partir do feedback de uso real (capturas enviadas pelo usuário).

### Corrigido
- **Sincronização de fórum (bug grave "tudo caiu em General"):** agora cada
  tópico do fórum é lido **separadamente** via
  `get_chat_history(message_thread_id=tid)` (com fallback para
  `get_discussion_replies`). Antes, o histórico inteiro era varrido de uma vez e
  o Pyrogram não preenchia o `message_thread_id` de forma confiável, jogando
  **todos os vídeos e todos os sumários no tópico "General"**. Resultado: **cada
  tópico = uma matéria com o SEU próprio sumário e as SUAS próprias aulas.**
- **Player interno não reproduzia (`DEMUXER_ERROR_NO_SUPPORTED_STREAMS`):** o
  player **principal** passou a ser o **QtMultimedia**, que usa os **codecs
  nativos do Windows** (Media Foundation) e suporta H.264/AAC. O QtWebEngine
  (que vem **sem** codecs proprietários) virou apenas *fallback*. Adicionado um
  **aviso amigável** com "↻ Tentar de novo" e "Abrir no VLC" em caso de erro.
- **"Caixas pretas" cobrindo textos/widgets:** desativada a composição por GPU
  do Qt/QtWebEngine (`QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu` +
  `AA_ShareOpenGLContexts` / `AA_UseSoftwareOpenGL`) e garantido
  `background: transparent` nos rótulos (`QLabel`) — some o artefato de
  retângulos pretos em placas/drivers problemáticos e em builds empacotados.
- **Geração do .exe não funcionava:** o `TGClassPlayer.spec` usava argumentos
  **removidos no PyInstaller 6.x** (`win_no_prefer_redirects`,
  `win_private_assemblies`, `cipher`/`block_cipher`), o que abortava o build.
  Spec reescrito para o PyInstaller 6.x. Além disso, `requirements.txt` deixou
  de listar **`PySide6-Addons` separado** (causava conflito de versões no pip —
  o `PySide6` já o inclui) e o **TgCrypto virou opcional** (não trava o build em
  Pythons sem *wheel*, ex.: 3.13). O `build_exe.bat` instala as dependências de
  forma resiliente (TgCrypto com aviso, sem abortar).

## v6.0.0

Reconstrução completa da interface (do zero) e correção dos bugs críticos,
mantendo a proposta central e o streaming sob demanda.

### Adicionado
- **Detecção automática do tipo do chat** na sincronização
  (`telegram_service.py`), sem intervenção do usuário:
  - **Fórum** (supergrupo com tópicos): usa a **API bruta**
    `channels.GetForumTopics`. **Cada tópico = uma matéria**, com a sua própria
    lista de aulas (filtradas por *thread*) e o seu próprio sumário (mensagem
    fixada do tópico ou melhor candidata a "menu").
  - **Grupo/supergrupo normal:** matéria única (sumário = fixado do grupo).
  - **Canal:** lista linear cronológica (sumário = fixado, se houver).
  - **Não depende mais** de `search_messages(query="#")` (que falhava em produção).
- **Modelo de MATÉRIAS** no banco (`subjects`) substituindo o antigo
  `topics_json`, com migração suave v5 → v6.
- **Interface nova do zero** (`app.py`): barra superior com nome do
  curso/matéria e **barra de progresso geral** (assistidas/total + horas);
  abas **Aulas** e **Acompanhamento**; navegação por **matérias** e, dentro de
  cada matéria, **seções por módulo** (árvore Módulo → Aula → Tipo → vídeos).
- **Temas Claro 🌞 e Escuro 🌙** calibrados (`style.py`), alternância no topo e
  **persistência** no banco (`settings.theme`).
- **Filtros** Todas / Assistidas / Pendentes / ★ Favoritas + busca por título,
  hashtag ou módulo. Estado ✅/⬜, favorito ★ e progresso por aula na lista.
- **Edição total** com menus de contexto: cursos (renomear, **cor**, reordenar,
  excluir), matérias (criar, renomear, reordenar, excluir, **editar sumário**) e
  aulas (título, **matéria**, módulo, tipo, hashtags, notas, favorito,
  assistida/pendente).
- **Aba "Acompanhamento" 📊** (`study_tab.py` + `charts.py`): **Pomodoro**
  configurável, **tarefas/checklist** (prioridade + prazo) e **gráficos** em
  QPainter (tempo por dia, aulas por curso, anel de progresso) + cartões de
  resumo (*streak*, horas, % do curso). **Sem QtCharts** (sem dependências
  frágeis).

### Corrigido
- **Player interno bloqueava o vídeo** (mixed-content / origem distinta): a
  página do player passou a ser servida pela rota **`/player/{token}`** do
  **mesmo** servidor local (aiohttp), com URL **relativa** para o
  `/stream/{token}` — **mesma origem**. O player agora carrega via
  `web.load(QUrl(player_url))` em vez de `setHtml(...)`. Mantido o streaming em
  blocos (HTTP Range), o *resume* automático e o fallback QtMultimedia. A tela
  de erro tem botão **"Abrir no VLC"**.
- **Sumários misturados entre matérias**: o parser agora é **por matéria**
  (`summary_parser.py`), sem agrupar por prefixo global de hashtag. Cada matéria
  tem o seu próprio sumário e o casamento hashtag → aula é feito por matéria.

### Notas técnicas
- SQLite (WAL) com migrações suaves (`_ensure_column`) e v5 → v6.
- `replace_videos` **preserva edições do usuário** (título, matéria, favorito,
  progresso, notas) ao re-sincronizar.
- Código validado com `py_compile` e `pyflakes`; *smoke tests* do parser, do
  gerador de HTML do player e da preservação de edições no `db.py`.

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
