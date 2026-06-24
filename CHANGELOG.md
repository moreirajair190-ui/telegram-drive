# Changelog — TgPlayer

## v6.6.0

Foco desta versão: **reverter o player do Windows ao formato anterior**
(Telegram como player principal + VLC como segunda opção), **corrigir o "bug
roxo" das divisões na árvore de aulas** e **adicionar um Planejador de aulas**
completo (Kanban + calendário).

### 📲 Player revertido (sem player embutido)

- **Removido o player embutido (QtWebEngine).** A reprodução voltou ao formato
  anterior, como pedido:
  - **Player principal:** abrir a aula **no Telegram instalado** (Telegram
    Desktop, **64Gram** ou Telegram oficial), reproduzindo o vídeo **direto
    dentro do app** do Telegram (via `tg://` com fallback para `t.me`).
  - **Segunda opção:** **VLC local** (streaming).
- Botões do painel de detalhes: **"📲 Assistir no Telegram"** (destaque),
  **"🎬 Assistir no VLC"** e **"🗓 Adicionar ao planejamento"**.
- Duplo clique numa aula abre direto no Telegram.
- Removidos `player.py` e `tests/test_player_ui.py`; o `TgPlayer.spec` e o
  `requirements.txt` não coletam/instalam mais QtWebEngine — **executável menor**.
  (O `player_html.py` continua, pois é usado pelo streaming web do serviço.)

### 🎨 Árvore de aulas: fim do "bug roxo" + rolagem suave

- As pastas/tópicos e a seleção agora são desenhadas por um **delegate**
  (`LessonTreeDelegate`) que pinta a **linha inteira como uma faixa contínua**,
  sem as emendas/blocos roxos quebrados entre as colunas (Tipo/Duração/Status).
- Faixas suaves por profundidade (módulo › subtópico › tipo) e um realce de
  seleção elegante com um filete de acento à esquerda.
- **Barra de rolagem mais suave**: mais fina, arredondada, trilho transparente
  e alça que ganha cor de destaque ao passar o mouse/arrastar.

### 🗓 Planejador de aulas (novo!)

- Nova aba **🗓 Planejador**: um **quadro estilo Kanban/Trello** com colunas
  **Backlog**, **Para o dia**, **Esta semana** e **Concluído**, com **cartões
  que você arrasta** entre as colunas.
- **Calendário integrado e funcional**: clique num dia para ver/agendar as aulas
  daquele dia; **arraste um cartão e solte no calendário** para agendá-lo. Dias
  com aulas agendadas ficam destacados.
- Adicione aulas dos seus **grupos/canais/supergrupos** do Telegram pelo botão
  **"+ Aula"** ou pelo menu de contexto da aba Aulas
  (**"🗓 Adicionar ao planejamento"**). Também dá para criar **tarefas livres**.
- Cartões mostram curso/módulo e progresso; menu de contexto para mover, mudar
  cor, marcar como concluído, abrir no Telegram ou remover.
- Integrado ao **Acompanhamento**: ao mudar o planejamento, o dashboard atualiza.
- Persistência em nova tabela `plan_items` (migração suave; bancos antigos
  continuam funcionando).

### ✅ Qualidade

- 13 testes passando + novo smoke headless `tests/smoke_v66.py` validando o
  player revertido, o CRUD/drag-drop do Planejador e capturando screenshots das
  3 abas nos temas claro e escuro.

## v6.5.1

Foco desta versão: **revisão geral do app Windows** — layout mais bonito,
suave e consistente, mais limpeza de código e correção de detalhes.

### ✨ Layout e experiência

- **Estados vazios amigáveis**: as listas de **cursos** e **matérias** e a aba
  **Arquivos** agora mostram uma mensagem central acolhedora quando estão vazias,
  em vez de uma caixa em branco (ex.: "Nenhum curso ainda. Conecte-se e use
  '+ Adicionar cursos'.").
- **Painel de detalhes da aula reorganizado**: o botão **"▶ Assistir aqui"** ficou
  maior e em destaque; os botões secundários (Telegram / VLC) e as ações da aula
  (Salvar ponto, Favorito, Editar, Assistida) ganharam altura consistente e foram
  agrupados sob a seção **"AÇÕES DA AULA"** num grid 2×2.
- **Cabeçalho do curso** com barra de progresso e contadores realinhados (o texto
  "X/Y aulas · Zh" agora fica à direita, sem se espremer).
- **Árvore de aulas**: as colunas (Tipo, Duração, Status) passaram a se ajustar
  ao conteúdo com a primeira coluna esticando — antes a coluna **"Status"** ficava
  cortada na borda direita.
- Cursor de "mãozinha" nos botões de ação e ajustes finos de espaçamento/QSS.

### 🧹 Limpeza de código

- Removido o módulo morto **`vlc_embed.py`** (backend libVLC antigo, sem nenhuma
  referência — substituído pelo player QtWebEngine).
- Removido o arquivo obsoleto **`MUDANCAS_v6.4.15.txt`** (já coberto pelo CHANGELOG).
- Removidos imports não usados (`ctypes`/`ctypes.wintypes` em `app.py`, `QFont`
  em `study_tab.py`) — `pyflakes` limpo.
- Comentário desatualizado no `TgPlayer.spec` (menção ao QMediaPlayer) corrigido.

### ✅ Qualidade

- 13 testes passando + smoke test headless de toda a UI (3 abas) e do player
  embutido (QtWebEngine offscreen).

## v6.5.0

Foco desta versão: **player embutido de volta no app Windows** (rápido, sem
depender do VLC) e **correção da lentidão do vídeo na web**.

### 🎬 Player embutido (Windows) — NOVO

- O player local voltou, agora baseado em **QtWebEngine** (Chromium do Qt). Ele
  carrega a MESMA página HTML5 do player web (`player_html.build_player_html`),
  servida pelo servidor local em `/player/{token}` — ou seja, **mesma origem**
  do vídeo (evita o bloqueio de mídia cross-origin do Chromium).
- **Partida rápida**: o backend faz *faststart virtual* (monta `ftyp+moov` em
  memória e serve antes do `mdat`), então o vídeo começa em poucos segundos —
  **sem a demora do VLC** para abrir o stream HTTP.
- Novo botão **"▶ Assistir aqui"** (principal) no painel de detalhes; duplo
  clique numa aula também abre o player embutido. "Telegram" e "VLC" ficam como
  alternativas. Há uma ação equivalente no menu de contexto.
- Overlay de carregamento com barra de progresso de buffer; se demorar mais que
  ~5 s, aparece o atalho "Abrir no VLC". Atalhos: Espaço (play/pause), F (tela
  cheia), M (mudo), Esc (fechar).
- Retomada automática da posição salva e **salvamento de progresso** ao fechar
  (conclui a aula automaticamente ao assistir ~95%).
- Robustez: se o QtWebEngine não estiver disponível no ambiente, o app oferece o
  VLC automaticamente, sem quebrar (`is_webengine_available()`).
- **Build (`TgPlayer.spec`)**: QtWebEngine deixou de ser excluído e passou a ser
  coletado (core + widgets + recursos como `QtWebEngineProcess`, `.pak`, ICU).
  Sem isso, o player embutido abriria em branco no `.exe`.

### 🌐 Web — correção da lentidão para carregar o vídeo

- O proxy `/api/stream` criava **uma `aiohttp.ClientSession` nova por
  requisição** — cada Range pedido pelo `<video>` pagava um novo handshake, o
  que deixava o vídeo lento/"sem carregar". Agora há uma **sessão keep-alive
  global** (TCP reaproveitado, pool generoso), criada no startup e fechada no
  shutdown.
- Cabeçalhos `Cache-Control: no-store, no-transform` e `X-Accel-Buffering: no`
  evitam que CDNs/proxies (ex.: Cloudflare) bufferizem o stream e atrasem a
  partida.
- `404` do upstream agora vira `stream_expired` (o frontend pode refazer o
  preparo) em vez de erro genérico.

## v6.4.15

Foco desta versão: **acompanhamento redesenhado** (dashboard de estudos) e a
**correção do erro de sessão do Telegram** que impedia os usuários de listar
cursos / receber o código.

### 🔴 Correção: `401 AUTH_KEY_UNREGISTERED` ao adicionar cursos

Vários usuários viam o erro:

> `Telegram says: [401 AUTH_KEY_UNREGISTERED] - The key is not registered in the
> system. Delete your session file and login again (caused by
> "messages.GetDialogs")`

**Causa:** o arquivo de sessão local (`tgclassplayer.session`) continuava
existindo, mas o servidor do Telegram já havia **revogado a chave de
autenticação** (sessão encerrada em outro dispositivo, troca de senha 2FA ou
expiração por inatividade). O app validava o login apenas com `get_me()` — que
às vezes passa — e só falhava depois, ao chamar `messages.GetDialogs`. Não havia
recuperação automática: o usuário precisava apagar o arquivo manualmente.

**O que mudou:**
- Nova exceção `SessionRevokedError` e o detector `_is_auth_revoked_error()`,
  que reconhecem `AUTH_KEY_UNREGISTERED`, `AUTH_KEY_INVALID`,
  `AUTH_KEY_DUPLICATED`, `SESSION_REVOKED`, `SESSION_EXPIRED` e
  `USER_DEACTIVATED`, tanto pela classe `Unauthorized` do Pyrogram quanto pela
  mensagem de texto crua.
- `clear_session_files()` **apaga automaticamente** os arquivos de sessão
  inválidos (`.session` e `.session-journal`) — o usuário não precisa mais fazer
  isso à mão.
- `ensure_connected()` foi reescrito: ao detectar sessão revogada, limpa o
  arquivo e **recria um client limpo já conectado**, devolvendo
  `{"authorized": False, "session_revoked": True}`. Assim o login (envio de
  código) funciona na mesma hora, sem precisar fechar/reabrir o app.
- `list_dialog_courses()` e `sync_course()` agora capturam o erro de chave
  revogada e disparam a limpeza + `SessionRevokedError` em vez de repetir a
  falha.
- Na interface (`app.py`), `add_courses_from_telegram()`,
  `sync_current_course()` e `try_quick_connect()` tratam a sessão revogada
  mostrando um aviso amigável ("Sua sessão expirou…") e **abrindo o login
  automaticamente** para renovar a conta.

### 📊 Acompanhamento redesenhado
- Aba **Acompanhamento** reestruturada como dashboard (cards de métricas,
  metas, gráficos de horas/aulas/progresso, Pomodoro e checklist).

## v6.4.0

Foco desta versão: **partida quase instantânea** do vídeo, um **player
redesenhado** com identidade visual única e uma nova aba **🗂️ Arquivos** que
transforma qualquer chat numa "nuvem" navegável. Tudo continua em
Python/PySide6/Pyrogram/aiohttp — sem Rust/React. A arquitetura de streaming
(servidor aiohttp local + `stream_cache.py` + backends de player) foi
preservada.

### 🔴 Partida rápida (2–5 s em vez de ~60 s)
- **Faststart virtual:** quando o `moov` está no FIM do arquivo (não-faststart),
  montamos em memória um cabeçalho `ftyp + moov` com os offsets de chunk
  (`stco`/`co64`) corrigidos e servimos a ordem `ftyp → moov → mdat`. O servidor
  mapeia offsets lógicos → físicos sob demanda, sem baixar a cauda inteira.
  Implementado em `src/tgplayer/mp4_faststart.py`.
- **Sintonia de início:** primeiro bloco de **512 KiB**, **8 downloads
  paralelos** no arranque e **orçamento de 2 MiB** para os primeiros bytes,
  reduzindo a latência do primeiro frame.
- **Warm-up ao SELECIONAR a aula** (debounce ~400 ms): aquece o início e monta o
  cabeçalho faststart em 2º plano; ao clicar em "Assistir", a partida já está
  quente.
- **Fallback seguro:** se a análise faststart falhar por qualquer motivo, o
  streaming volta ao comportamento anterior — a reprodução nunca quebra.

### 🟠 Player redesenhado
- **Cor de acento única** (índigo `#7c5cff`): removidos os tons ciano/azul/
  amarelo conflitantes.
- **Menu de engrenagem ⚙:** Qualidade, Velocidade e "Abrir no VLC" reunidos num
  único pop-up escuro, deixando a barra de controles limpa.
- **Barra de progresso premium:** faixa de buffer, *tooltip* de tempo e
  *thumb* com realce ao passar o mouse.
- **Cabeçalho sem duplicidade:** título + `posição` · `resolução` · `⚡ backend`
  (sem badges repetidos).
- **Overlay de carregamento** com **spinner animado + porcentagem**.
- Todos os atalhos preservados (Espaço, ←/→, J/L, ↑/↓, F, M, Esc, D, [ ], 0–9,
  N/P). O mesmo visual foi aplicado ao `player_html.py`.

### 🟢 Nova aba 🗂️ Arquivos
- Navegação de **toda a mídia** de um chat (vídeo/PDF/imagem/zip/áudio) em
  **grade com miniaturas**, **busca** e **filtros por tipo**.
- **Baixar para o disco com progresso** e **enviar arquivo do disco com
  progresso** (com mensagem amigável quando faltam permissões de envio).
- **Pré-visualização de imagem**; **vídeo abre no player interno**; **copiar
  link t.me** para chats públicos. Inspirada em `caamer20/Telegram-Drive`.

### 🔧 Build mais robusto
- `build_exe.bat` **nunca fecha sozinho** em erro (ponto de saída único com
  `pause`), **verifica o venv** (`where python`) e afrouxa o Pyrogram para
  `>=2.0.106,<2.1`.
- `run_dev.bat` com a mesma proteção e checagem de ativação do venv.
- Novo `build_exe.ps1` (alternativa em PowerShell, com `Read-Host` no final).

### Testes
- Novos `tests/test_faststart.py` (cabeçalho faststart + mapeamento lógico/
  físico) e `tests/test_files_tab.py` (classificação de mídia + filtros da aba).

## v6.3.0

Foco desta versão: **portar ideias e lógica** do projeto de referência
`caamer20/Telegram-Drive` (Tauri/Rust/React, "Telegram como nuvem") para o
TgPlayer — **reimplementadas em Python/PySide6/Pyrogram/aiohttp**, sem copiar
código Rust/React. Onde a referência usava MSE/mp4box no navegador (remux
fMP4), aqui usamos reprodução nativa (libVLC/QMediaPlayer) e descartamos o
remux. A arquitetura atual foi preservada (servidor aiohttp local +
`stream_cache.py` + backends de player).

### Streaming mais rápido e à prova de corrupção (Fase A)
- **Alinhamento de offset do CDN (512 KiB):** todas as leituras de Range são
  alinhadas para baixo na fronteira de 512 KiB que o CDN do Telegram usa,
  eliminando corrupção de MP4 por requisições desalinhadas.
- **Descoberta do `moov` em 3 passos** (128K → 512K → cauda): localiza o átomo
  `moov` mesmo em arquivos *non-faststart* (moov no fim), com varredura
  estruturada + busca por assinatura validando `mvhd`.
- **Cache persistente do `moov` (SQLite, LRU ~200):** boot instantâneo na 2ª
  reprodução; pré-aquece direto o intervalo do `moov`.
- **Cancelamento limpo:** ao fechar uma sessão, os downloads em voo são
  cancelados de forma ordenada (sem travar o app).

### Qualidade e adaptação (Fase B)
- **Presets de qualidade** (360/480/720/1080/original) com mapa de *throttle*
  por qualidade e **modo adaptativo** (mede banda numa janela de 3s e escolhe a
  qualidade por limiares).
- **Seletor de qualidade no player** (engrenagem ⚙), com trava anti-*upscale*
  (não oferece resolução acima da fonte) e botão "Auto".

### Player premium (Fase C)
- **Badges** sobre o vídeo: qualidade, resolução, modo (Auto/Manual) e posição
  na playlist.
- **Overlay de debug (tecla D):** velocidade, segundos em buffer, modo, tamanho,
  `moov` e botão para limpar cache.
- **Navegação entre aulas (‹ ›) + auto-play da próxima** (contagem de 5s ao
  chegar a ~92% ou ao terminar).
- **Atalhos completos:** Espaço, ←/→, J/L (±10s), ↑/↓ (volume), F, M, Esc, D,
  [ / ] (velocidade), 0–9 (saltar %), N/P (próxima/anterior).
- **Velocidade de reprodução persistente por curso.**

### Miniaturas e metadados (Fase D)
- **Cache de miniaturas com poda LRU** (até 400 arquivos / 256 MB, preservando o
  ativo) — ideia portada do `preview.rs`.
- **Pré-busca de metadados** (resolução/duração) em 2º plano, persistida em
  `moov_cache` e na tabela `videos`.
- **VideoMetaBadge:** resolução compacta (ex.: `1080p`) nas listas e no painel
  de detalhes.

### Rede, proxy e resiliência (Fase E)
- Seção **"Streaming & Rede"** (menu Ferramentas): qualidade/adaptativo,
  **limite de banda (kbps)**, tamanho do bloco, re-tentativas e **modo conexão
  instável**, além de **proxy SOCKS5/HTTP/MTProto** (host/porta/usuário/senha).
- **Re-tentativas por bloco com backoff** e retomada do offset já gravado (não
  reinicia o bloco do zero) — downloads resilientes em redes ruins.
- **Backoff/retry no SQLite** (`busy_timeout` + tentativas) contra
  *"database is locked"*.
- **Widget de banda em tempo real** na barra superior (some sem streaming
  ativo).

### Links e organização (Fase F)
- **Copiar link nativo do Telegram (`t.me/{canal}/{id}`)** para canais/grupos
  PÚBLICOS, com aviso claro quando o canal é privado.

### Toques de produto (P7)
- **Continuar assistindo (resume):** filtro virtual na barra de matérias com as
  aulas em andamento, ordenadas pelo acesso mais recente.
- **% de progresso por curso** na lista de cursos.
- **Estados vazios** mais amigáveis.

### Mapeamento "ideia da referência → recurso do TgPlayer"

| Referência (`caamer20/Telegram-Drive`) | Reimplementação no TgPlayer |
| --- | --- |
| Alinhamento de offset do CDN (512 KiB) no leitor de stream | `stream_cache.align_down` + `CDN_ALIGNMENT` aplicado em `read_range` |
| Busca do átomo `moov` (faststart vs moov-no-fim) | `discover_moov` (3 passos) + `_scan_moov_box` (walk + assinatura) |
| Cache de metadados/preview por arquivo | tabela SQLite `moov_cache` (LRU 200) + `get/set_moov_cache` |
| Cancelamento de downloads em voo | `StreamSession.close()` aguarda cancelamento das tasks |
| Presets/adaptação de qualidade (`AdaptiveMediaPlayer.tsx`) | `quality.py` (presets, throttle, limiares) + modo adaptativo por janela de 3s |
| Remux fMP4 (MSE/mp4box no navegador) | **descartado**: reprodução nativa libVLC/QMediaPlayer |
| Cache de previews com poda preservando o ativo (`preview.rs`) | `ensure_thumbnail` + `_prune_thumb_cache` (LRU 400/256MB, preserva `keep`) |
| Tuning de rede / proxy | `StreamingSettingsDialog` + `_build_proxy` (SOCKS5/HTTP/MTProto) + `max_retries` |
| Modo conexão instável | re-tentativas com backoff em `_download_block` + `unstable_connection` |
| Copiar link nativo do Telegram | `telegram_message_link` + ação "Copiar link do Telegram (t.me)" |
| Continuar assistindo / progresso | `continue_watching` + `course_progress` + filtro `SUBJECT_CONTINUE` |

### Notas técnicas
- Sessão preservada (`tgclassplayer`) e migração de dados legados mantidas.
- Migrações idempotentes (`width`, `height`, `last_watched_at`, tabela
  `moov_cache`) via `_ensure_column` / `CREATE TABLE IF NOT EXISTS`.
- Todos os ajustes persistem via `get_setting`/`set_setting`.
- Smoke tests: alinhamento de CDN, descoberta de `moov`, cache hit/miss + LRU,
  qualidade adaptativa, retomada/continuar/progresso, metadados/`moov` parcial.

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
