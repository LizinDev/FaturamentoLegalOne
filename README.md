# Cadastro de tarefas em lote — Legal One

Cadastra tarefas em lote nos processos listados numa planilha de cobranças,
direto no Legal One (`hasson.novajus.com.br`), via Selenium.

Para cada linha da planilha o programa busca o processo pelo número CNJ, abre o
formulário de nova tarefa e preenche os valores do perfil escolhido.

São **duas planilhas e duas tarefas**, uma planilha para cada perfil:

| Perfil (`--tarefa`) | Descrição gravada | Exige no nome da planilha |
| --- | --- | --- |
| `faturamento-final` | `FATURAMENTO FINAL` | `Faturamento` |
| `defesa-faturada` | `DEFESA FATURADA` | `Defesa` |

O resto é igual nos dois:

| Campo | Valor |
| --- | --- |
| Tipo | `Diversos` |
| Status | `Cumprido` |
| Responsável | `Heloiza Helena de Araujo` |
| Data (início e fim) | hoje (ou `--data`) |

Os perfis ficam em `PERFIS`, em `src/config.py`. São nomeados em vez de texto
livre na linha de comando porque parear a planilha de uma tarefa com a descrição
da outra criaria centenas de tarefas indevidas.

Pela mesma razão cada perfil exige um trecho no nome do arquivo (`dica_arquivo`):
se você mandar a planilha de defesas com `--tarefa faturamento-final`, a rodada
é abortada antes de começar. Quando o par estiver certo mas o arquivo não seguir
a convenção de nome, `--forcar-planilha` passa por cima.

## Requisitos

- Python 3.10+
- Google Chrome, aberto em modo debug e logado no Legal One

```bash
pip install -r requirements.txt
```

Para mexer no código, veja [Desenvolvimento](#desenvolvimento).

## Como usar

### 1. Abrir o Chrome em modo debug

```powershell
# Windows
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\ChromeDebug"
```

```bash
# Linux
google-chrome --remote-debugging-port=9222 --user-data-dir=~/ChromeDebug
```

Faça login no Legal One nesse Chrome antes de rodar. O programa **nunca abre uma
instância nova** — ele se conecta à que já está aberta e trabalha numa aba
própria, sem mexer nas suas outras abas.

### 2. Pré-voo: quais processos existem no Legal One?

Antes de cadastrar qualquer coisa, vale saber quais números da planilha o Legal
One não encontra. `--so-buscar` só pesquisa, sem abrir formulário:

```bash
cd src
python main.py --planilha "C:/Users/Kamila/Downloads/Faturamento.xlsx" --so-buscar
```

### 3. Simulação

Sem `--executar` o programa preenche o formulário inteiro e **não salva**. É o
padrão — dá para conferir tudo antes de gravar:

```bash
python main.py --planilha "C:/.../Faturamento.xlsx" --abas 2026 --limite 20
```

### 4. Rodada real

```bash
python main.py --planilha "C:/.../Faturamento.xlsx" --executar
```

### 5. Cota diária, alternando as duas planilhas

Para ~500 cadastros por dia, é o mesmo comando com `--max-cadastros`, mudando a
planilha e o perfil a cada dia:

```bash
# um dia
python main.py --planilha "C:/.../Faturamento.xlsx" --tarefa faturamento-final --max-cadastros 500 --executar

# no outro
python main.py --planilha "C:/.../Defesa.xlsx" --tarefa defesa-faturada --max-cadastros 500 --executar
```

Não é preciso controlar por onde parou, nem qual foi a última: o ledger guarda o
progresso **por tarefa**, então cada rodada pega os próximos 500 ainda não
cadastrados daquele perfil. Ao terminar, a planilha do dia é gerada sozinha.

Duas flags parecidas, com contas diferentes:

| Flag | Conta o quê |
| --- | --- |
| `--max-cadastros 500` | Para em **500 tarefas cadastradas**. Processos não encontrados e os que já tinham a tarefa não consomem cota. |
| `--limite 500` | Para depois de **500 processos examinados**, cadastrando quantos der (~375, já que cerca de 1 em 4 não existe no Legal One). |

Para a meta do supervisor, `--max-cadastros` é a que corresponde ao número
combinado.

## Opções

| Opção | O que faz |
| --- | --- |
| `--planilha CAMINHO` | Caminho do `.xlsx` de cobranças |
| `--tarefa PERFIL` | `faturamento-final` (padrão) ou `defesa-faturada` |
| `--forcar-planilha` | Ignora a trava que confere planilha × perfil |
| `--abas NOME [NOME...]` | Só estas abas (padrão: todas) |
| `--tipo-contem TEXTO` | Filtra `TIPO DE COBRANÇA` por trecho do texto |
| `--status-planilha TEXTO` | Filtra `STATUS LEGAL ONE` exato (ex.: `Ativo`) |
| `--limite N` | Examina no máximo N processos (conta os não encontrados) |
| `--max-cadastros N` | Para depois de N tarefas efetivamente cadastradas |
| `--data DD/MM/AAAA` | Data da tarefa (padrão: hoje) |
| `--executar` | Grava de verdade — sem isso, apenas simula |
| `--retentar` | Tenta de novo os que deram erro / não foram encontrados |
| `--rapido` | Pula a checagem de tarefa duplicada — **leia o aviso abaixo** |
| `--so-buscar` | Pré-voo: só procura os processos, não abre formulário |
| `--processo CNJ [CNJ...]` | Roda só estes números (testar ou refazer um caso) |
| `--relatorio` | Só refaz os relatórios do que já rodou e sai |
| `--dia AAAA-MM-DD` | Com `--relatorio`, refaz a planilha de um dia específico |

## Aviso sobre `--rapido`

Parte dos processos **já tem a tarefa cadastrada antes de o programa rodar**,
feita à mão. Confirmado em produção: o processo `0088829-65.2025.8.05.0001` já
tinha uma `DEFESA FATURADA` marcada como Cumprido.

Ou seja, a checagem de duplicata não é só uma rede de segurança contra perda do
ledger — ela é a única coisa que impede o programa de duplicar tarefa que já
existe. `--rapido` desliga exatamente isso. Use só se tiver certeza de que
nenhum processo da planilha já foi tratado.

Quando a tarefa já existe, o processo é contado como `ja_existia` e **não
consome cota** de `--max-cadastros` — 500 continuam sendo 500 cadastros novos.

## Retomada

A planilha tem milhares de processos e a rodada leva horas, então ela vai ser
interrompida em algum momento. Cada processo é gravado em `data/ledger.sqlite3`
assim que termina, e commitado na hora.

Para continuar de onde parou, basta rodar o mesmo comando de novo — o que já
está no ledger é pulado. `Ctrl+C` é seguro: o progresso até ali está salvo.

O ledger é indexado por **(processo, tarefa)**, e não só pelo processo. Um mesmo
número pode estar nas duas planilhas e precisar das duas tarefas; com a chave só
no processo, cadastrar `FATURAMENTO FINAL` faria a rodada de `DEFESA FATURADA`
pular aquele processo em silêncio. Ledgers de versões anteriores são migrados
automaticamente, com os registros existentes atribuídos a `FATURAMENTO FINAL`.

Situações registradas no ledger:

| Situação | Significado |
| --- | --- |
| `ok` | Tarefa cadastrada |
| `ja_existia` | O processo já tinha a tarefa daquele perfil |
| `nao_encontrado` | Nenhuma pasta com esse número exato no Legal One |
| `ambiguo` | Mais de uma pasta do tipo Processo com o mesmo número |
| `erro` | Falha no meio do caminho (o motivo fica na coluna `DETALHE`) |

Numa retomada normal tudo que já está no ledger é pulado. Com `--retentar`,
só `ok` e `ja_existia` são pulados — o resto é tentado outra vez.

Uma **simulação nunca escreve no ledger**, para não marcar como feito algo que
não foi cadastrado.

Um `ok` nunca é rebaixado para `ja_existia`. Reprocessar um processo já
cadastrado responde "já tinha a tarefa", e isso é verdade daquela passada — mas
sobrescrever apagaria o registro de que fomos nós que cadastramos, e em que dia.
Como a planilha diária se apoia nisso, a situação, a data e o detalhe originais
são preservados.

Pela mesma razão, **valor vazio nunca sobrescreve valor preenchido**. Nem todo
caminho tem todos os dados em mãos — um erro no meio do cadastro não sabe o tipo
de cobrança, por exemplo —, e sem essa regra a segunda passada esvaziaria as
colunas que a primeira tinha preenchido.

## Como os erros são tratados

A regra geral: **um processo com problema nunca derruba a fila.** A falha é
registrada com o motivo e o programa segue para o próximo. Só duas situações
param tudo, e as duas de propósito.

**Falha isolada** (timeout, elemento que não apareceu, tipo padrão diferente do
esperado) — vira `erro` no ledger com a exceção e a mensagem em `DETALHE`, e a
rodada continua. Numa retomada normal esses são pulados; com `--retentar` são
tentados de novo.

**Processo não encontrado** — vira `nao_encontrado` e vai para
`nao_encontrados.csv` com o motivo. Não é erro: parte da planilha simplesmente
não existe no Legal One.

**Aba fechada** — se a aba de trabalho sumir, o programa detecta antes de cada
processo e recria, sem perder a rodada.

**Sessão expirada** — se o Legal One redirecionar para a tela de login, a rodada
**para na hora**. Sem isso, todo o resto da fila viraria erro em silêncio.

**Muitos "não encontrado" seguidos** — o disjuntor. Uma sessão pode morrer sem
redirecionar para o login, e aí a busca passa a não achar nada. Depois de 25
seguidos o programa para, descarta esses registros suspeitos do ledger e avisa.
É o caso mais perigoso de uma rodada sem supervisão, porque falha parecendo
sucesso. O descarte só alcança registros pendentes: `ok` e `ja_existia` são
trabalho confirmado no Legal One e nunca são apagados.

**`Ctrl+C`** — encerra limpo, exporta os relatórios e mantém o progresso.

Em qualquer saída — inclusive erro fatal ou `Ctrl+C` — os relatórios são
exportados antes de terminar. Cada arquivo é gravado por conta própria: se o
`relatorio.csv` estiver aberto no Excel (o Windows recusa a escrita), o erro
aparece no log e os outros arquivos saem do mesmo jeito.

### Código de saída

Para quem for agendar a rodada num script:

| Código | Significado |
| --- | --- |
| `0` | Rodada completa, ou nada a fazer |
| `1` | Rodada abortada: sessão expirada, disjuntor ou Chrome fora do ar |
| `2` | Erro de uso: argumento ou planilha inválida |
| `130` | Interrompida com `Ctrl+C` |

Atenção: `1` significa que **a fila não terminou**, e não que os cadastros
feitos até ali se perderam — esses estão no ledger. É só rodar de novo.

### O que vigiar durante a rodada

O log sai no console e em `logs/faturamento.log`. A cada 25 processos aparece
uma linha de progresso com ritmo, tempo decorrido, ETA e o placar:

```
... 250/11954 | 9.6s/processo | decorrido 0h40m | falta ~31h12m | {'ok': 187, 'erro': 2, 'nao_encontrado': 61, 'ja_existia': 0}
```

`erro` subindo rápido é sinal de que algo mudou no Legal One — vale parar e
olhar. `nao_encontrado` alto é esperado (boa parte da planilha é de processo
antigo que não está no Legal One).

## Relatórios

Toda rodada exporta dois CSVs (separador `;`, UTF-8 com BOM, abrem direto no
Excel):

**`data/relatorio.csv`** — uma linha por processo já processado: número,
situação, id no Legal One, detalhe, tipo de cobrança, status na planilha,
origem e quando rodou.

**`data/nao_encontrados.csv`** — só o que precisa de conferência manual
(`nao_encontrado` e `ambiguo`), ordenado pela posição na planilha. Sai do ledger,
então é **acumulado**: traz o que todas as rodadas já levantaram, e não só a
última. Colunas:

| Coluna | Conteúdo |
| --- | --- |
| `PROCESSO` | O número como está na planilha |
| `PESQUISADO_COMO` | Só preenchido quando o número foi corrigido antes de buscar |
| `TAREFA` | Qual das duas tarefas essa rodada tentava cadastrar |
| `SITUACAO` | `nao_encontrado` ou `ambiguo` |
| `MOTIVO` | Por que falhou (sem resultado, só recurso, número fora do padrão…) |
| `TIPO_COBRANCA` | Ajuda a priorizar o que conferir |
| `STATUS_PLANILHA` | O que a planilha dizia do Legal One |
| `ORIGEM` | Aba e linha exatas (ex.: `2026!L14; 2025!L802`) |

**`data/nao_encontrados_simulacao.csv`** — a mesma lista, quando a rodada é
simulação. Mesmas colunas, arquivo separado: uma simulação só enxerga a própria
fila, e escrever no arquivo de cima apagaria a lista acumulada das rodadas de
verdade. É assim que se levanta o que não existe no Legal One sem cadastrar nada:

```bash
python main.py --planilha "C:/.../Faturamento.xlsx" --so-buscar
```

**`data/cadastrados_AAAA-MM-DD.xlsx`** — a planilha Excel do dia, gerada
automaticamente ao fim de toda rodada real que tenha cadastrado alguma coisa.
Traz só os processos que *esta instalação cadastrou naquele dia*, com cabeçalho
formatado, painel congelado e autofiltro. Colunas: processo, id no Legal One, os
quatro valores da tarefa (descrição, status, tipo, responsável), tipo de
cobrança, status na planilha, origem e o horário do cadastro.

Uma rodada que atravessa a meia-noite gera **as duas** planilhas, cada uma só com
o que foi cadastrado naquele dia. Se as duas tarefas rodarem no mesmo dia, as
duas aparecem no mesmo arquivo, separadas pela coluna `TAREFA`.

Os valores fixos da tarefa são repetidos em toda linha de propósito: assim a
planilha se explica sozinha para quem recebe e não acompanhou a execução.

Para refazer a de um dia anterior:

```bash
python main.py --relatorio --dia 2026-07-30
```

Sem `--dia`, `--relatorio` refaz a planilha de todos os dias que têm cadastro.

## Planilha esperada

Qualquer aba que tenha uma coluna `PROCESSO` no cabeçalho da primeira linha.
Também são lidas, quando existem, `TIPO DE COBRANÇA` e `STATUS LEGAL ONE` — só
para os filtros e para o relatório.

Números repetidos entre abas viram **um processo só** (a tarefa é cadastrada uma
única vez), e a origem agregada aparece no relatório.

Número no formato `0064904.50.2019.8.05.0001` (ponto no lugar do primeiro hífen)
é corrigido automaticamente. Números realmente quebrados são tentados como
estão, falham na busca e saem no relatório marcados como fora do padrão CNJ —
em vez de sumirem em silêncio.

## Estrutura

```
src/
├── main.py       # CLI, laço principal (classe Rodada), progresso e ETA
├── config.py     # URLs, seletores, valores da tarefa e timeouts
├── planilha.py   # leitura do .xlsx -> lista de processos únicos
├── legalone.py   # Selenium: busca o processo e cadastra a tarefa
├── ledger.py     # checkpoint em SQLite + exportação dos CSVs
└── relatorio.py  # planilha Excel do dia (a que vai para o supervisor)

tests/            # pytest — nenhum teste abre o Chrome
.github/          # CI, análise de dependências e Dependabot
```

## Desenvolvimento

```bash
pip install -r requirements-dev.txt
pytest          # a suíte inteira roda em segundos, sem navegador
ruff check .    # lint
```

Nenhum teste toca no Legal One, no Chrome ou no ledger de produção: o Selenium
é substituído por um automador falso e todo arquivo vai para um diretório
temporário. O que está coberto é justamente o que dói quando quebra — a
interpretação da grade de resultados (em qual pasta a tarefa vai), o ledger
(inclusive as migrações de esquema antigo) e as regras que param ou não param a
fila: disjuntor, cota do dia, sessão expirada, `Ctrl+C` e erro isolado.

`ruff format` **não** é usado: o código é alinhado à mão e reformatar tudo
esconderia o histórico atrás de uma mudança de estilo.

### O que roda no GitHub Actions

| Workflow | Quando | O que faz |
| --- | --- | --- |
| `ci.yml` | push e pull request | lint com ruff; `pytest` em Python 3.10/3.12/3.14 no Linux e 3.14 no Windows; fumaça da CLI (`--help`, `--relatorio`, código de saída) |
| `seguranca.yml` | semanal e quando mexe nos requirements | `pip-audit` nas dependências |
| `codeql.yml` | semanal | análise estática de segurança |
| `dependabot.yml` | semanal/mensal | PR quando sai versão nova de dependência ou de action |

O Windows está na matriz de propósito: é onde o programa roda de verdade, e
caminho, encoding de console e arquivo travado pelo Excel se comportam
diferente lá.

A rodada completa não tem como ser testada na CI — ela depende de um Chrome
logado no Legal One. Por isso a checagem de verdade continua sendo o
`--so-buscar` e a simulação antes de uma rodada real.

Como o repositório é **privado**, o `codeql.yml` exige GitHub Advanced
Security e pode falhar no upload dos alertas; nesse caso é só apagar o arquivo.
Ele já vem sem gatilho de push justamente para não atrapalhar o dia a dia.

## Notas técnicas

- **O filtro de status da busca precisa ser limpo na mão.** O Legal One salva os
  critérios de filtro entre sessões e o padrão da conta é `Ativo`, então uma
  busca normal não acha processo arquivado. Todas as URLs de busca mandam
  `StatusSimples[0]` vazio (`FILTRO_SEM_STATUS` em `config.py`). Sem isso, 14.801
  das 17.749 linhas da planilha ficariam invisíveis.
- **Status só é vinculado com clique.** Digitar `Cumprido` e dar ENTER preenche
  `StatusText` mas deixa `StatusId` **vazio**; é o clique na linha do lookup que
  grava `StatusId=1`. E o casamento precisa ser exato, porque `Não cumprido`
  contém `Cumprido` como substring.
- **Tipo e datas já vêm certos do formulário** (`Diversos` / hoje). O código
  confere o tipo em vez de reescrever, para não desfazer o vínculo de `TipoId`.
  Se o padrão do Legal One mudar, o programa falha alto em vez de cadastrar
  errado.
- **Só cadastra em pasta do tipo `Processo`.** Recurso e incidente repetem o
  número CNJ do processo principal; cadastrar neles duplicaria a tarefa.
  Verificado em produção: o CNJ `0003850-54.2026.8.16.0188` tem 3 pastas
  (1 Processo + 2 Recursos) e a tarefa vai só para a principal. Quando o número
  existe **apenas** como recurso ou incidente, o programa não adivinha — marca
  como `nao_encontrado` com o motivo `nenhuma pasta do tipo Processo (achei:
  ...)` e joga na lista de conferência. Para mudar esse critério, veja
  `TIPO_ACEITO` em `config.py`.
- **Um disjuntor para a rodada se a sessão cair.** Numa rodada saudável os
  "não encontrado" aparecem espalhados; muitos seguidos significa que a sessão
  do Legal One caiu, não que os processos sumiram. Depois de
  `MAX_NAO_ENCONTRADOS_SEGUIDOS` (25) seguidos o programa para, **apaga do
  ledger esses registros suspeitos** e avisa — assim eles voltam para a fila na
  próxima rodada em vez de ficarem marcados como resolvidos.
- **Chromedriver do PATH é ignorado.** Um chromedriver antigo instalado via
  chocolatey ganha do Selenium Manager e quebra com Chrome novo, então esses
  diretórios são removidos do `PATH` no processo (`legalone.py`). Num Linux com
  chromedriver em `/usr/bin`, tire na mão — ali o `PATH` não dá para mexer.
- **A aba de trabalho é lembrada pelo handle.** O Chrome limpa `window.name` em
  navegação entre sites, então procurar só pela marca faria o programa abrir uma
  aba nova a cada checagem e encher o navegador de abas.
- **Sessão expirada aborta a rodada inteira.** Sem isso, um logout no meio do
  lote transformaria todos os processos restantes em `erro` e queimaria a fila
  em silêncio.
- Campos de data são preenchidos por JavaScript — o datepicker ignora os eventos
  normais do Selenium.

## Desempenho medido

- Busca isolada (`--so-buscar`): ~2,3 s por processo
- Fluxo completo (busca + checagem de duplicata + formulário): ~9,6 s por processo

Para os ~12 mil processos únicos da planilha, o fluxo completo fica na casa das
30 horas. Dá para rodar em pedaços — a retomada é automática.

## Versão

**1.3.0** — suíte de testes e CI (ver [Desenvolvimento](#desenvolvimento)); o
laço virou a classe `Rodada`, testável sem navegador; a rodada devolve código de
saída; `--limite`/`--max-cadastros`/`--dia` são validados na linha de comando;
uma exportação que falha não derruba mais as outras; migração do ledger numa
transação só.

1.2.0 — coluna vazia não sobrescreve mais coluna preenchida no ledger; lista de
conferência acumulada (com a de simulação em arquivo próprio); planilha do dia
para cada dia que a rodada tocar; disjuntor não apaga trabalho confirmado.

1.1.0 — dois perfis de tarefa e ledger indexado por (processo, tarefa).
