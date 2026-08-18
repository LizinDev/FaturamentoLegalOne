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
- [Arquivos gerados](#arquivos-gerados)
- [A planilha de entrada](#a-planilha-de-entrada)
- [Perfis de tarefa](#perfis-de-tarefa)
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
python main.py --planilha "C:\Users\Kamila\Downloads\Faturamento.xlsx" --limite 3
```

Sem `--executar` isso é simulação: preenche o formulário inteiro e não salva
nada. Se as três primeiras linhas passarem, a conexão, o login e a planilha
estão bons.

---

## A receita de um dia

A cota combinada é de ~500 cadastros por dia, alternando as duas planilhas.

**Dia ímpar — faturamento:**

```powershell
cd C:\Users\Kamila\projetos\FaturamentoLegalOne\src
python main.py --planilha "C:\Users\Kamila\Downloads\Faturamento.xlsx" --max-cadastros 500 --executar
```

**Dia par — defesa:**

```powershell
python main.py --planilha "C:\Users\Kamila\Downloads\Defesas.xlsx" --tarefa defesa-faturada --max-cadastros 500 --executar
```

O nome do arquivo não é livre: cada perfil exige um trecho no caminho da
planilha (`Faturamento` e `Defesa`) e recusa o par errado com código 2 — ver
[`--forcar-planilha`](#valores-da-tarefa).

**Planilha única, com as duas tarefas misturadas na mesma aba:**

```powershell
python main.py --planilha "..\Planilha de Faturamento.xlsx" --abas "2019-2020-2021" --tarefa auto --max-cadastros 500 --executar
```

Aqui não se alterna planilha: cada linha recebe a tarefa que a coluna
`TIPO DE COBRANÇA` indica, e a cota do dia sai da fila inteira. Ver
[`--tarefa auto`](#valores-da-tarefa).

Não é preciso anotar por onde parou nem qual planilha foi a última. O ledger
guarda o progresso **por tarefa**, então cada rodada pega os próximos 500 ainda
não cadastrados daquele perfil. Ao terminar, a planilha do dia é gerada sozinha
em `data/`.

Ao fim de cada rodada, o resumo sai no console:

```
Resumo desta rodada: {'ok': 350, 'recadastrada': 150, 'erro': 3, 'nao_encontrado': 166, 'ja_existia': 0}
Planilha do dia:      ...\data\cadastrados_2026-07-30.xlsx
```

`ok` e `recadastrada` somados são o que entrou no Legal One naquela rodada — é o
que a planilha do dia mostra e o que corresponde à cota. `recadastrada` é o
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
| `--tarefa` | `faturamento-final` \| `defesa-faturada` \| `auto` | `faturamento-final` |
| `--forcar-planilha` | — | desligada |
| `--abas` | um ou mais nomes de aba | todas as abas |
| `--tipo-contem` | trecho de texto | sem filtro |
| `--status-planilha` | texto exato | sem filtro |
| `--limite` | inteiro ≥ 1 | sem limite |
| `--max-cadastros` | inteiro ≥ 1 | sem limite |
| `--processo` | um ou mais números CNJ | toda a planilha |
| `--data` | `DD/MM/AAAA` | hoje |
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
deu erro **não consomem cota**. Recadastro consome: ele cria tarefa no Legal One
como qualquer outro (com `--pular-existentes`, o processo é pulado e não conta).

É esta a flag que corresponde à meta combinada: `--max-cadastros 500` são 500
tarefas criadas no Legal One. `--limite 500` seriam 500 processos olhados,
resultando em bem menos cadastros (cerca de 1 em 4 da planilha não existe no
Legal One).

As duas exigem inteiro **maior que zero**. `--max-cadastros 0` é recusado com
código 2 de propósito: zero é falso em Python e passaria como "sem cota",
rodando a planilha inteira em vez de parar na hora.

### Valores da tarefa

**`--tarefa PERFIL`**

Escolhe qual tarefa cadastrar. O perfil define a descrição gravada:

| Perfil | Descrição gravada |
| --- | --- |
| `faturamento-final` | `FATURAMENTO FINAL` |
| `defesa-faturada` | `DEFESA FATURADA` |
| `auto` | a que a coluna `TIPO DE COBRANÇA` disser, linha a linha |

Tipo (`Diversos`), status (`Cumprido`) e responsável (`Heloiza Helena de
Araujo`) são iguais nos dois. O ledger é indexado por **(processo, tarefa)**, de
modo que o mesmo processo pode receber as duas sem que uma rodada pule a outra.

**`--tarefa auto`** é para a planilha que mistura as duas tarefas na mesma aba.
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
python main.py --planilha "..\Planilha de Faturamento.xlsx" --abas "2019-2020-2021" --tarefa auto --max-cadastros 500 --executar
```

**`--data DD/MM/AAAA`**

Data de início e fim da tarefa. Sem ela, hoje. Data mal formada ou inexistente
(`31/02/2026`) encerra com código 2 antes de abrir o Chrome.

**`--forcar-planilha`**

Desliga a trava que confere se a planilha combina com o `--tarefa` escolhido.

A trava está **ligada** nos dois perfis: o caminho da planilha precisa conter o
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
2. **Deduplicação por número CNJ.** O mesmo processo repetido em várias linhas ou
   abas vira uma entrada só, com as origens agregadas.
3. **Remoção do que já está no ledger** — tudo, ou só `ok` e `recadastrada` se
   `--retentar`. Numa simulação, nada é removido.
4. **`--processo`**, se usada: mantém só os números pedidos e desfaz o passo 3.
5. **`--limite`**: corta a fila resultante.
6. **`--max-cadastros`**: interrompe o laço durante a execução.

Combinações que o programa recusa:

| Combinação | O que acontece |
| --- | --- |
| `--so-buscar --executar` | Encerra com código 2 |
| `--pular-existentes --rapido` | Encerra com código 2 |
| nem `--planilha` nem `--relatorio` | Encerra com código 2 |
| planilha sem o trecho exigido pelo `--tarefa` | Encerra com código 2 (salvo `--forcar-planilha`) |

Combinações inofensivas, que só rendem um aviso e seguem:

| Combinação | O que acontece |
| --- | --- |
| `--so-buscar --max-cadastros` | Avisa que não tem efeito (nada é cadastrado); para encurtar o pré-voo, `--limite` |
| `--dia` sem `--relatorio` | Avisa e ignora |

---

## Códigos de saída

| Código | Significado |
| --- | --- |
| `0` | Terminou a fila (inclusive "nada a fazer" e cota do dia atingida) |
| `1` | Abortou: não conectou ao Chrome, sessão expirada ou disjuntor |
| `2` | Erro de uso: flag faltando, combinação inválida, planilha × tarefa incompatível, data ou planilha inválida, aba inexistente |
| `130` | `Ctrl+C` |

O código 2 acontece **antes** de qualquer cadastro. O 1 e o 130 podem acontecer
no meio da rodada — em qualquer um dos três o progresso está salvo no ledger e
repetir o mesmo comando retoma de onde parou.

---

## Variáveis de ambiente

| Variável | Padrão | Para que serve |
| --- | --- | --- |
| `DEBUG_PORT` | `9222` | Porta de debug do Chrome |
| `LOG_LEVEL` | `INFO` | `DEBUG` para log detalhado. Valor inválido cai em `INFO` em vez de impedir a rodada |

```powershell
$env:LOG_LEVEL = "DEBUG"
python main.py --planilha "..." --limite 5
```

---

## Arquivos gerados

Tudo em `data/` e `logs/`, que são criados sozinhos e **não vão para o Git** —
contêm números de processo e nomes de clientes.

| Arquivo | O que é |
| --- | --- |
| `data/ledger.sqlite3` | O progresso. É o que permite retomar sem cadastrar nada duas vezes |
| `data/relatorio.csv` | Uma linha por par (processo, tarefa) já processado, com situação e detalhe |
| `data/nao_encontrados.csv` | Só o que precisa de conferência manual. Sai do ledger, então é acumulado entre rodadas |
| `data/nao_encontrados_simulacao.csv` | O mesmo, quando a rodada é simulação. Arquivo separado para não apagar a lista acumulada |
| `data/cadastrados_AAAA-MM-DD.xlsx` | A planilha do dia, a que vai para o supervisor |
| `logs/faturamento.log` | O log completo, em UTF-8 |

Os CSVs usam `;` como separador e UTF-8 com BOM — abrem direto no Excel
brasileiro, sem assistente de importação.

O ledger é o arquivo que importa preservar. Perdê-lo não causa cadastro
duplicado (a checagem de duplicata continua protegendo), mas custa reprocessar
milhares de buscas. Ele fica só nesta máquina.

---

## A planilha de entrada

Qualquer aba que tenha uma coluna `PROCESSO` no **cabeçalho da primeira linha**.
Aba sem essa coluna é ignorada com um aviso, e não derruba a leitura.

| Coluna | Obrigatória | Uso |
| --- | --- | --- |
| `PROCESSO` | sim | O número CNJ a buscar |
| `TIPO DE COBRANÇA` | não | Filtro `--tipo-contem`, coluna dos relatórios e, com `--tarefa auto`, a tarefa daquela linha |
| `STATUS LEGAL ONE` | não | Filtro `--status-planilha` e coluna dos relatórios |

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

Ficam em `PERFIS`, no fim de `src/config.py`. Para mudar responsável, status ou
tipo, é lá.

```python
PERFIS = {
    p.nome: p for p in [
        PerfilTarefa("faturamento-final", "FATURAMENTO FINAL",
                     dica_arquivo="Faturamento"),
        PerfilTarefa("defesa-faturada", "DEFESA FATURADA",
                     dica_arquivo="Defesa"),
    ]
}
```

`dica_arquivo` é o trecho que precisa aparecer no caminho da planilha — é a
trava contra parear a planilha de uma tarefa com o perfil da outra. Se os
arquivos mudarem de nome, é este campo que se ajusta; deixá-lo vazio desliga a
conferência daquele perfil.

`--tarefa auto` não é um perfil a mais nessa lista: ele escolhe, para cada
linha, um dos perfis acima. O casamento é pela descrição — acrescentar um perfil
novo já o torna disponível no modo auto, com o texto da coluna
`TIPO DE COBRANÇA` tendo que ser igual à descrição.

---

## Quando algo dá errado

### O programa não conecta no Chrome (código 1)

A janela com `--remote-debugging-port=9222` está aberta? Ela precisa continuar
aberta durante toda a rodada. Se estiver aberta e mesmo assim falhar, confirme a
porta com `DEBUG_PORT`.

### "SESSAO EXPIRADA" (código 1)

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

### `erro` subindo rápido no placar

Sinal de que algo mudou no Legal One. Vale parar e olhar `logs/faturamento.log`:
o motivo de cada falha fica na coluna `DETALHE` do `relatorio.csv` também.
Depois de resolver, `--retentar` recoloca os erros na fila.

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
| `PASSO_PROGRESSO` | `25` | De quantos em quantos processos sai a linha de progresso (em `src/main.py`) |
| `TIPO_ACEITO` | `"Processo"` | Só cadastra em pasta deste tipo (recurso e incidente repetem o CNJ) |

Aumentar `TIMEOUT_PADRAO` é o primeiro ajuste a tentar se começarem a aparecer
muitos erros de timeout numa rede lenta.
