# TGClassPlayer v6 — Player Premium de Videoaulas do Telegram

Organize e assista às **videoaulas dos seus cursos no Telegram** dentro de uma
interface premium, com **streaming sob demanda**: a aula carrega na hora,
**sem baixar o vídeo inteiro**, **sem armazenar** o arquivo no seu PC e
**sem travar** ao avançar/retroceder. Agora com **temas claro/escuro**,
navegação por **matérias** e uma aba de **Acompanhamento de estudos** (Pomodoro,
tarefas e gráficos).

> Proposta central: você conecta o app à sua própria conta do Telegram e ele
> transforma seus **cursos (grupos, canais e fóruns)** em uma videoteca
> organizada por **matérias → módulos → aulas**, com tudo editável.

---

## ✨ Principais recursos

- **Detecção automática do tipo do chat** (sem você configurar nada):
  - **Supergrupo com fórum (tópicos):** cada **tópico vira uma matéria**, com o
    seu próprio sumário (mensagem fixada do tópico) e a sua própria lista de aulas.
  - **Grupo/supergrupo normal:** uma única matéria; sumário = mensagem fixada.
  - **Canal (broadcast):** lista linear de aulas em ordem cronológica.
- **Streaming sob demanda (sem download completo):** baixa só os pedaços do
  vídeo que você está assistindo (cache em blocos), com leitura antecipada
  (*read-ahead*) e *seek* instantâneo. O arquivo temporário é apagado ao fechar
  o player — **nada do vídeo fica salvo de forma permanente**.
- **Player nativo (QtMultimedia)** como reprodutor **principal**: usa os
  **codecs do próprio Windows** (Media Foundation), então reproduz H.264/AAC sem
  o erro `DEMUXER_ERROR_NO_SUPPORTED_STREAMS` que ocorria no player HTML5.
  Tem ±10s, velocidade, volume, tela cheia e botão **"Abrir no VLC"**, além de um
  aviso amigável com **"Tentar de novo"** quando algum formato não é suportado.
  O player HTML5 (QtWebEngine), servido pelo mesmo servidor local, fica como
  *fallback* automático.
- **Sumário por matéria** no formato hierárquico
  `= Módulo / == Aula / === Tipo / #TAG01 #TAG02`. Cada hashtag liga o item do
  menu à aula com a **mesma hashtag**. Cada matéria tem o **seu próprio** sumário
  (sem misturar matérias).
- **Tudo editável:** renomear/cor/reordenar/excluir **cursos**;
  criar/renomear/reordenar/excluir **matérias** e editar o texto do sumário;
  editar cada **aula** (título, matéria, módulo, tipo, hashtags, anotações,
  favorito, marcar assistida/pendente). Menus de contexto (botão direito) em
  cursos, matérias e aulas.
- **Barra de progresso geral** no topo (aulas assistidas/total e horas).
- **Filtros**: Todas / Assistidas / Pendentes / ★ Favoritas + busca por título,
  hashtag ou módulo.
- **Retomar de onde parou:** o progresso de cada aula é salvo automaticamente.
- **Temas Claro 🌞 e Escuro 🌙** bem calibrados, com alternância no topo e
  **persistência** (o app lembra a sua escolha).
- **Aba "Acompanhamento" 📊:**
  - ⏱️ **Pomodoro** configurável (foco / pausa curta / pausa longa), com
    iniciar/pausar/zerar, ciclos e registro de sessões.
  - ✅ **Tarefas/checklist** (A fazer / Feito) com prioridade e prazo opcional.
  - 📈 **Gráficos** (tempo de estudo por dia, aulas concluídas por curso e anel
    de progresso) desenhados com QPainter — **sem dependências frágeis**.
  - Cartões de resumo: sequência de dias (*streak*), horas totais e % do curso.

---

## 🚀 Gerar o executável (.exe) no Windows — passo a passo

1. Instale o **Python 3.11 ou 3.12 (64 bits)** em
   <https://www.python.org/downloads/> e **marque "Add Python to PATH"**.
   > Evite o **Python 3.13** por enquanto: o `TgCrypto` ainda pode não ter
   > *wheel* pronto nele (o build continua mesmo assim, mas sem essa otimização).
2. Baixe/extraia esta pasta do projeto.
3. Dê **duplo clique** em **`build_exe.bat`**. Ele cria um ambiente virtual,
   instala as dependências e gera o executável.
   > Se aparecer um **aviso** dizendo que o TgCrypto não foi instalado, **tudo
   > bem** — é opcional e o aplicativo funciona normalmente.
4. Ao final, o app estará em **`dist\TGClassPlayer\TGClassPlayer.exe`**.
   Para distribuir, copie a **pasta inteira** `dist\TGClassPlayer`.

> O build usa o modo **onedir** (pasta única) porque é o mais confiável para
> apps com **QtWebEngine** (o player HTML5).

### Rodar a partir do código-fonte (sem gerar .exe)

Dê **duplo clique** em **`run_dev.bat`** (cria o `.venv` na primeira vez e
executa `python TGClassPlayer.py`).

---

## 🔑 Como obter o API ID e o API HASH (Telegram)

1. Acesse <https://my.telegram.org> e entre com o seu número.
2. Vá em **API development tools**.
3. Crie um app (qualquer nome). Você verá o **api_id** (número) e o
   **api_hash** (texto).
4. No TGClassPlayer, clique em **Conectar**, informe **API ID**, **API HASH** e
   seu **telefone com DDI** (ex.: `+5547999999999`). O **código de login** chega
   no próprio Telegram. Se você usa verificação em duas etapas, informe a senha.

> ⚠️ **Nunca compartilhe** seu código de login, senha 2FA ou API HASH com
> ninguém. O app guarda a sessão localmente, no seu computador.

---

## 🧭 Como o app detecta fórum × grupo × canal

Ao **Sincronizar**, o app identifica o tipo do chat automaticamente:

- **Fórum** (supergrupo com tópicos): usa a API bruta
  `channels.GetForumTopics` para listar os tópicos. **Cada tópico = uma matéria.**
  As aulas são filtradas pelo tópico (thread) e o sumário é a mensagem **fixada**
  daquele tópico (ou a melhor "candidata a menu" encontrada nele).
- **Grupo/supergrupo normal:** matéria única; sumário = fixado do grupo.
- **Canal:** lista linear (cronológica); sumário = fixado, se houver.

Você não precisa configurar nada — mas pode **editar** matérias, sumários e
aulas depois, manualmente.

---

## 🗂️ Como organizar o sumário (menu) de cada matéria

O sumário liga as **hashtags** das aulas a um menu organizado. Formato:

```
= Módulo 1 - Introdução
== Aula 1 - Boas-vindas
=== Videoaula
#AULA01
=== Resumo
#RESUMO01

== Aula 2 - Conceitos
=== Videoaula
#AULA02
```

- `=` Módulo, `==` Aula, `===` Tipo (Videoaula/Resumo/Bônus...).
- As linhas com `#TAG` ligam o item do menu à aula que tem a **mesma hashtag**
  (na legenda, no nome do arquivo ou no texto).
- Aulas sem correspondência aparecem em **"Sem módulo"**.
- Texto decorativo (ex.: "Clique aqui", "⚠️ Atenção ⚠️") é ignorado.

Edite o sumário em **Editar matérias/sumários** (na barra lateral) ou clicando
com o **botão direito** na matéria.

---

## 📁 Onde ficam os dados

- Rodando como **.exe**, os dados ficam em
  `%LOCALAPPDATA%\TGClassPlayer` (banco SQLite, sessão e logs).
- O **cache de vídeo** é temporário e **apagado** ao fechar o player.

---

## 🔒 Restrições e privacidade

- O app **não armazena** os vídeos de forma permanente (streaming sob demanda;
  apenas cache temporário).
- Use somente com **conteúdo a que você tem acesso legítimo**.
- A sua sessão e suas credenciais ficam **somente no seu computador**.

---

## 🛠️ Estrutura do projeto

```
TGClassPlayer.py            # ponto de entrada
src/tgclassplayer/
  app.py                    # janela principal (UI nova, abas, tema, progresso)
  db.py                     # banco SQLite (cursos, matérias, aulas, estudo)
  telegram_service.py       # Telegram (detecção fórum/grupo/canal + streaming)
  summary_parser.py         # parser do sumário por matéria
  player.py                 # player QtMultimedia (principal) + fallback HTML5
  player_html.py            # página HTML do player
  study_tab.py              # aba Acompanhamento (Pomodoro, tarefas, gráficos)
  charts.py                 # gráficos em QPainter (sem QtCharts)
  dialogs.py                # login, seleção de cursos, editores
  style.py                  # temas claro/escuro (QSS + paletas)
  stream_cache.py, paths.py, utils.py, vlc_locator.py, logging_setup.py
requirements.txt, TGClassPlayer.spec, build_exe.bat, run_dev.bat
```

---

Feito para ser **simples para quem não é técnico** e **poderoso para estudar**.
Bons estudos! 📚
