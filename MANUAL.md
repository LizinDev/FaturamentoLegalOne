# Manual de operação

Guia prático do cadastro em lote de tarefas no Legal One. O
[README](README.md) explica *por que* cada decisão foi tomada; aqui é *como
operar*.

- [Antes de começar](#antes-de-começar)
- [A receita de um dia](#a-receita-de-um-dia)
- [Referência das flags](#referência-das-flags)
- [Como as flags se combinam](#como-as-flags-se-combinam)
- [Códigos de saída](#códigos-de-saída)
- [Variáveis de ambiente](#variáveis-de-ambiente)
- [Pastas e arquivos](#pastas-e-arquivos)
- [A planilha de entrada](#a-planilha-de-entrada)
- [Perfis de tarefa](#perfis-de-tarefa)
- [Scripts de operação](#scripts-de-operação)
- [Quando algo dá errado](#quando-algo-dá-errado)
- [Ajustes finos](#ajustes-finos)

---

## Antes de começar

### Instalação

```powershell
cd C:\Users\Kamila\projetos\FaturamentoLegalOne
pip install -r requirements.txt
```

Precisa de Python 3.10 ou mais novo (o código usa `str | Path` e `set[str]`).
No 3.10 o `requirements.txt` instala também o `tomli`, que lê o `tarefas.toml`;
da 3.11 em diante isso já vem no Python.

### Abrir o Chrome em modo debug

O programa **nunca abre uma instância nova** de Chrome. Ele se conecta a uma que
já está aberta na porta de debug e trabalha numa aba própria, sem mexer nas
outras abas.

```powershell
# Windows
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\ChromeDebug"
```

```bash
# Linux
google-chrome --remote-debugging-port=9222 --user-data-dir=~/ChromeDebug
```

**Faça login no Legal One nessa janela antes de rodar.** É a sessão dela que o
programa usa. Se essa janela fechar no meio da rodada, a rodada morre junto.

### Confirmar que está tudo de pé

```powershell
cd src
python main.py --planilha "..\Planilhas\Faturamento 2024.xlsx" --limite 3
```

Sem `--executar` isso é simulação: preenche o formulário inteiro e não salva
nada. Se as três primeiras linhas passarem, a conexão, o login e a planilha
estão bons.

---

## A receita de um dia

O normal é rodar a planilha inteira e deixar ir até o fim. `--max-cadastros`
existe para quando você quiser parar num número — não há limite diário a
respeitar.

As planilhas recebidas ficam em `Planilhas\`, na raiz do projeto (ver
[Pastas e arquivos](#pastas-e-arquivos)); os exemplos rodam de dentro de `src\`.

**Faturamento:**

```powershell
cd C:\Users\Kamila\projetos\FaturamentoLegalOne\src
python main.py --planilha "..\Planilhas\Faturamento 2024.xlsx" --executar
```

**Defesa:**

```powershell
python main.py --planilha "..\Planilhas\Defesas.xlsx" --tarefa defesa-faturada --executar
```

O nome do arquivo não é livre: cada perfil exige um trecho no caminho da
planilha (`Faturamento` e `Defesa`) e recusa o par errado com código 2 — ver
[`--forcar-planilha`](#valores-da-tarefa).

**Planilha única, com as duas tarefas misturadas na mesma aba:**

```powershell
python main.py --planilha "..\Planilha de Faturamento.xlsx" --abas "2019-2020-2021" --tarefa auto --executar
```

Aqui cada linha recebe a tarefa que a coluna da cobrança indica. Ver
[`--tarefa auto`](#valores-da-tarefa).

**Uma tarefa que não é de faturamento nem de defesa:**

```powershell
# avulsa, só nesta rodada
python main.py --planilha "..\Planilhas\Custas.xlsx" --descricao "CONFERIR CUSTAS" --status Pendente --responsavel "Nathalia Maria Gatto Pinto" --data 01/10/2026
# ou a planilha diz a tarefa de cada linha
python main.py --planilha "..\Planilhas\Tarefas.xlsx" --tarefa planilha
```

Sem `--executar`, as duas são simulação. **Leia o cabeçalho antes de repetir com
`--executar`**: ele mostra cada combinação de tarefa e quantos processos a pedem,
e as linhas `Conferido:` mostram o tipo e o responsável como o Legal One os
achou. Se a tarefa for recorrente, vale virar um perfil no `tarefas.toml` (ver
[Perfis de tarefa](#perfis-de-tarefa)).

Não é preciso anotar por onde parou nem qual planilha foi a última. O ledger
guarda o progresso **por tarefa**, então cada rodada pega o que ainda não foi
cadastrado daquele perfil. Ao terminar, a planilha do dia é gerada sozinha
em `data/`.

Uma rodada de milhares de processos leva horas e vai ser interrompida —
sessão que expira, máquina que reinicia. Isso é esperado e não custa nada:
repetir o mesmo comando retoma de onde parou. Ao fim, uma passada de
`--retentar` recolhe o que falhou pelo caminho. Para uma rodada de horas sem
vigiar, use o `scripts\rodada_ate_a_meta.ps1`, que solta o Python destacado e o
religa depois de um disjuntor (ver [Scripts de operação](#scripts-de-operação)).

Ao fim de cada rodada, o resumo sai no console:

```
Resumo desta rodada: {'ok': 350, 'recadastrada': 150, 'erro': 3, 'nao_encontrado': 166, 'ja_existia': 0}
Planilha do dia:      ...\data\cadastrados_2026-07-30.xlsx
```

`ok` e `recadastrada` somados são o que entrou no Legal One naquela rodada — é o
que a planilha do dia mostra. `recadastrada` é o
processo que já tinha a tarefa e recebeu outra, seguindo a orientação de
"pode agendar novamente, vamos pecar pelo excesso"; na planilha do dia essas
linhas vêm marcadas na coluna `JÁ TINHA A TAREFA`.

---

## Referência das flags

Nenhuma flag é obrigatória por si só, mas **ou `--planilha` ou `--relatorio`
precisa estar presente**.

| Flag | Argumento | Padrão |
| --- | --- | --- |
| `--planilha` | caminho do `.xlsx` | — (obrigatória, salvo com `--relatorio`) |
| `--tarefa` | um perfil do `tarefas.toml` \| `auto` \| `planilha` | `faturamento-final` |
| `--descricao` | texto | — (tarefa avulsa; exige `--status` e `--responsavel`) |
| `--tipo` | caminho na árvore de tipos | o do perfil (avulsa: `Diversos`) |
| `--status` | um dos seis status do Legal One | o do perfil |
| `--responsavel` | usuário ativo do Legal One | o do perfil |
| `--forcar-planilha` | — | desligada |
| `--abas` | um ou mais nomes de aba | todas as abas |
| `--tipo-contem` | trecho de texto | sem filtro |
| `--status-planilha` | texto exato | sem filtro |
| `--limite` | inteiro ≥ 1 | sem limite |
| `--max-cadastros` | inteiro ≥ 1 | sem limite |
| `--processo` | um ou mais números CNJ | toda a planilha |
| `--data` ou `--inicio` | `DD/MM/AAAA`, `DD/MM/AAAA HH:MM` ou `HH:MM` | hoje, hora do formulário |
| `--fim` | idem | o dia do início (com hora de início, 30 min depois) |
| `--publicacao` | `DD/MM/AAAA` | a do formulário |
| `--disponibilizacao` | `DD/MM/AAAA` | a do formulário |
| `--executar` | — | desligada (simula) |
| `--retentar` | — | desligada |
| `--rapido` | — | desligada |
| `--pular-existentes` | — | desligada (por padrão recadastra) |
| `--so-buscar` | — | desligada |
| `--relatorio` | — | desligada |
| `--dia` | `AAAA-MM-DD` | todos os dias com cadastro |

### Seleção do que rodar

**`--planilha CAMINHO`**

Caminho do `.xlsx` de cobranças. Use aspas se tiver espaço no caminho. Barra
normal (`/`) funciona no Windows também. Arquivo inexistente encerra com código
2 antes de abrir o Chrome.

**`--abas NOME [NOME ...]`**

Restringe a leitura a estas abas. O nome tem que bater exatamente, inclusive
maiúsculas. Aba que não existe encerra com código 2 e lista as disponíveis:

```powershell
python main.py --planilha "..." --abas 2026 2025
```

**`--tipo-contem TEXTO`**

Mantém só as linhas cuja coluna `TIPO DE COBRANÇA` contenha esse trecho.
Comparação **por substring e ignorando maiúsculas** — `encerramento` casa com
`ENCERRAMENTO FINAL`.

**`--status-planilha TEXTO`**

Mantém só as linhas cuja coluna `STATUS LEGAL ONE` seja **exatamente** esse
texto. Diferente da anterior: aqui é igualdade, e maiúsculas contam. `Ativo`
casa com `Ativo`, não com `ativo` nem com `Ativo - em recurso`.

**`--processo CNJ [CNJ ...]`**

Roda só estes números e **ignora o ledger de propósito** — é a forma de refazer
um caso ou testar um processo específico mesmo que ele já esteja marcado como
cadastrado. Os números são normalizados igual à planilha, então tanto faz
escrever com ponto ou com hífen no lugar certo.

O número precisa **estar na planilha**. Se não estiver, sai um aviso
(`Nao esta(o) na planilha: ...`) e ele não é processado.

```powershell
python main.py --planilha "..." --processo 0088829-65.2025.8.05.0001 --executar
```

**`--limite N`**

Para depois de **examinar** N processos, contando os que não foram encontrados e
os que já tinham a tarefa.

Atenção ao ponto que costuma confundir: o corte é aplicado **depois** de tirar da
fila o que já está no ledger. Numa retomada, `--limite 20` são os próximos 20
ainda não vistos, e não os 20 primeiros da planilha.

**`--max-cadastros N`**

Para depois de **cadastrar** N tarefas. Processo não encontrado e processo que
deu erro **não são contados**. Recadastro é: ele cria tarefa no Legal One
como qualquer outro (com `--pular-existentes`, o processo é pulado e não conta).

Repare na diferença para `--limite`: `--max-cadastros 300` são 300 tarefas
criadas no Legal One, enquanto `--limite 300` seriam 300 processos olhados,
resultando em menos cadastros quando parte da planilha não existe lá.

As duas exigem inteiro **maior que zero**. `--max-cadastros 0` é recusado com
código 2 de propósito: zero é falso em Python e passaria como "sem limite",
rodando a planilha inteira em vez de parar na hora.

### Valores da tarefa

**`--tarefa PERFIL`**

Escolhe qual tarefa cadastrar. Sem `--tarefa` (e sem `--descricao`), vale
`faturamento-final`.

| `--tarefa` | A tarefa |
| --- | --- |
| `faturamento-final` | `FATURAMENTO FINAL`, do `tarefas.toml` |
| `defesa-faturada` | `DEFESA FATURADA`, do `tarefas.toml` |
| outro perfil do `tarefas.toml` | a daquela seção (ver [Perfis de tarefa](#perfis-de-tarefa)) |
| `auto` | a que a coluna `TIPO DE COBRANÇA` disser, linha a linha |
| `planilha` | a das colunas `... DA TAREFA` de cada linha (ver abaixo) |

**`--descricao TEXTO`**, **`--tipo`**, **`--status`**, **`--responsavel`**

`--descricao` cadastra uma tarefa **avulsa**, que não está no `tarefas.toml`, e
exige `--status` e `--responsavel` (sem eles, código 2). `--tipo` é opcional e
vale `Diversos`. Não combina com `--tarefa`.

```powershell
python main.py --planilha "..." --descricao "CONFERIR CUSTAS" --tipo "Diversos" --status Pendente --responsavel "Nathalia Maria Gatto Pinto"
```

Com um perfil, `--tipo`, `--status` e `--responsavel` sobrepõem o perfil **só
nesta rodada** — a descrição e a trava de nome de arquivo continuam as do
perfil. Com `--tarefa auto`, valem para todos os perfis da planilha.

```powershell
python main.py --planilha "..\Defesa.xlsx" --tarefa defesa-faturada --responsavel "Nathalia Maria Gatto Pinto"
```

`--status` aceita sem acento e em qualquer caixa (`nao cumprido`). O tipo e o
responsável são escritos como em [Perfis de tarefa](#perfis-de-tarefa), e
conferidos no Legal One antes do primeiro cadastro.

**`--tarefa planilha`**

A tarefa inteira sai de cada linha, pelas colunas:

| Coluna | Na linha vazia, vale |
| --- | --- |
| `DESCRIÇÃO DA TAREFA` | obrigatória — linha sem ela é pulada, com aviso |
| `TIPO DA TAREFA` | `--tipo`; senão o perfil de mesma descrição; senão `Diversos` |
| `STATUS DA TAREFA` | `--status`; senão o perfil de mesma descrição |
| `RESPONSÁVEL DA TAREFA` | `--responsavel`; senão o perfil de mesma descrição |
| `INÍCIO DA TAREFA` | `--data`; senão hoje |
| `CONCLUSÃO DA TAREFA` | `--fim`; senão o dia do início (com hora de início, 30 min depois) |
| `PUBLICAÇÃO DA TAREFA` | `--publicacao`; senão a do formulário |
| `DISPONIBILIZAÇÃO DA TAREFA` | `--disponibilizacao`; senão a do formulário |

As colunas de início e conclusão aceitam hora na mesma célula
(`01/10/2026 09:00`) e célula de data/hora do Excel. Uma célula de data do Excel
às 00:00 é lida como "sem hora" — é assim que o Excel guarda uma data pura.

Se a descrição de uma linha for a de um perfil do `tarefas.toml` (sem ligar
para caixa e espaços), o perfil completa o que a linha e as flags não disserem,
e a descrição passa a ser escrita como no perfil. Linha que termina sem status
ou sem responsável — nem na coluna, nem na flag, nem no perfil — encerra com
código 2 antes de abrir o Chrome, listando aba e linha.

O cabeçalho da rodada mostra cada combinação distinta e quantos processos a
pedem. **Confira antes de `--executar`**: é ali que aparece uma coluna de
responsável trocada ou um status errado na planilha inteira.

```
Tarefa:      'CONFERIR CUSTAS' / tipo 'Diversos' / status 'Pendente' / responsavel 'nathalia': 212 processo(s)
Tarefa:      'FATURAMENTO FINAL' / tipo 'Diversos' / status 'Cumprido' / responsavel 'Heloiza Helena de Araujo': 40 processo(s)
```

O mesmo processo pode pedir tarefas diferentes em linhas diferentes. Mas a
mesma tarefa (mesma descrição) no mesmo processo, com tipo, status ou
responsável diferentes entre as linhas, encerra com código 2 — o ledger guarda
um cadastro por par (processo, descrição).

**`--tarefa auto`** é para a planilha que mistura faturamento e defesa na mesma
aba. O ledger é indexado por **(processo, tarefa)**, de modo que o mesmo processo
pode receber as duas sem que uma rodada pule a outra.
Cada linha recebe a tarefa que a sua coluna `TIPO DE COBRANÇA` indica:

| Célula (ignorando caixa e espaços) | Tarefa cadastrada |
| --- | --- |
| `FATURAMENTO FINAL` | `FATURAMENTO FINAL` |
| `DEFESA FATURADA` | `DEFESA FATURADA` |
| qualquer outro texto | nenhuma — a linha é pulada |

As linhas puladas saem num aviso no log, com a lista dos valores e quantas
linhas cada um tinha:

```
1834 linha(s) puladas por TIPO DE COBRANCA sem tarefa correspondente:
'CONTESTAÇÃO' (612); 'ÊXITO' (410); 'Acordo' (23)
```

Neste modo não há trava por nome de arquivo (a garantia vem da célula de cada
linha), e o mesmo número que aparece com as duas cobranças recebe **as duas
tarefas** — são etapas diferentes do mesmo caso.

```powershell
python main.py --planilha "..\Planilha de Faturamento.xlsx" --abas "2019-2020-2021" --tarefa auto --executar
```

**`--data QUANDO`** (ou `--inicio`), **`--fim QUANDO`**, **`--publicacao`**, **`--disponibilizacao`**

As datas da tarefa. `QUANDO` é `DD/MM/AAAA`, `DD/MM/AAAA HH:MM` ou só `HH:MM`
(o dia fica sendo o do cadastro); `9h` e `9h30` também valem. Publicação e
disponibilização são só data, como no formulário.

| Pedido | Vai ao formulário |
| --- | --- |
| nada | início e conclusão hoje, na hora que o formulário sugere (a próxima hora cheia) — o de sempre |
| `--data 01/10/2026` | início e conclusão em 01/10, hora do formulário |
| `--data "01/10/2026 09:00"` | 09:00 às **09:30** — sem hora de fim, a tarefa dura 30 minutos, como no Legal One |
| `--data 09:00` | hoje, 09:00 às 09:30 |
| `--data 01/10/2026 --fim "01/10/2026 10:00"` | 09:30 às 10:00 — sem hora de início, 30 minutos antes |
| `--data 01/10/2026 --fim 03/10/2026` | de 01 a 03/10, hora do formulário |
| `--publicacao 25/09/2026` | escrita por cima do que o subtipo sugerir |

A hora que falta num dia só é completada pela outra, e não deixada para o
formulário: a sugestão dele vem da hora atual e pode cair antes do início
pedido, e o Legal One recusa início depois da conclusão.

Sem `--data`, "hoje" é recalculado a cada tarefa cadastrada, não fixado no
início da rodada. Numa rodada que atravessa a meia-noite, as tarefas cadastradas
antes da virada levam a data de ontem e as de depois levam a de hoje, na mesma
execução. Use `--data` quando precisar que a rodada inteira registre uma data
fixa.

Antes de abrir o Chrome, cada processo é conferido contra o que o Legal One
recusaria — data mal escrita (`31/02/2026`), **início depois da conclusão** e
**Pendente com conclusão no passado** — e qualquer um deles encerra com código 2,
listando as linhas. Data anterior a hoje com Cumprido é aceita: o Legal One pede
confirmação (*Deseja salvar mesmo assim?*) e a rodada confirma em cada cadastro,
avisando no início do log. A confirmação só é dada quando o **dia** foi pedido
(`--data`, `--fim` ou a coluna da planilha); sem dia pedido, o aviso só aparece
num processo pego pela meia-noite, que vira `erro` e é refeito pelo
`--retentar` com a data do dia.

No modo planilha, as colunas `INÍCIO DA TAREFA`, `CONCLUSÃO DA TAREFA`,
`PUBLICAÇÃO DA TAREFA` e `DISPONIBILIZAÇÃO DA TAREFA` valem por cima dessas
flags, campo a campo.

**`--forcar-planilha`**

Desliga a trava que confere se a planilha combina com o `--tarefa` escolhido.

A trava está **ligada** nos dois perfis de produção: o caminho da planilha precisa conter o
trecho declarado em `dica_arquivo` (`Faturamento` para `faturamento-final`,
`Defesa` para `defesa-faturada`), sem diferenciar maiúsculas. Rodar
`--tarefa defesa-faturada` apontando para `Faturamento.xlsx` encerra com código
2 em vez de criar centenas de tarefas erradas.

Use `--forcar-planilha` quando a intenção for mesmo essa — planilha com nome
fora do padrão, por exemplo. Para mudar o trecho exigido, veja
[Perfis de tarefa](#perfis-de-tarefa).

### Modo de execução

**`--executar`**

Grava de verdade. **Sem esta flag tudo é simulação**: o formulário é preenchido
inteiro, conferido, e não é salvo. A simulação também não escreve no ledger — do
contrário, marcaria como feito algo que nunca foi cadastrado.

**`--retentar`**

Numa retomada normal, tudo que já está no ledger é pulado. Com `--retentar`, só
`ok` e `recadastrada` são pulados — o que este programa cadastrou;
`nao_encontrado`, `ambiguo`, `erro` e `ja_existia` voltam para a fila. Os
`ja_existia` são de rodadas antigas, quando o programa pulava a tarefa que já
existia: são exatamente os casos que a orientação atual manda cadastrar.

Útil depois de corrigir a causa de uma leva de erros. Não faz diferença nenhuma
sem `--executar`, porque a simulação já não pula nada.

**`--so-buscar`**

Pré-voo: só procura os processos e relata quais não existem, sem abrir
formulário. Bem mais rápido (~2,3 s por processo, contra ~9,6 s do fluxo
completo). É como se levanta a lista de conferência sem tocar em nada:

```powershell
python main.py --planilha "..." --so-buscar
```

**Não pode ser combinada com `--executar`** (encerra com código 2). As duas
juntas marcariam no ledger como resolvido o que nunca foi cadastrado.

**`--pular-existentes`**

Não cadastra onde a tarefa já existe: o processo vira `ja_existia` e a fila
segue. É o comportamento antigo, hoje sob demanda.

Por padrão o programa **cadastra de novo**, seguindo a orientação de operação
("pode agendar novamente, vamos pecar pelo excesso"). Nesse caso a situação
gravada é `recadastrada` e a planilha do dia marca `Sim` na coluna
`JÁ TINHA A TAREFA` — o excesso vai visível para quem recebe.

Não pode ser combinada com `--rapido` (encerra com código 2): não há como pular
o que não foi checado.

**`--rapido`**

Pula a checagem de tarefa duplicada, economizando uma página por processo.

> **O que se perde.** Não é o cadastro — com ou sem a flag a tarefa é criada. É a
> informação: sem a checagem, todo cadastro é gravado como novo (`ok`), e o
> relatório deixa de distinguir o que já existia. Use quando a velocidade
> importar mais do que essa distinção.

### Relatórios

**`--relatorio`**

Reexporta os relatórios do que já rodou e sai. Não abre o Chrome, não lê
planilha, não cadastra nada — as outras flags são ignoradas, exceto `--dia`.

```powershell
python main.py --relatorio
```

Refaz `relatorio.csv`, `nao_encontrados.csv` e a planilha de **todos** os dias
que têm cadastro.

**`--dia AAAA-MM-DD`**

Junto com `--relatorio`, refaz a planilha de um dia só:

```powershell
python main.py --relatorio --dia 2026-07-30
```

---

## Como as flags se combinam

A fila é montada nesta ordem. Saber a ordem explica por que `--limite` às vezes
parece pegar processos "do meio" da planilha.

1. **Leitura da planilha**, já aplicando `--abas`, `--tipo-contem` e
   `--status-planilha`.
2. **Deduplicação por (processo, tarefa).** O mesmo processo repetido em várias
   linhas ou abas com a mesma tarefa vira uma entrada só, com as origens
   agregadas.
3. **A tarefa de cada processo**: o perfil (com as flags por cima), a coluna da
   cobrança (`auto`) ou as colunas da tarefa (`planilha`), e as datas — conferidas
   contra o que o Legal One recusaria.
4. **Remoção do que já está no ledger** — tudo, ou só `ok` e `recadastrada` se
   `--retentar`. Numa simulação, nada é removido.
5. **`--processo`**, se usada: mantém só os números pedidos e desfaz o passo 4.
6. **`--limite`**: corta a fila resultante.
7. **Conferência no Legal One** do tipo e do responsável de cada tarefa da fila
   (depois de conectar no Chrome; `--so-buscar` pula).
8. **`--max-cadastros`**: interrompe o laço durante a execução.

Combinações que o programa recusa:

| Combinação | O que acontece |
| --- | --- |
| `--so-buscar --executar` | Encerra com código 2 |
| `--pular-existentes --rapido` | Encerra com código 2 |
| nem `--planilha` nem `--relatorio` | Encerra com código 2 |
| planilha sem o trecho exigido pelo `--tarefa` | Encerra com código 2 (salvo `--forcar-planilha`) |
| `--descricao` com `--tarefa`, ou sem `--status`/`--responsavel` | Encerra com código 2 |
| `--tarefa planilha` sem a coluna `DESCRIÇÃO DA TAREFA`, ou com linha incompleta | Encerra com código 2, listando as linhas |
| início depois da conclusão; Pendente com conclusão no passado | Encerra com código 2, listando as linhas |
| `tarefas.toml` com problema | Encerra com código 2 (`--help` e `--relatorio` seguem funcionando) |
| tipo ou responsável que não casa com o Legal One | Encerra com código 2, antes do primeiro cadastro |

Combinações inofensivas, que só rendem um aviso e seguem:

| Combinação | O que acontece |
| --- | --- |
| `--so-buscar --max-cadastros` | Avisa que não tem efeito (nada é cadastrado); para encurtar o pré-voo, `--limite` |
| `--dia` sem `--relatorio` | Avisa e ignora |

---

## Códigos de saída

| Código | Significado |
| --- | --- |
| `0` | Terminou a fila (inclusive "nada a fazer" e `--max-cadastros` atingido) |
| `1` | Abortou: não conectou ao Chrome ou disjuntor |
| `2` | Erro de uso: flag faltando, combinação inválida, planilha × tarefa incompatível, data ou planilha inválida, aba inexistente, tipo ou responsável que não casa com o Legal One |
| `3` | Sessão expirada: alguém precisa fazer login antes de repetir |
| `130` | `Ctrl+C` |

O código 2 acontece **antes** de qualquer cadastro. O 1, o 3 e o 130 podem
acontecer no meio da rodada — em qualquer um deles o progresso está salvo no
ledger e repetir o mesmo comando retoma de onde parou. O 3 é separado do 1 para
um script que reinicia a rodada sozinho saber quando parar de tentar: repetir
sem login só gasta tentativas.

---

## Variáveis de ambiente

| Variável | Padrão | Para que serve |
| --- | --- | --- |
| `DEBUG_PORT` | `9222` | Porta de debug do Chrome |
| `LOG_LEVEL` | `INFO` | `DEBUG` para log detalhado. Valor inválido cai em `INFO` em vez de impedir a rodada |
| `FATURAMENTO_LOG_FILE` | `logs/faturamento.log` | Outro arquivo de log; vazio desliga o arquivo (os testes usam assim, para não sujar o log de produção) |

```powershell
$env:LOG_LEVEL = "DEBUG"
python main.py --planilha "..." --limite 5
```

---

## Pastas e arquivos

Versionados no Git: o código (`src/`), os testes, os scripts de operação
(`scripts/`) e os perfis (`tarefas.toml`). O resto fica **só nesta máquina** e
não vai para o Git — contém números de processo e nomes de clientes:

| Pasta | O que guarda |
| --- | --- |
| `data/` | O estado da automação, lido e escrito pelo programa (tabela abaixo). Criada sozinha |
| `data/backups/` | Cópias do ledger feitas antes de mexer nele à mão (auditoria, remoção de fantasmas) e os scripts avulsos das rodadas de 10/09 |
| `logs/` | `faturamento.log` e os logs de cada rodada longa. Criada sozinha |
| `Planilhas/` | As planilhas de cobrança recebidas — a entrada das rodadas |
| `Cadastros/` | O que foi entregue à supervisão: planilhas por leva, auditorias, relatórios |

`Planilhas/` e `Cadastros/` são convenção de organização: o programa não
depende delas, e `--planilha` aceita qualquer caminho.

| Arquivo | O que é |
| --- | --- |
| `data/ledger.sqlite3` | O progresso. É o que permite retomar sem cadastrar nada duas vezes |
| `data/relatorio.csv` | Uma linha por par (processo, tarefa) já processado, com situação, detalhe e os valores da tarefa enviada |
| `data/nao_encontrados.csv` | Só o que precisa de conferência manual. Sai do ledger, então é acumulado entre rodadas |
| `data/nao_encontrados_simulacao.csv` | O mesmo, quando a rodada é simulação. Arquivo separado para não apagar a lista acumulada |
| `data/cadastrados_AAAA-MM-DD.xlsx` | A planilha do dia, a que vai para o supervisor |
| `logs/faturamento.log` | O log completo, em UTF-8 |

Os CSVs usam `;` como separador e UTF-8 com BOM — abrem direto no Excel
brasileiro, sem assistente de importação.

O ledger é o arquivo que importa preservar. Perdê-lo não causa cadastro
duplicado (a checagem de duplicata continua protegendo), mas custa reprocessar
milhares de buscas. Ele fica só nesta máquina. Antes de mexer nele à mão, copie
para `data/backups/` com a data e o motivo no nome
(`ledger.sqlite3.bak-antes-remover-57`).

---

## A planilha de entrada

Qualquer aba que tenha uma coluna `PROCESSO` no **cabeçalho da primeira linha**.
Aba sem essa coluna é ignorada com um aviso, e não derruba a leitura.

| Coluna | Obrigatória | Uso |
| --- | --- | --- |
| `PROCESSO` | sim | O número CNJ a buscar |
| `TIPO DE COBRANÇA`, `TAREFA` **ou** `TAREFA PARA LANÇAR` | não | Filtro `--tipo-contem`, coluna dos relatórios e, com `--tarefa auto`, a tarefa daquela linha |
| `STATUS LEGAL ONE` | não | Filtro `--status-planilha` e coluna dos relatórios |
| `DESCRIÇÃO DA TAREFA`, `TIPO DA TAREFA`, `STATUS DA TAREFA`, `RESPONSÁVEL DA TAREFA` | só com `--tarefa planilha` (a descrição é obrigatória nesse modo) | A tarefa de cada linha. Fora desse modo são ignoradas |
| `INÍCIO DA TAREFA`, `CONCLUSÃO DA TAREFA`, `PUBLICAÇÃO DA TAREFA`, `DISPONIBILIZAÇÃO DA TAREFA` | não | As datas de cada linha no modo planilha, por cima de `--data` e das outras flags de data |

**A coluna da cobrança tem três nomes aceitos.** As planilhas antigas trazem
`TIPO DE COBRANÇA`; a de 2022 e a de 2025 trazem `TAREFA`; a de 2026 traz
`TAREFA PARA LANÇAR`. São tratadas como o mesmo campo, e
vale a primeira que tiver valor na linha — `TIPO DE COBRANÇA` primeiro. Se a
sua planilha usar um terceiro nome, acrescente-o a `COLUNAS_TIPO_COBRANCA`, em
`src/config.py`.

Isso importa mais do que parece: sem o nome reconhecido, a coluna passa a valer
como vazia e **`--tarefa auto` pula a aba inteira em silêncio**. Pior, se o nome
do arquivo contiver `Faturamento` ou `Defesa`, a trava de perfil não segura — o
programa cadastraria a tarefa do nome do arquivo em todas as linhas, inclusive
nas da outra tarefa. Confira no cabeçalho da rodada que a contagem por tarefa
bate com a planilha antes de deixar rodar.

Os nomes das colunas são reconhecidos sem ligar para maiúsculas nem espaços
sobrando, mas o acento conta: `TIPO DE COBRANCA` sem cedilha não é reconhecida e
a coluna passa a valer como vazia.

Número no formato `0064904.50.2019.8.05.0001` (ponto no lugar do primeiro hífen)
é corrigido automaticamente; o relatório mostra as duas versões nas colunas
`PROCESSO` e `PESQUISADO_COMO`. Números realmente quebrados são tentados como
estão, falham na busca e saem marcados como fora do padrão CNJ — em vez de
sumirem em silêncio.

---

## Perfis de tarefa

Ficam no **`tarefas.toml`**, na raiz do projeto — uma seção por perfil, e o
nome da seção é o que se escreve em `--tarefa`. Para criar uma tarefa
recorrente ou mudar responsável, status ou tipo de uma existente, é lá; não é
preciso mexer em Python.

```toml
[defesa-faturada]
descricao = "DEFESA FATURADA"
tipo = "Diversos"
status = "Cumprido"
responsavel = "Heloiza Helena de Araujo"
dica_arquivo = "Defesa"

[conferir-custas]
descricao = "CONFERIR CUSTAS"
tipo = "Diversos / Contato Telefônico"
status = "Pendente"
responsavel = "Nathalia Maria Gatto Pinto"
```

`descricao`, `tipo`, `status` e `responsavel` são obrigatórios; `dica_arquivo` é
opcional. Campo que falta, campo desconhecido (`responsável` com acento, por
exemplo), status que não existe, duas seções com a mesma descrição ou uma seção
chamada `auto`, `planilha` ou `avulsa` fazem **toda rodada** terminar com código
2, com a lista dos problemas — `--help` e `--relatorio` continuam funcionando.

Mudar um perfil só afeta os cadastros **daqui para a frente**. Desde a 1.8 o
ledger guarda o tipo, o status, o responsável e as datas de cada tarefa enviada,
e é de lá que saem as planilhas do dia — refazer com `--relatorio` a planilha de
um dia antigo continua mostrando o que foi cadastrado naquele dia.

Os campos de um perfil:

| Campo | Como escrever |
| --- | --- |
| `descricao` | O texto gravado no campo Descrição; identifica a tarefa na checagem de duplicata e no ledger — trocar a descrição de um perfil faz as rodadas seguintes a tratarem como outra tarefa |
| `tipo` | O caminho na árvore de tipos do Legal One: `"Diversos"` para um tipo, `"Diversos / Contato Telefônico"` para um subtipo. `>` também serve de separador. O nome sozinho (`"Contato Telefônico"`) vale enquanto for único na árvore |
| `status` | Um dos seis do Legal One: Pendente, Cumprido, Não cumprido, Cancelado, Iniciado, Recusado. Acento e maiúsculas não importam |
| `responsavel` | Um usuário **ativo** do Legal One. Acento e maiúsculas não importam, e basta parte do nome enquanto só um usuário casar |
| `dica_arquivo` | Opcional. Ver abaixo |

As mesmas regras de escrita valem para `--tipo`, `--status` e `--responsavel`
e para as colunas do modo planilha.

**O tipo e o responsável são conferidos no Legal One antes do primeiro
cadastro.** Logo depois de conectar no Chrome, a rodada busca a árvore de tipos
e os usuários e casa cada tipo e cada responsável distintos da fila — venham do
arquivo, das flags ou da planilha —, uma vez cada. O log mostra o resultado:

```
Conferido:   tipo 'Diversos > Contato Telefonico' -> 'Diversos / Contato Telefônico' (subtipo_9)
Conferido:   responsavel 'nathalia' -> 'Nathalia Maria Gatto Pinto'
```

Se um tipo não existe, se o nome do tipo aparece sob mais de um pai
("Audiência" existe em mais de dez lugares) ou se o responsável casa com mais
de um usuário, a rodada termina com **código 2** sem cadastrar nada, listando o
que existe:

```
Tarefa que nao da para cadastrar no Legal One:
  tipo 'Prazos / Apelacao' nao existe no Legal One. Parecidos: '[Cível] Prazos / Apelação'; ... [em 'TAREFA X']
  responsavel 'Ana' casa com mais de um usuario; escreva o nome completo: 'Ana Clara Stroparo'; ... [em 'TAREFA Y']
```

Nunca se escolhe um "mais parecido": errar o tipo ou a pessoa criaria centenas
de tarefas no lugar errado. `--so-buscar` não passa por essa conferência, porque
não abre formulário.

No formulário, o tipo é escolhido **antes** das datas. Subtipos com contagem de
prazo preenchem sozinhos Data de publicação e Prazo e podem recalcular início e
fim; por isso as datas pedidas são escritas depois, e conferidas de novo logo
antes do Salvar — se o Legal One tiver trocado alguma, o processo vira `erro`
em vez de gravar a tarefa com a data errada. "Diversos", que já é o padrão do
formulário, não é reescolhido.

`dica_arquivo` é o trecho que precisa aparecer no caminho da planilha — é a
trava contra parear a planilha de uma tarefa com o perfil da outra. Se os
arquivos mudarem de nome, é este campo que se ajusta; deixá-lo vazio desliga a
conferência daquele perfil.

`--tarefa auto` não é um perfil a mais no arquivo: ele escolhe, para cada
linha, um dos perfis do arquivo. O casamento é pela descrição — acrescentar um perfil
novo já o torna disponível no modo auto, com o texto da coluna
`TIPO DE COBRANÇA` tendo que ser igual à descrição.

---

## Scripts de operação

Em `scripts/`, versionados, rodados da raiz do projeto. Nenhum cadastra nada.

**`auditar.py`** — confere no Legal One se os cadastros que o ledger dá como
feitos existem de verdade, num recorte de horário, e grava um CSV com
`sim`/`NAO`/`ERRO_CONFERENCIA` por processo. Não muda o ledger. Serve para as
rodadas de antes da 1.8 (ver
[O ledger diz cadastrado mas a tarefa não existe](#o-ledger-diz-cadastrado-mas-a-tarefa-não-existe))
e para qualquer rodada que se queira conferir. Precisa do Chrome de debug livre.

```powershell
python scripts\auditar.py --desde 2026-09-10T14:00 [--ate 2026-09-11] [--saida "Cadastros\Auditoria.csv"]
```

**`planilha_da_leva.py`** — a planilha de cadastrados de um recorte de horário,
no mesmo formato da planilha do dia. É para as levas que não coincidem com um
dia (uma que atravessa a meia-noite, um dia com duas). Só lê o ledger.

```powershell
python scripts\planilha_da_leva.py --desde 2026-09-10T14:00 --ate 2026-09-10T18:20 --saida "Cadastros\Cadastrados Quarta Leva dia 10 de setembro.xlsx"
```

**`rodada_ate_a_meta.ps1`** — solta uma rodada longa e a religa até cadastrar N
tarefas, pedindo a cada vez só o que falta (`--max-cadastros`). Decide pelo
código de saída: religa depois de disjuntor (1), conferindo antes se o Chrome e
o Legal One respondem; encerra na fila vazia (0), no erro de uso (2), na sessão
expirada (3) e no `Ctrl+C` (130). O Python sai destacado com `Start-Process` —
rodada longa como tarefa de fundo do terminal morre sem aviso — e o log vai para
`logs\rodada_<data-hora>.log`. Tudo depois de `--` vai para o `main.py`:

```powershell
$a = @('-File','scripts\rodada_ate_a_meta.ps1','-Planilha','"Planilhas\Faturamento 2024.xlsx"','-Meta','1000','--','--tarefa','auto')
Start-Process powershell -ArgumentList $a -WindowStyle Hidden
Get-Content logs\rodada_*.log -Wait -Tail 20
```

---

## Quando algo dá errado

### O programa não conecta no Chrome (código 1)

A janela com `--remote-debugging-port=9222` está aberta? Ela precisa continuar
aberta durante toda a rodada. Se estiver aberta e mesmo assim falhar, confirme a
porta com `DEBUG_PORT`.

### "SESSAO EXPIRADA" (código 3)

O Legal One redirecionou para a tela de login. A rodada para na hora, de
propósito — sem isso, todo o resto da fila viraria erro em silêncio. Faça login
naquela janela do Chrome e rode **o mesmo comando de novo**; o progresso está
salvo.

### "PARADO: 25 processos seguidos sem ser encontrados" (código 1)

O disjuntor. Numa rodada saudável os "não encontrado" aparecem espalhados; muitos
seguidos significa que a sessão caiu sem redirecionar para o login. O programa
descarta esses registros suspeitos do ledger (só os pendentes — trabalho
confirmado nunca é apagado) e eles voltam para a fila. Confira o Chrome, refaça o
login e repita o comando.

### "PARADO: 3 falhas consecutivas" (código 1)

O segundo disjuntor. Timeout, elemento que não apareceu e outras falhas de
automação viram `erro` e podem ocorrer isoladamente, mas três `erro` seguidos
fazem a rodada parar. Confira o Chrome e o Legal One; depois rode novamente com
`--retentar`. Este disjuntor não descarta registros do ledger: os erros ficam
salvos para a nova tentativa.

### Todo processo falha com "element click intercepted" no Salvar

Quase sempre é um **aviso in-app do Legal One** (Pendo) no canto de baixo da
tela, por cima do botão Salvar. Ele volta em toda página até alguém dispensar, e
em 16/09/2026 fez o disjuntor disparar a cada reinício. Desde a 1.7.0 o programa
clica sozinho em **"Ok, entendi"** (ou no X do aviso) antes do Salvar, e numa
interceptação espera a máscara de carregamento sair e tenta o clique uma segunda
vez. Se ainda assim acontecer, o aviso mudou de formato: abra a aba de trabalho,
dispense à mão e repita o comando. Botões do aviso que abrem outra página (por
exemplo "Canais de Atendimento") nunca são clicados.

### "SalvarIncerto: nao salvou: formulario nao avancou"

O Salvar foi clicado e a página não saiu do formulário. Diferente dos outros
erros, **a tarefa pode ter sido gravada**: em 15/09/2026, processos com esse erro
recadastrados no `--retentar` ficaram com a tarefa em dobro.

Por isso o erro guarda no `DETALHE` quantas tarefas iguais o processo tinha
antes do clique (`[tarefas antes do Salvar: N]`); um `Ctrl+C` no meio do
cadastro deixa a mesma marca. Na retentativa o programa conta de novo: se subiu,
o Salvar tinha gravado — o processo vira `ok` com "conferido pela contagem" e
**não** é cadastrado outra vez. Se não subiu, cadastra normalmente.

Dois limites: sem contagem (`--rapido`) não há conferência; e registros de antes
da 1.7.0 não têm a marca, então um `erro` antigo com "nao salvou" é recadastrado
como sempre foi — confira esses à mão na aba de compromissos do processo.

**Não confira logo depois do Salvar.** A lista de tarefas da pasta demora a
mostrar uma tarefa recém-gravada, e uma checagem imediata diz "não existe" para
o que existe. Cadastrar de novo por causa disso gera duplicata.

### `erro` subindo rápido no placar

Sinal de que algo mudou no Legal One. Vale parar e olhar `logs/faturamento.log`:
o motivo de cada falha fica na coluna `DETALHE` do `relatorio.csv` também.
Depois de resolver, `--retentar` recoloca os erros na fila.

### O ledger diz cadastrado mas a tarefa não existe

Até a versão 1.7, o modo de falha mais caro, porque não aparecia em lugar nenhum
enquanto rodava. Quando o Legal One não grava, ele devolve o formulário em
`/processos/tarefas/Edit` com a mensagem na tela; o programa reconhecia sucesso
por ter saído de `CreateFromProcesso`, então gravava `ok`. O log dizia
`cadastrada`, o placar não acusava nada, e a planilha do dia ia para a
supervisão com linhas que não existem no sistema.

| Mensagem na tela | Causa |
| --- | --- |
| Aviso *"... é anterior à data atual. Deseja salvar mesmo assim?"* | Data anterior a hoje — a rodada atravessou a meia-noite |
| `O status selecionado não pode ser 'Pendente' quando a data de conclusão for anterior à data atual` | Pendente com data passada |
| `O conteúdo informado no campo 'Nome' já existe` | O responsável já consta como envolvido da tarefa, e é preenchido de novo |

**Desde a 1.8 isso vira `erro`**, com a mensagem no `DETALHE` do
`relatorio.csv`, e volta com `--retentar`. A exceção é o aviso de data passada
quando o dia foi pedido (`--data`, `--fim` ou a coluna da planilha): aí ele é
confirmado e a tarefa grava, e o detalhe diz
`cadastrada (data anterior a hoje confirmada)`.

O `ok` gravado **antes** da 1.8 não é revisto sozinho: rodadas antigas ainda
precisam da auditoria abaixo.

**Como auditar uma rodada.** Com o `scripts\auditar.py`, que lê no ledger os
cadastros de um recorte de horário e confere cada um na grade do processo (ver
[Scripts de operação](#scripts-de-operação)):

```powershell
python scripts\auditar.py --desde 2026-09-10T14:00 --ate 2026-09-11 --saida "Cadastros\Auditoria rodada 10-09.csv"
```

Gasta ~2,5 s por processo (uma rodada de 2.600 leva perto de duas horas) e
exige o Chrome livre, como a rodada. Cada ausente é conferido **duas vezes**
antes de sair como `NAO`: é esse resultado que decide o que sai do ledger.

O que não existir deve ser **removido** do ledger (`delete from processos where
cnj = ? and tarefa = ?`), e não marcado como erro — assim volta à fila numa
rodada normal, sem depender de `--retentar`. Faça backup do ledger antes e
refaça os relatórios com `--relatorio` depois.

Medido em 09-10/09/2026: 208 ausentes em 3.003 cadastros (6,9%), distribuídos
de forma que não se explica por tribunal nem por horário.

### Código 2 com "Tarefa que nao da para cadastrar no Legal One"

A conferência do início da rodada não achou exatamente um tipo ou um
responsável para o que foi pedido. A mensagem diz o que existe: os caminhos do
tipo repetido ("escreva o caminho"), os parecidos com o tipo que não existe, os
usuários que casam com o nome ambíguo. Corrija o `tarefas.toml`, a flag ou a
coluna e repita — nada foi cadastrado. Responsável que "não é usuário ativo"
pode ter sido desativado no Legal One.

### Código 2 com "Data que o Legal One nao aceita" ou "Linha(s) sem tarefa completa"

A conferência de antes do Chrome achou linhas que o Legal One recusaria (início
depois da conclusão, Pendente com conclusão no passado) ou que não dizem a
tarefa inteira (sem status, sem responsável, status que não existe, data mal
escrita). A mensagem traz aba, linha e número de cada uma. Corrija a planilha
ou as flags e repita.

### `nao_encontrado` alto

Esperado. Boa parte da planilha é de processo antigo que não está no Legal One —
a taxa medida numa amostra foi de cerca de 1 em 4. A lista para conferir à mão
está em `data/nao_encontrados.csv`.

### Preciso parar no meio

`Ctrl+C` encerra limpo (código 130): exporta os relatórios e mantém o progresso.
Fechar a janela do terminal também não corrompe o ledger (ele usa WAL e commita
cada processo na hora), mas perde os relatórios daquela rodada — que podem ser
refeitos com `--relatorio`.

### O que vigiar durante a rodada

A cada 25 processos sai uma linha de progresso com ritmo, tempo decorrido, ETA e
o placar:

```
... 250/11954 | 9.6s/processo | decorrido 0h40m | falta ~31h12m | {'ok': 130, 'recadastrada': 57, 'erro': 2, 'nao_encontrado': 61, 'ja_existia': 0}
```

---

## Ajustes finos

Constantes em `src/config.py`, para o caso de o Legal One ficar mais lento ou
mudar de comportamento.

| Constante | Valor | O que faz |
| --- | --- | --- |
| `TIMEOUT_PADRAO` | `20` | Segundos de espera por elemento antes de desistir |
| `DEBOUNCE_DELAY` | `0.4` | Pausa antes do ENTER no lookup de envolvido |
| `PAUSA_ENTRE_PROCESSOS` | `0.5` | Respiro entre um processo e o próximo |
| `MAX_NAO_ENCONTRADOS_SEGUIDOS` | `25` | Quantos "não encontrado" seguidos disparam o disjuntor |
| `MAX_ERROS_SEGUIDOS` | `3` | Quantos `erro` seguidos param a rodada |
| `PASSO_PROGRESSO` | `25` | De quantos em quantos processos sai a linha de progresso (em `src/main.py`) |
| `TIPO_ACEITO` | `"Processo"` | Só cadastra em pasta deste tipo (recurso e incidente repetem o CNJ) |
| `STATUS_RECUSADOS_NO_PASSADO` | `{"Pendente"}` | Status que o Legal One não aceita com conclusão no passado; conferido antes do Chrome |
| `DURACAO_PADRAO` | 30 minutos | Duração da tarefa quando só uma das horas é pedida (em `src/datas.py`) |

Aumentar `TIMEOUT_PADRAO` é o primeiro ajuste a tentar se começarem a aparecer
muitos erros de timeout numa rede lenta.
