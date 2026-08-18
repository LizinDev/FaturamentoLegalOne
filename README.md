# Cadastro de tarefas em lote — Legal One

Cadastra tarefas em lote nos processos listados numa planilha de cobranças,
direto no Legal One (`hasson.novajus.com.br`), via Selenium.

Para cada linha da planilha o programa busca o processo pelo número CNJ, abre o
formulário de nova tarefa e preenche os valores do perfil escolhido.

> Para operar no dia a dia — todas as flags, códigos de saída e o que fazer
> quando algo dá errado —, veja o **[Manual de operação](MANUAL.md)**. Este
> README explica as decisões de projeto por trás do comportamento.

São **duas tarefas**, e três formas de dizer qual cadastrar:

| Perfil (`--tarefa`) | Descrição gravada | Exige no nome da planilha |
| --- | --- | --- |
| `faturamento-final` | `FATURAMENTO FINAL` | `Faturamento` |
| `defesa-faturada` | `DEFESA FATURADA` | `Defesa` |
| `auto` | a que a coluna `TIPO DE COBRANÇA` disser, linha a linha | — |

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

### `--tarefa auto`

Quando as duas tarefas convivem na mesma aba, separá-las em dois arquivos a cada
atualização da planilha é trabalho manual que erra. Com `--tarefa auto` a tarefa
de cada processo sai da própria coluna `TIPO DE COBRANÇA`: `FATURAMENTO FINAL`
vira a tarefa de faturamento, `DEFESA FATURADA` vira a de defesa, e **qualquer
outro texto é pulado** com aviso — a coluna é livre e as abas mais novas trazem
dezenas de variantes (`CONTESTAÇÃO`, `ÊXITO`, `Acordo`) que não são tarefa
nenhuma. O casamento ignora caixa e espaços sobrando, mas é pelo texto inteiro:
nada de sinônimo ou pedaço de palavra.

Aqui não há trava por nome de arquivo, e de propósito — a garantia vem da célula
de cada linha, que é mais forte do que o nome do arquivo.

Neste modo a deduplicação passa a ser por **(processo, tarefa)**, e não só pelo
processo: na aba `2019-2020-2021` há 38 números que aparecem como defesa *e*
como faturamento final, etapas diferentes do mesmo caso. Deduplicar só pelo
número perderia uma das duas tarefas.

## Requisitos

- Python 3.10+ (`pip install -r requirements.txt`)
- Google Chrome, aberto em modo debug e logado no Legal One

O programa **nunca abre uma instância nova** de Chrome: conecta na que já está
aberta e trabalha numa aba própria, sem mexer nas outras abas. É a sessão dessa
janela que ele usa — daí a exigência de estar logado antes.

O passo a passo (abrir o Chrome, pré-voo, simulação, rodada real, cota do dia) e
a referência completa das flags estão no
**[Manual de operação](MANUAL.md)**. Para mexer no código, veja
[Desenvolvimento](#desenvolvimento).

## Quando a tarefa já existe

Parte dos processos **já tem a tarefa cadastrada antes de o programa rodar**,
feita à mão. Confirmado em produção: o processo `0088829-65.2025.8.05.0001` já
tinha uma `DEFESA FATURADA` marcada como Cumprido, e numa conferência da aba
`2019-2020-2021` isso valeu para 5 de 10 processos sorteados.

A orientação de operação para esse caso é **"pode agendar novamente, vamos pecar
pelo excesso"**. Então o programa cadastra de novo, e a checagem de duplicata
deixou de decidir *se* cadastra: ela decide *o que registrar*. O processo entra
no ledger como `recadastrada` e sai marcado na planilha do dia, na coluna
`JÁ TINHA A TAREFA`. Sem isso o supervisor receberia o excesso sem saber que é
excesso.

`--pular-existentes` restaura o comportamento antigo (registra `ja_existia` e não
cadastra), para o dia em que a orientação mudar.

Duas consequências disso:

- **Recadastro consome cota.** Ele cria uma tarefa no Legal One como qualquer
  outro, então entra na conta de `--max-cadastros`. 500 por dia passa a incluir
  as repetidas.
- **`--retentar` devolve os `ja_existia` antigos para a fila.** São exatamente os
  casos que a orientação atual manda cadastrar. Registros `ok` e `recadastrada` —
  o que este programa cadastrou — continuam fora.

### Aviso sobre `--rapido`

A flag `--rapido` pula a checagem, economizando uma página por processo. Ela já
não muda o que é cadastrado (com ou sem ela a tarefa é criada); o que se perde é
a **informação**: sem a checagem, todo cadastro sai como novo e o relatório deixa
de distinguir o que era repetido. Use quando a velocidade importar mais do que
essa distinção. Combinar `--rapido` com `--pular-existentes` é recusado com
código 2 — não dá para pular o que não foi checado.

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
| `recadastrada` | O processo já tinha a tarefa e ela foi cadastrada de novo |
| `ja_existia` | O processo já tinha a tarefa e foi pulado (`--pular-existentes`) |
| `nao_encontrado` | Nenhuma pasta com esse número exato no Legal One |
| `ambiguo` | Mais de uma pasta do tipo Processo com o mesmo número |
| `erro` | Falha no meio do caminho (o motivo fica na coluna `DETALHE`) |

Numa retomada normal tudo que já está no ledger é pulado. Com `--retentar`, só
`ok` e `recadastrada` são pulados — o que este programa cadastrou. O resto,
inclusive `ja_existia`, é tentado outra vez.

Uma **simulação nunca escreve no ledger**, para não marcar como feito algo que
não foi cadastrado.

Um cadastro nosso (`ok` ou `recadastrada`) nunca é rebaixado para `ja_existia`.
Reprocessar um processo já cadastrado responde "já tinha a tarefa", e isso é
verdade daquela passada — mas
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
sucesso. O descarte só alcança registros pendentes: `ok`, `recadastrada` e
`ja_existia` são trabalho confirmado no Legal One e nunca são apagados.

**`Ctrl+C`** — encerra limpo, exporta os relatórios e mantém o progresso.

Em qualquer saída — inclusive erro fatal ou `Ctrl+C` — os relatórios são
exportados antes de terminar. Cada arquivo é gravado por conta própria: se o
`relatorio.csv` estiver aberto no Excel (o Windows recusa a escrita), o erro
aparece no log e os outros arquivos saem do mesmo jeito.

A rodada distingue essas saídas no **código de saída**, para quem for agendá-la
num script: `1` quer dizer que a fila não terminou, e não que os cadastros
feitos até ali se perderam. A tabela dos códigos e o que vigiar no log durante a
rodada estão no [Manual de operação](MANUAL.md#códigos-de-saída).

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
verdade. É assim (com `--so-buscar`) que se levanta o que não existe no Legal
One sem cadastrar nada.

**`data/cadastrados_AAAA-MM-DD.xlsx`** — a planilha Excel do dia, gerada
automaticamente ao fim de toda rodada real que tenha cadastrado alguma coisa.
Traz só os processos que *esta instalação cadastrou naquele dia*, com cabeçalho
formatado, painel congelado e autofiltro. Colunas: processo, id no Legal One, os
quatro valores da tarefa (descrição, status, tipo, responsável), tipo de
cobrança, status na planilha, origem, o horário do cadastro e
`JÁ TINHA A TAREFA` (`Sim` nos recadastros, vazio nos demais).

Uma rodada que atravessa a meia-noite gera **as duas** planilhas, cada uma só com
o que foi cadastrado naquele dia. Se as duas tarefas rodarem no mesmo dia, as
duas aparecem no mesmo arquivo, separadas pela coluna `TAREFA`.

Os valores fixos da tarefa são repetidos em toda linha de propósito: assim a
planilha se explica sozinha para quem recebe e não acompanhou a execução.

Qualquer um desses arquivos pode ser refeito depois com `--relatorio`
([manual](MANUAL.md#relatórios)).

## Planilha esperada

O formato aceito está no [manual](MANUAL.md#a-planilha-de-entrada). Duas
decisões por trás dele:

Números repetidos entre abas viram **um processo só** — a tarefa é cadastrada
uma vez, e a origem agregada aparece no relatório. Com `--tarefa auto` isso vale
por tarefa: o mesmo número com as duas cobranças recebe as duas.

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

**1.5.0** — tarefa que já existe passa a ser cadastrada de novo, seguindo a
orientação de operação; a situação `recadastrada` e a coluna `JÁ TINHA A TAREFA`
marcam o excesso, e `--pular-existentes` guarda o comportamento antigo.

1.4.0 — `--tarefa auto`: a tarefa de cada processo sai da coluna `TIPO DE
COBRANÇA`, para a planilha que mistura as duas na mesma aba. Com ele, a
deduplicação e o controle de "já feito" passam a ser por (processo, tarefa).

1.3.0 — suíte de testes e CI (ver [Desenvolvimento](#desenvolvimento)); o
laço virou a classe `Rodada`, testável sem navegador; a rodada devolve código de
saída; `--limite`/`--max-cadastros`/`--dia` são validados na linha de comando;
uma exportação que falha não derruba mais as outras; migração do ledger numa
transação só.

1.2.0 — coluna vazia não sobrescreve mais coluna preenchida no ledger; lista de
conferência acumulada (com a de simulação em arquivo próprio); planilha do dia
para cada dia que a rodada tocar; disjuntor não apaga trabalho confirmado.

1.1.0 — dois perfis de tarefa e ledger indexado por (processo, tarefa).
