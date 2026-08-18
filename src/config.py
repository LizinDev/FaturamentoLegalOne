"""Configuracoes centralizadas do cadastro em lote de tarefas no Legal One."""
import contextlib
import dataclasses
import logging
import os
import sys
from pathlib import Path

# O console do Windows costuma abrir em cp1252 e os logs tem acento ("Nao
# cumprido", nomes de cliente). Sem isto, um UnicodeEncodeError dentro do
# logging derrubaria a rodada por causa de uma letra.
for fluxo in (sys.stdout, sys.stderr):
    # Um fluxo redirecionado (pytest, pipe) pode nao ter reconfigure.
    with contextlib.suppress(AttributeError, ValueError):
        fluxo.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = str(LOGS_DIR / "faturamento.log")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    # LOG_LEVEL invalido cai em INFO: um nome errado na variavel de ambiente
    # nao deve impedir a rodada de comecar.
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

# --- Legal One / NovaJus -----------------------------------------------------

BASE_URL = "https://hasson.novajus.com.br"

# A busca de processos guarda o filtro de status entre sessoes ("salvar criterios
# de filtros"), e o padrao do usuario e Ativo. Mandar StatusSimples[0] vazio
# limpa esse filtro e faz a busca alcancar tambem os arquivados — sem isso,
# 14.801 das 17.749 linhas da planilha simplesmente nao aparecem.
FILTRO_SEM_STATUS = "&StatusSimples%5B0%5D.Id=&StatusSimples%5B0%5D.Value="

URL_BUSCA = BASE_URL + "/processos/processos/search?Search={cnj}" + FILTRO_SEM_STATUS
URL_NOVA_TAREFA = BASE_URL + "/processos/tarefas/CreateFromProcesso/{id}"

# Nas linhas da grade de resultados: Status na coluna 2, Tipo na 3, CNJ na 5.
COL_STATUS = 1
COL_TIPO = 2
COL_PROCESSO = 4

# So cadastramos em pastas do tipo "Processo"; recurso e incidente compartilham
# o mesmo numero CNJ e cadastrar neles duplicaria a tarefa.
TIPO_ACEITO = "Processo"

# --- Perfis de tarefa --------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class PerfilTarefa:
    """Tudo que define uma das tarefas cadastradas em lote.

    Cada planilha tem o seu perfil. Sao perfis nomeados, e nao um texto livre
    na linha de comando, porque parear a planilha errada com a tarefa errada
    criaria centenas de tarefas indevidas — e o nome do perfil e conferido
    contra a lista abaixo antes de qualquer coisa acontecer.
    """

    nome: str               # como se escreve em --tarefa
    descricao: str          # vai no campo Descricao da tarefa
    tipo: str = "Diversos"  # ja e o padrao do formulario (TipoId=tipo_4)
    status: str = "Cumprido"  # StatusId=1; o padrao do formulario e Pendente (0)
    # O lookup de envolvido busca por prefixo; o nome completo confirma que veio
    # a pessoa certa antes de salvar.
    responsavel_busca: str = "Heloiza"
    responsavel_esperado: str = "Heloiza Helena de Araujo"
    # Trecho que deve aparecer no caminho da planilha. Serve de trava contra
    # rodar a planilha de uma tarefa com o perfil da outra. Vazio = sem trava.
    dica_arquivo: str = ""


PERFIS = {
    p.nome: p for p in [
        PerfilTarefa("faturamento-final", "FATURAMENTO FINAL",
                     dica_arquivo="Faturamento"),
        PerfilTarefa("defesa-faturada", "DEFESA FATURADA",
                     dica_arquivo="Defesa"),
    ]
}

PERFIL_PADRAO = "faturamento-final"

# Modo em que a tarefa de cada processo sai da coluna TIPO DE COBRANCA, linha a
# linha, em vez de valer uma so para a rodada inteira. E para a planilha que
# mistura as duas tarefas na mesma aba.
NOME_AUTO = "auto"

# Aqui nao ha trava de nome de arquivo, e de proposito: a garantia de nao parear
# planilha errada com tarefa errada vem da propria celula de cada linha, que e
# mais forte do que o nome do arquivo.
PERFIL_AUTO = PerfilTarefa(NOME_AUTO, "(da coluna TIPO DE COBRANÇA)")

# Descricao -> perfil. Serve para resolver a tarefa de uma linha da planilha no
# modo auto e para reconstruir tipo/status/responsavel a partir do que ficou
# gravado no ledger (que guarda so a descricao).
PERFIS_POR_DESCRICAO = {p.descricao: p for p in PERFIS.values()}

_DESCRICOES_POR_TIPO = {d.upper(): d for d in PERFIS_POR_DESCRICAO}


def tarefa_do_tipo(tipo: str) -> str | None:
    """Tarefa correspondente a um TIPO DE COBRANCA da planilha (None se nenhuma).

    O casamento e pelo texto inteiro, ignorando caixa e espacos sobrando. Nao ha
    sinonimo nem casamento por pedaco: a coluna e texto livre, e "CONTESTACAO" ou
    "ENCERRAMENTO S/ EXITO" nao devem virar tarefa por acidente.
    """
    return _DESCRICOES_POR_TIPO.get(" ".join(str(tipo).split()).upper())

# "Nao cumprido" contem "Cumprido": o casamento no lookup precisa ser exato.
STATUS_VALIDOS = {
    "Pendente": "0",
    "Cumprido": "1",
    "Não cumprido": "2",
    "Cancelado": "3",
    "Iniciado": "4",
    "Recusado": "5",
}

# --- Planilha ----------------------------------------------------------------

COLUNA_PROCESSO = "PROCESSO"
COLUNA_TIPO_COBRANCA = "TIPO DE COBRANÇA"
COLUNA_STATUS_LEGALONE = "STATUS LEGAL ONE"

# --- Execucao ----------------------------------------------------------------

TIMEOUT_PADRAO = 20
DEBOUNCE_DELAY = 0.4       # espera do lookup do NovaJus antes do ENTER
PAUSA_ENTRE_PROCESSOS = 0.5

# Disjuntor para rodada sem supervisao. Numa rodada saudavel os "nao
# encontrado" aparecem espalhados (~1 em 4), entao muitos seguidos nao e
# coincidencia: e sessao caida ou busca quebrada. Sem isso, um logout no meio
# da noite marcaria milhares de processos como inexistentes — e eles seriam
# pulados na retomada.
MAX_NAO_ENCONTRADOS_SEGUIDOS = 25
DEBUG_PORT = int(os.environ.get("DEBUG_PORT", "9222"))
DEBUG_ADDRESS = f"localhost:{DEBUG_PORT}"

LEDGER_FILE = str(DATA_DIR / "ledger.sqlite3")
RELATORIO_CSV = str(DATA_DIR / "relatorio.csv")

# A lista de conferencia da rodada real e acumulada (vem do ledger, com tudo o
# que ja passou). A da simulacao e volatil e so cobre o que aquela passada olhou
# — por isso vai para outro arquivo: rodar uma simulacao de 20 processos nao
# pode apagar a lista de milhares levantada nas rodadas de verdade.
NAO_ENCONTRADOS_CSV = str(DATA_DIR / "nao_encontrados.csv")
NAO_ENCONTRADOS_SIMULACAO_CSV = str(DATA_DIR / "nao_encontrados_simulacao.csv")


def planilha_do_dia(dia: str) -> str:
    """Caminho do .xlsx com os cadastros de um dia (dia = AAAA-MM-DD)."""
    return str(DATA_DIR / f"cadastrados_{dia}.xlsx")


VERSION = "1.4.0"
PROJECT_NAME = "Cadastro de tarefas em lote - Legal One"
