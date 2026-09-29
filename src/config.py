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

# FATURAMENTO_LOG_FILE vazio desliga o arquivo. Os testes usam isso: sem a
# variavel, cada pytest gravava centenas de linhas de rodada simulada (erros,
# sessao expirada, processos "A" e "B") no log de producao, que e justamente
# onde se procura o motivo das falhas reais.
LOG_FILE = os.environ.get("FATURAMENTO_LOG_FILE", str(LOGS_DIR / "faturamento.log"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

_handlers: list[logging.Handler] = [logging.StreamHandler()]
if LOG_FILE:
    _handlers.insert(0, logging.FileHandler(LOG_FILE, encoding="utf-8"))
logging.basicConfig(
    # LOG_LEVEL invalido cai em INFO: um nome errado na variavel de ambiente
    # nao deve impedir a rodada de comecar.
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=_handlers,
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
    # Caminho na arvore de tipos do Legal One: "Diversos" (tipo) ou "Diversos /
    # Contato Telefônico" (subtipo). O nome sozinho tambem vale quando e unico.
    # Diversos ja e o padrao do formulario (TipoId=tipo_4) e nao e reescolhido.
    tipo: str = "Diversos"
    status: str = "Cumprido"  # StatusId=1; o padrao do formulario e Pendente (0)
    # Usuario ativo do Legal One. Casa sem ligar para acento e caixa, e pode ser
    # so parte do nome enquanto for o unico usuario que casa (ver catalogo).
    responsavel: str = "Heloiza Helena de Araujo"
    # Trecho que deve aparecer no caminho da planilha. Serve de trava contra
    # rodar a planilha de uma tarefa com o perfil da outra. Vazio = sem trava.
    dica_arquivo: str = ""
    # Preenchido pela checagem do inicio da rodada, junto com tipo e responsavel
    # reescritos como o Legal One os escreve. Vazio = ainda nao conferido.
    tipo_id: str = ""


@dataclasses.dataclass(frozen=True)
class Tarefa:
    """A tarefa concreta que vai para o formulario de um processo.

    O perfil diz *qual* tarefa; a Tarefa junta a isso *quando*, com as datas
    resolvidas no momento do cadastro. E ela que vai para o formulario e para o
    ledger, para que o registro guarde exatamente o que foi enviado ao Legal
    One, e nao o que o perfil diria no dia em que alguem for conferir.
    """

    descricao: str
    tipo: str                       # caminho, como o Legal One o escreve
    status: str
    responsavel: str                # nome completo do usuario no Legal One
    data_inicio: str                # DD/MM/AAAA
    data_fim: str                   # DD/MM/AAAA
    # None deixa a hora que o formulario sugere, que e a proxima hora cheia (as
    # 13h13 ele traz 14h00-14h30). Hora passada no proprio dia e aceita: o Legal
    # One so compara a data (testado em 29/09/2026).
    hora_inicio: str | None = None  # HH:MM:SS
    hora_fim: str | None = None
    # Data anterior a hoje faz o Legal One pedir confirmacao ("Deseja salvar
    # mesmo assim?"). So se responde Sim quando a data foi escolhida de
    # proposito; a data de hoje que virou ontem durante o cadastro nao conta.
    confirmar_data_passada: bool = False
    # Id na arvore de tipos ("tipo_4", "subtipo_9"). Vazio quando o tipo nao
    # passou pela checagem: ai o formulario so confere o tipo que ja vem nele.
    tipo_id: str = ""

    @classmethod
    def do_perfil(cls, perfil: PerfilTarefa, data: str,
                  confirmar_data_passada: bool = False) -> "Tarefa":
        """A tarefa de um perfil numa data, com inicio e fim no mesmo dia."""
        return cls(
            descricao=perfil.descricao,
            tipo=perfil.tipo,
            status=perfil.status,
            responsavel=perfil.responsavel,
            data_inicio=data,
            data_fim=data,
            confirmar_data_passada=confirmar_data_passada,
            tipo_id=perfil.tipo_id,
        )

    @property
    def inicio(self) -> str:
        """Data (e hora, se houver) de inicio, como vai para o ledger."""
        return " ".join(filter(None, (self.data_inicio, self.hora_inicio)))

    @property
    def fim(self) -> str:
        return " ".join(filter(None, (self.data_fim, self.hora_fim)))


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
# modo auto. Tipo, status e responsavel de um cadastro ja feito nao saem mais
# daqui: o ledger guarda os valores que foram de fato enviados.
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

# Status que o Legal One nao aceita com data de conclusao anterior a hoje: ele
# devolve "O status selecionado nao pode ser 'Pendente' quando a data de
# conclusao for anterior a data atual" (testado em 29/09/2026). Cumprido e
# aceito depois de confirmar o aviso. Os demais status nao foram testados; se
# forem recusados, a recusa aparece como erro com a mensagem do Legal One.
STATUS_RECUSADOS_NO_PASSADO = {"Pendente"}

# --- Planilha ----------------------------------------------------------------

COLUNA_PROCESSO = "PROCESSO"
# A coluna que diz a cobranca de cada linha nao tem nome unico nas planilhas que
# chegam: as antigas trazem "TIPO DE COBRANÇA", a de 2022 traz "TAREFA" e a de
# 2026 traz "TAREFA PARA LANÇAR". Sao nomes do mesmo campo, entao vale a
# primeira que a aba tiver — sem isso a aba inteira e lida como se a coluna
# estivesse vazia, e no modo auto todas as linhas sao puladas em silencio.
COLUNAS_TIPO_COBRANCA = ("TIPO DE COBRANÇA", "TAREFA", "TAREFA PARA LANÇAR")
# Nome canonico, para as mensagens de log e a ajuda da CLI.
COLUNA_TIPO_COBRANCA = COLUNAS_TIPO_COBRANCA[0]
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
# Falhas de automacao em sequencia quase sempre significam que o Chrome ou a
# sessao deixaram de responder; continuar so transforma a fila inteira em erro.
MAX_ERROS_SEGUIDOS = 3
# Quantas vezes digitar a descricao antes de desistir. O formulario de tarefa as
# vezes termina de inicializar depois de visivel e apaga o que foi digitado: em
# 13-15/09/2026 foram 87 erros "campo Descricao ficou ''", que em sequencia
# disparavam o disjuntor sem nada de errado com o processo.
TENTATIVAS_DESCRICAO = 3
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


VERSION = "1.8.0"
PROJECT_NAME = "Cadastro de tarefas em lote - Legal One"
