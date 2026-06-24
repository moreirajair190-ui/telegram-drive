# TgPlayer v6.5.1

TgPlayer é um organizador de videoaulas do Telegram. A partir da v6.5 o app conta com um **player embutido rápido** (QtWebEngine + streaming faststart) que reproduz a aula dentro do próprio TgPlayer em poucos segundos — sem depender do VLC. Abrir no Telegram Desktop/64Gram/Nekogram e abrir no VLC continuam como alternativas. Mantém progresso/manual de estudo, favoritos, checklist e Pomodoro.

A v6.5.1 traz uma **revisão geral do layout do app Windows**: estados vazios amigáveis nas listas e na aba Arquivos, painel de detalhes reorganizado com o botão "Assistir aqui" em destaque e ações agrupadas, colunas da árvore de aulas que não cortam mais o "Status", além de limpeza de código (remoção de módulos/arquivos mortos e imports não usados).

## Como rodar em desenvolvimento

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python TgPlayer.py
```

## Como gerar o executável correto

Use o script:

```bat
build_exe.bat --clean
```

Ao terminar, o app correto estará em:

```text
dist\TgPlayer\TgPlayer.exe
```

O script também tenta criar automaticamente:

```text
TgPlayer_PORTABLE_PARA_ENVIAR.zip
```

Esse ZIP é o pacote correto para enviar ao usuário final.

## Erro comum: python311.dll

Se aparecer:

```text
Failed to load Python DLL ... _internal\python311.dll
```

quase sempre é porque você executou ou enviou a pasta errada.

Não execute nem envie:

```text
build_intermediario_NAO_EXECUTAR\TgPlayer\TgPlayer.exe
build\TgPlayer\TgPlayer.exe
```

Essas pastas são temporárias do PyInstaller e não contêm tudo que o app precisa.

Execute/envie somente:

```text
dist\TgPlayer\TgPlayer.exe
```

ou o arquivo:

```text
TgPlayer_PORTABLE_PARA_ENVIAR.zip
```

## Build alternativo em arquivo único

Existe também:

```bat
build_exe_unico.bat --clean
```

Ele gera:

```text
dist_onefile\TgPlayer.exe
```

Mas, para apps com PySide6, a versão em pasta costuma ser mais confiável.

## Segurança das credenciais

O projeto final não deve conter:

```text
data
logs
*.session
*.sqlite3
.venv
.venv-build
dist
build
build_intermediario_NAO_EXECUTAR
```

As credenciais e sessão ficam no computador do usuário, em `%LOCALAPPDATA%\TgPlayer`, não dentro do código-fonte.

Para limpar localmente no app:

```text
Conta → Limpar credenciais locais
```


## Novidades v6.4.15

- Aba **Acompanhamento** redesenhada com base no projeto YasMedStudies.
- Dashboard com métricas de hoje, semana, sequência, aulas concluídas, Pomodoros e curso atual.
- Meta semanal editável, gráficos dos últimos 7 dias, progresso por curso e por matéria.
- Pomodoro e tarefas reorganizados em cards sem sobreposição.
- Atividade recente com aulas concluídas.
