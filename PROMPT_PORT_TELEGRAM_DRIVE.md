# PROMPT COMPLETO — Portar ideias e lógica do projeto `caamer20/Telegram-Drive` para o **TgPlayer**

> **Para usar:** cole o conteúdo abaixo (a partir de "INÍCIO DO PROMPT") como uma única tarefa para o agente de IA, junto com o projeto **TgPlayer** indexado. Este documento descreve **o que** portar e **como adaptar**, NÃO contém implementação de código — é um plano de execução detalhado.

---

## Contexto da análise (resumo do projeto de referência)

`caamer20/Telegram-Drive` é um app **desktop multiplataforma** (Tauri + Rust + React/TypeScript) que transforma a conta do Telegram em um "drive" de nuvem ilimitado. Embora a finalidade dele seja gerenciamento de arquivos, **o coração técnico dele é exatamente o que o TgPlayer precisa: streaming de vídeo do Telegram com baixa latência, player premium, qualidade adaptativa e cache de metadados**. As partes mais valiosas para reaproveitar são:

| Área | Arquivo de referência | O que aprender |
|---|---|---|
| **Servidor de streaming HTTP com Range** | `src-tauri/src/server.rs` | Alinhamento de offset a fronteira de CDN (512 KB), parsing de `Range`, resposta `206 Partial Content`, *skip* de bytes de chunk |
| **Pipeline de streaming adaptativo (MSE/mp4box)** | `src/hooks/useAdaptiveStreaming.ts` (1158 linhas) | Descoberta rápida do `moov` (128 KB → 512 KB → tail), extração do átomo `moov`, *pre-warm* de cache HTTP para `moov-at-end`, throttle por qualidade, medição de banda |
| **Cache de metadados `moov`** | `src/hooks/moovCache.ts` | IndexedDB/cache LRU (50 entradas) das infos de faixa por arquivo → boot instantâneo na 2ª vez |
| **Metadados de vídeo (duração/resolução)** | `src-tauri/src/commands/video_metadata.rs`, `src/hooks/useVideoMetadata.ts` | Baixar só os primeiros ~2 MB, parsear `mvhd`/`tkhd`, cachear por 30 min |
| **Seletor de qualidade + modo adaptativo** | `src/components/shared/QualitySelector.tsx`, `src/types.ts` | Presets 360p/480p/720p/1080p/original, mapa de throttle, thresholds adaptativos por kbps |
| **Player premium (UI)** | `src/components/desktop/dashboard/AdaptiveMediaPlayer.tsx` (1194 linhas) | Overlay de loading com %, badges de qualidade/resolução, overlay de debug (tecla D), barra flutуante em fullscreen, atalhos, controle de volume com slider |
| **Transcode HLS sob demanda** | `src-tauri/src/transcode.rs` | FFmpeg → variantes HLS 360/480/720/1080, segmentos `.ts` de 4s, cache de 5 GB com LRU |
| **Remux fMP4 on-the-fly** | `src-tauri/src/fmp4_remux.rs` | Converter MP4 progressivo (moov-at-end) em fMP4 por *stream-copy* (sem reencode) para MSE |
| **Compartilhamento / links** | `src-tauri/src/commands/sharing.rs` | Links com senha (bcrypt) + expiração + revogação; copiar link nativo do Telegram |
| **Otimizador de rede / proxy** | `src-tauri/src/vpn_optimizer.rs` | SOCKS5/MTProto, chunk size ajustável, retries com backoff, keep-alive, limite de banda |
| **Bandwidth widget** | `src/components/desktop/dashboard/BandwidthWidget.tsx`, `bandwidth.rs` | Medição/exibição de banda up/down em tempo real |
| **Atalhos de teclado globais** | `src/hooks/useKeyboardShortcuts.ts` | Ctrl+A, Ctrl+F (busca), Delete, Esc, Enter |
| **DB robusta** | `src-tauri/src/db.rs` | Abertura/migração com retry + backoff exponencial (anti "database is locked") |
| **Previews/thumbnails com cache LRU** | `src-tauri/src/commands/preview.rs` | Cache de previews (30 arquivos / 256 MB) com poda automática preservando o ativo |

> **Diferença fundamental de stack:** a referência é Rust/React; o **TgPlayer é Python/PySide6 + Pyrogram + aiohttp**. **Portar = reimplementar a LÓGICA e as IDEIAS em Python/Qt**, não copiar código. Onde a referência usa MSE/mp4box no navegador, o TgPlayer usa libVLC/QMediaPlayer nativos — então alguns recursos (ex.: remux fMP4 para MSE) são **desnecessários** e devem ser substituídos pelo equivalente nativo.

---

# INÍCIO DO PROMPT

Você é um engenheiro sênior trabalhando no **TgPlayer** — um app desktop em **PySide6 + Pyrogram + aiohttp** para **assistir cursos/videoaulas hospedados no Telegram de forma fácil e prática**. O projeto já está indexado neste repositório (branch `genspark_ai_developer`). Quero que você **incorpore as melhores ideias e a lógica** do projeto de referência `https://github.com/caamer20/Telegram-Drive` (app Tauri/Rust/React de "Telegram como drive"), **adaptando tudo para o caso de uso de cursos/videoaulas**. Reimplemente em Python/Qt (não copie código Rust/React). Seja criativo, mas mantenha a arquitetura atual do TgPlayer (servidor aiohttp local + `stream_cache.py` + backends de player libVLC/QMediaPlayer/QtWebEngine).

A seguir, a lista priorizada de funcionalidades a implementar. **Implemente da prioridade ALTA para a BAIXA.** Para cada item, há a origem na referência e a adaptação esperada no TgPlayer.

---

## PRIORIDADE 1 — Streaming mais rápido e confiável (núcleo)

### 1.1 Alinhamento de offset a fronteira de CDN (anti-corrupção de MP4)
- **Origem:** `server.rs` (`build_media_response`).
- **Problema que resolve:** o CDN do Telegram arredonda offsets para baixo até a fronteira de 512 KB; pedidos `Range` desalinhados retornam dados deslocados → corrompem o parsing das *boxes* MP4 e quebram o seek.
- **Adaptar no TgPlayer (`stream_cache.py` / `telegram_service.py`):**
  - Ao receber um `Range: bytes=start-`, **arredonde `start` para baixo** até múltiplo de `CDN_ALIGNMENT = 524288` (512 KB).
  - Calcule o índice de chunk MTProto (use `CHUNK_SIZE = 1 MiB` ou 512 KiB, divisível pelo mínimo do Pyrogram) e descarte (`bytes_to_skip`) os bytes entre o offset alinhado e o `start` real antes de servir ao cliente.
  - Garanta o invariante `aligned_start <= start`.
  - Logue `requested / cdn_aligned / chunk_index / bytes_to_skip` em DEBUG.

### 1.2 Descoberta rápida do átomo `moov` (boot quase instantâneo)
- **Origem:** `useAdaptiveStreaming.ts` (`discoverMoov`, `extractMoovAtom`, constantes `MOOV_DISCOVERY_BYTES=128KB`, `MOOV_RETRY_BYTES=512KB`, `MOOV_TAIL_BYTES=512KB`).
- **Adaptar:** o TgPlayer **já** faz prefetch do moov em `stream_cache.py`. Melhore para a estratégia em 3 passos:
  1. Buscar os **primeiros 128 KB** (cobre `ftyp` + `moov` na maioria dos arquivos).
  2. Se não achar `moov`, **retry com 512 KB**.
  3. Se ainda não achar (arquivo "moov-at-end"), buscar os **últimos 512 KB** (tail) e prefazer o cache desses blocos.
  - Implemente um **scanner de boxes** que valida `moov`: tamanho plausível (≤ 64 MB), primeiro filho deve ser `mvhd` (evita falso-positivo de bytes `moov` dentro de `mdat`).
  - Quando o moov estiver no fim, **pré-aqueça o cache** dos blocos finais (equivalente ao `warmProgressiveMoovCache`) para que o player nativo encontre o moov sem round-trip lento.

### 1.3 Cache persistente de metadados `moov` (boot instantâneo na 2ª vez)
- **Origem:** `moovCache.ts` (IndexedDB, LRU de 50 entradas, chave `folderId:messageId`).
- **Adaptar:** crie uma tabela SQLite no `db.py` (ex.: `moov_cache`) com chave `(chat_id, message_id)` guardando:
  - tamanho do arquivo, offset/length do moov, duração (ms), largura/altura, codec, nº de faixas, `timestamp`.
  - LRU: limite ~200 entradas (cursos têm muitas aulas), evicção das mais antigas.
  - Ao abrir uma aula já assistida antes, **pular a descoberta do moov** e ir direto ao streaming → loading muito mais rápido.

### 1.4 Servidor de streaming: paralelismo e cancelamento
- **Origem:** lógica de `iter_download` + `chunk_size` + `skip_chunks` do `server.rs`.
- **Adaptar (já parcialmente feito):** mantenha `Semaphore(6)`, `READ_AHEAD_BLOCKS=6`, *partial yield* de 256 KiB no primeiro byte. Adicione **cancelamento limpo** quando o usuário troca de aula (abortar tasks de download em voo, como o `AbortController` da referência).

---

## PRIORIDADE 2 — Qualidade adaptativa + seletor de qualidade

### 2.1 Presets de qualidade e throttle de banda
- **Origem:** `types.ts` (`QUALITY_THROTTLE_MAP`, `ADAPTIVE_THRESHOLDS`, `StreamingQuality`).
- **Adaptar:** adicione um conceito de qualidade ao TgPlayer:
  - Presets: `360p, 480p, 720p, 1080p, original`.
  - Mapa de throttle (kbps): `360p=500, 480p=1000, 720p=2500, 1080p=5000, original=0(ilimitado)`.
  - **Modo adaptativo:** medir banda real (janela de 3 s) e escolher a qualidade pelos thresholds: `≥4000kbps→1080p, ≥2000→720p, ≥800→480p, else 360p`.
  - Persistir a escolha em `settings` (`db.py` / `get_setting/set_setting`), chave `streaming_quality` + `adaptive_mode`.

### 2.2 Transcode HLS sob demanda (se FFmpeg disponível)
- **Origem:** `transcode.rs` (FFmpeg → HLS 360/480/720/1080, segmentos de 4 s, cache 5 GB LRU) + `useCachedVariants.ts`.
- **Adaptar (OPCIONAL, atrás de "FFmpeg disponível?"):**
  - Detectar `ffmpeg` no PATH / pasta do app (reusar a lógica do `vlc_locator.py` como modelo → criar `ffmpeg_locator.py`).
  - Sob demanda, gerar variantes HLS por aula em `cache/streaming/hls/{chat}_{msg}/{quality}/index.m3u8`.
  - Servir playlist/segmentos pelo mesmo servidor aiohttp (`_handle_hls`).
  - Cache LRU de 5 GB; botão "limpar transcodes" desta aula.
  - **Importante:** isto é um *nice-to-have*. Como o TgPlayer usa libVLC nativo (que lida bem com MP4 progressivo), o **throttle por banda (2.1) já cobre 90% do valor** sem FFmpeg. Implemente HLS apenas se for trivial; senão, deixe atrás de uma flag em config.

### 2.3 Seletor de qualidade na UI do player
- **Origem:** `QualitySelector.tsx`.
- **Adaptar:** menu no `player.py` (botão de engrenagem nos controles) listando as qualidades; impedir *upscale* acima da resolução de origem (ler do `moov_cache`); toggle "Auto (adaptativo)"; mostrar a banda medida ao lado.

---

## PRIORIDADE 3 — Player premium (UI/UX)

> O TgPlayer já tem player premium (Fase v6.2.0). **Complemente** com estas ideias do `AdaptiveMediaPlayer.tsx`:

### 3.1 Badges sobre o vídeo
- Badge de **qualidade atual** (ex.: "720p · 2.5k") no canto.
- Badge de **resolução** "Source: 1920×1080 · Playing: 1280×720".
- Badge de **modo** (Original / Throttled / HLS).

### 3.2 Overlay de debug (tecla **D**)
- Painel translúcido (mono) com: velocidade medida (Kbps/Mbps), cap de qualidade, **segundos no buffer** (`buffered.end - currentTime`), modo, tamanho, e botão "limpar cache desta aula".
- Persistir on/off em `settings` (`debug_overlay`).

### 3.3 Navegação entre aulas dentro do player
- **Origem:** botões `ChevronLeft/Right` + props `onNext/onPrev/currentIndex/totalItems`.
- **Adaptar:** no `player.py`, setas ‹ › para **aula anterior/próxima da mesma matéria/curso**, com indicador "3/12". Ao terminar uma aula (≥92%), oferecer **auto-play da próxima** (countdown de 5 s, cancelável) — feature de "binge" típica de plataformas de curso.

### 3.4 Atalhos completos de player
- **Origem:** handler de teclado do `AdaptiveMediaPlayer.tsx` + `useKeyboardShortcuts.ts`.
- **Adaptar/garantir:** `Space` play/pause, `←/→` (J/L) ±10s, `↑/↓` volume, `F` fullscreen, `M` mute, `Esc` fechar, `D` debug, `[` `]` velocidade, `0–9` pular para %, **`N` próxima aula / `P` anterior**.

### 3.5 Barra flutuante e controles auto-hide em fullscreen
- Reaproveitar a ideia do "Fullscreen overlay toolbar" (gradiente inferior, play/volume/qualidade/sair) com auto-hide já existente no TgPlayer.

---

## PRIORIDADE 4 — Metadados, miniaturas e biblioteca de cursos

### 4.1 Metadados de vídeo por aula (duração/resolução) com cache
- **Origem:** `video_metadata.rs` + `useVideoMetadata.ts` (baixar ~2 MB, parsear `mvhd`/`tkhd`, cache 30 min).
- **Adaptar:** ao listar aulas, **pré-buscar duração e resolução** (parser já existe parcialmente em `summary_parser.py`/`stream_cache.py`) e gravar no `moov_cache`. Exibir duração ("12:34") e badge de resolução em cada item da lista de aulas. Batch: processar a lista da matéria em segundo plano.

### 4.2 Miniaturas/thumbnails com cache LRU
- **Origem:** `preview.rs` (cache 30 arquivos / 256 MB, poda preservando o ativo).
- **Adaptar:** baixar o thumbnail do vídeo do Telegram (Pyrogram expõe `thumbs`), salvar em `cache/thumbs/{chat}_{msg}.jpg`, exibir como capa da aula no grid. Poda LRU automática. Fallback: gerar 1 frame via FFmpeg se disponível.

### 4.3 `VideoMetaBadge` na lista
- **Origem:** `VideoMetaBadge.tsx`.
- **Adaptar:** componente Qt reutilizável que mostra duração + resolução + ícone de tipo nas listas de aulas.

---

## PRIORIDADE 5 — Robustez (rede, DB, proxy)

### 5.1 Abertura/migração de DB com retry + backoff
- **Origem:** `db.rs` (5 tentativas, backoff exponencial, anti "database is locked").
- **Adaptar:** envolver `connect()`/`init()` do `db.py` com retry exponencial (já há WAL; adicionar resiliência).

### 5.2 Suporte a proxy SOCKS5 / MTProto
- **Origem:** `vpn_optimizer.rs` (`ProxyConfig`).
- **Adaptar:** o Pyrogram aceita `proxy={...}` (SOCKS5) nativamente. Adicionar em Configurações: tipo (SOCKS5/MTProto), host, porta, usuário, senha. Passar ao `Client` do Pyrogram. Útil para alunos em redes restritas.

### 5.3 Tuning de rede ("modo conexão instável")
- **Origem:** `VpnConfig` (chunk size, retries, backoff, keep-alive, limite de banda).
- **Adaptar:** opções em Configurações:
  - `chunk_size_kb` (256/512/1024) para o download MTProto.
  - retries + backoff nas chamadas de download.
  - limite de banda down (kbps).
  - keep-alive periódico.
  - "auto: conexão instável" que aplica presets conservadores.

### 5.4 Widget de banda em tempo real
- **Origem:** `BandwidthWidget.tsx` + `bandwidth.rs`.
- **Adaptar:** medir bytes/s de download no servidor de streaming e exibir um pequeno indicador (↓ X MB/s) na barra de status ou no overlay de debug.

---

## PRIORIDADE 6 — Compartilhamento e conveniências

### 6.1 Copiar link nativo do Telegram da aula
- **Origem:** "copying native Telegram message links for files in public channels".
- **Adaptar:** se o canal do curso for público, gerar `https://t.me/{username}/{message_id}` e copiar (botão "Copiar link do Telegram"). O TgPlayer já tem `copy_stream_url()` — adicionar este modo.

### 6.2 (Opcional) Links locais protegidos
- **Origem:** `sharing.rs` (token + bcrypt + expiração + revogação, rota `/d/{token}`).
- **Adaptar:** menos relevante para o caso de cursos (uso pessoal). Implementar só se quiser compartilhar uma aula localmente. Se fizer: tabela `shared_links`, rota `_handle_share` no aiohttp, senha com `bcrypt`/`hashlib`, expiração, painel "Meus links" para revogar.

### 6.3 Estados vazios e arrastar-para-organizar
- **Origem:** `EmptyState.tsx`, drag&drop de organização.
- **Adaptar:** telas de "nenhum curso ainda / adicione um canal", e reordenar matérias/aulas por arrastar (o `db.py` já tem `reorder_*`).

---

## PRIORIDADE 7 — Toques de produto (criatividade — caso de uso "cursos")

Estas NÃO existem na referência, mas combinam com o espírito dela aplicado a videoaulas:

1. **Continuar assistindo / Retomar curso:** seção na home com as últimas aulas em andamento (usa `position_ms`/`progress` que já existem).
2. **Auto-play da próxima aula** (já citado em 3.3) com toggle nas configurações.
3. **Marcadores/anotações com timestamp:** clicar em "anotar" salva nota vinculada ao `position_ms` atual (estender `set_video_note`); lista de marcadores clicáveis que dão *seek*.
4. **Velocidade de reprodução persistente por curso** (0.75×–2×), comum em plataformas de curso.
5. **Progresso do curso em %:** barra "8/24 aulas concluídas (33%)" por matéria/curso (usa `mark_watched`).
6. **Modo "estudo focado":** integrar com o `study_tab.py`/pomodoro existente — iniciar um pomodoro ao abrir uma aula, registrar tempo via `log_study_time`.
7. **Fila de download offline:** baixar aulas selecionadas para assistir sem internet (reaproveita `DownloadQueue.tsx` como inspiração + servidor de stream apontando para arquivo local).

---

## Requisitos de engenharia (obrigatórios)

- **Stack-fit:** reimplementar em Python/PySide6/Pyrogram/aiohttp. **Não** introduzir Rust/React/Node. Onde a referência depende de MSE/mp4box no browser (ex.: `fmp4_remux.rs`), **prefira o backend nativo (libVLC/QMediaPlayer)** e descarte o remux — só mantenha a *ideia* de "deixar o MP4 pronto para tocar rápido".
- **Arquitetura:** manter o servidor aiohttp local + `stream_cache.py` como fonte de bytes. Novos handlers (`_handle_hls`, `_handle_share`, etc.) entram no `telegram_service.py`.
- **DB:** novas tabelas (`moov_cache`, opcional `shared_links`) com migração idempotente em `db.py` (`_ensure_column`/`CREATE TABLE IF NOT EXISTS`) e retry/backoff.
- **Configurações:** tudo configurável persiste via `get_setting/set_setting`. Adicionar uma aba/seção "Streaming & Rede" nas configurações.
- **Compatibilidade:** preservar o nome de sessão atual (`tgclassplayer`) e a migração de dados legada já existente.
- **Qualidade:** `python -m py_compile`, `pyflakes` sem warnings, e smoke tests (como os de `tests/`) para: alinhamento CDN, descoberta de moov em 3 passos, cache de moov (hit/miss), seleção adaptativa de qualidade, retomada/auto-play.
- **Docs e versão:** atualizar `CHANGELOG.md` (sugestão: **v6.3.0**), `README.md`, `COMO_USAR.txt`. Reempacotar o `.zip` (`TgPlayer_v6.3.0.zip`).
- **Git/PR:** commits atômicos na branch `genspark_ai_developer`; sincronizar com `main`; abrir/atualizar o PR e **enviar o link do PR + link do .zip** ao final.

## Ordem de implementação sugerida (fases)

1. **Fase A (núcleo):** 1.1 alinhamento CDN, 1.2 descoberta moov em 3 passos, 1.3 cache moov em SQLite, 1.4 cancelamento. → maior ganho de velocidade percebida.
2. **Fase B (qualidade):** 2.1 presets + throttle + adaptativo, 2.3 seletor na UI. (2.2 HLS só se FFmpeg trivial.)
3. **Fase C (player):** 3.1 badges, 3.2 debug overlay, 3.3 navegação/auto-play, 3.4 atalhos, 3.5 fullscreen.
4. **Fase D (biblioteca):** 4.1 metadados, 4.2 thumbnails, 4.3 badges na lista.
5. **Fase E (robustez):** 5.1 DB retry, 5.2 proxy, 5.3 tuning rede, 5.4 widget banda.
6. **Fase F (produto):** 6.1 link Telegram, P7 (continuar assistindo, anotações com timestamp, progresso do curso, velocidade por curso, estudo focado, offline).

Entregue cada fase com commit próprio e descrição clara. Ao concluir, escreva no `CHANGELOG` o mapeamento "ideia da referência → feature do TgPlayer".

# FIM DO PROMPT
