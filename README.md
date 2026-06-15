# TGClassPlayer v5 — Player Premium de Videoaulas do Telegram

Organize e assista às **videoaulas dos seus cursos no Telegram** dentro de uma
interface premium, com **streaming sob demanda**: a aula carrega na hora,
**sem baixar o vídeo inteiro**, **sem armazenar** o arquivo no seu PC e
**sem travar** ao avançar/retroceder.

> Proposta central: você conecta o app à sua própria API do Telegram e ele
> transforma seus **cursos (supergrupos)** em uma videoteca organizada por
> **tópicos → sumários → aulas**, com tudo editável.

---

## ✨ Principais recursos

- **Streaming sob demanda (sem download completo):** baixa só os pedaços do
  vídeo que você está assistindo (cache em blocos), com leitura antecipada
  (*read-ahead*) e *seek* instantâneo. O arquivo temporário é apagado ao fechar
  o player — **nada do vídeo fica salvo de forma permanente**.
- **Player premium em HTML5** (QtWebEngine): barra de progresso com prévia de
  buffer, ±10s, velocidade, volume, Picture-in-Picture, tela cheia e atalhos de
  teclado. Fallback automático para o player nativo (QtMultimedia) se necessário.
- **Cursos = supergrupos do Telegram**, separados em **tópicos**, com **sumários**
  e um **guia** explicando como as aulas estão organizadas.
- **Tudo editável:** renomear/excluir cursos, criar/renomear/reordenar/excluir
  tópicos, editar o texto do sumário, e editar cada aula (título, tópico,
  hashtags, anotações, favorito, marcar como assistida/não assistida).
- **Retomar de onde parou:** o progresso de cada aula é salvo automaticamente.
- **Organização por hashtags:** o sumário usa o formato
  `= Módulo / == Aula / === Tipo / #TAG01 #TAG02` para montar o menu e ligar as
  hashtags às aulas.
- **Interface premium dark**, busca, filtros (favoritos / pendentes) e menus de
  contexto (botão direito) em cursos e aulas.
- **Também abre no VLC** (opcional), caso você prefira.

---

## 🚀 Gerar o executável (.exe) no Windows — passo a passo

1. Instale o **Python 3.10, 3.11 ou 3.12 (64 bits)** em
   <https://www.python.org/downloads/> — durante a instalação **marque
   "Add Python to PATH"**.
2. Baixe/extraia esta pasta do projeto.
3. Dê **duplo clique** em **`build_exe.bat`** (ou rode no Prompt de Comando).
   O script cria um ambiente virtual, instala as dependências e gera o `.exe`.
4. Ao terminar, o aplicativo estará em:

   ```
   dist\TGClassPlayer\TGClassPlayer.exe
   ```

5. Para distribuir/levar para outro PC, copie a **pasta inteira**
   `dist\TGClassPlayer` (não apenas o `.exe`).

> 💡 Usamos o modo *onedir* (pasta única) porque é o mais confiável para apps
> com player HTML5 (QtWebEngine).

### Rodar a partir do código-fonte (sem gerar o .exe)

- **Windows:** duplo clique em `run_dev.bat`, ou:
  ```bat
  python -m venv .venv
  .venv\Scripts\activate
  pip install -r requirements.txt
  python TGClassPlayer.py
  ```
- **Linux/macOS (modo dev):**
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
  python TGClassPlayer.py
  ```

---

## 🔑 Como conseguir API ID e API HASH (Telegram)

1. Acesse <https://my.telegram.org> e faça login com seu número.
2. Entre em **API development tools**.
3. Crie um app (qualquer nome) e copie o **App api_id** e o **App api_hash**.
4. No TGClassPlayer, clique em **Conectar**, informe **API ID**, **API HASH** e
   seu **telefone**, depois o **código** que o Telegram enviar (e a **senha 2FA**,
   se você tiver).

> ⚠️ **Segurança:** nunca compartilhe seu **API HASH**, código de login ou senha
> 2FA. Esses dados ficam apenas no seu computador.

---

## 🧠 Como organizar um curso (sumário)

No sumário de um tópico, use este formato para gerar o menu e ligar as aulas:

```
= Módulo 1 — Introdução
== Aula 1 — Boas-vindas
=== Vídeo
#PY01

== Aula 2 — Configurando o ambiente
=== Vídeo
#PY02
```

- `=` é módulo, `==` é aula, `===` é o tipo de conteúdo.
- As **hashtags** (`#PY01`, `#PY02`, ...) ligam cada item do menu ao vídeo
  correspondente que tenha a mesma hashtag.

---

## 📁 Onde ficam os dados

- **No .exe:** em `%LOCALAPPDATA%\TGClassPlayer` (banco SQLite, sessão e logs).
- **No código-fonte:** na pasta `data/` do projeto.
- O **cache de vídeo é temporário** e é apagado ao fechar o player.

---

## 🛠️ Estrutura do projeto

```
TGClassPlayer.py          # ponto de entrada
TGClassPlayer.spec        # configuração do PyInstaller (gera o .exe)
build_exe.bat             # gera o executável (Windows)
run_dev.bat              # roda o código-fonte (Windows)
requirements.txt          # dependências
src/tgclassplayer/
  app.py                  # janela principal (UI premium, edição de tudo)
  telegram_service.py     # Pyrogram + servidor HTTP local (streaming)
  stream_cache.py         # cache em blocos sob demanda (sem download total)
  player.py / player_html.py  # player premium (HTML5) + fallback
  db.py                   # banco SQLite com edição completa
  dialogs.py              # login, seleção de cursos, editor de sumário/aula
  style.py                # tema premium dark
  summary_parser.py       # parser do menu/sumário (hashtags)
  utils.py / paths.py / vlc_locator.py / logging_setup.py
```

---

## ❓ Problemas comuns

- **"Python não encontrado":** reinstale o Python marcando *Add Python to PATH*.
- **O player não abre o vídeo:** garanta que o `TgCrypto` foi instalado (acelera
  o Telegram) — ele já está no `requirements.txt`.
- **Build falhou no QtWebEngine:** rode novamente o `build_exe.bat`; o `.spec` já
  coleta automaticamente os recursos do WebEngine.

---

Feito com PySide6 + Pyrogram. **Use apenas com conteúdo ao qual você tem acesso
legítimo.**
