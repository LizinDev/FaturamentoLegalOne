"""Configuracoes centralizadas do cadastro em lote de tarefas no Legal One."""
import contextlib
import dataclasses
import logging
import os
import sys
import unicodedata
from pathlib import Path

import datas

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: o tomllib so entrou na 3.11
    import tomli as tomllib

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

    Os de producao vem do tarefas.toml (ver carregar_perfis), com todos os
    campos escritos por extenso. Os padroes abaixo so servem a quem monta um
    perfil no codigo — os testes e a tarefa avulsa, que exige status e
    responsavel na linha de comando antes de chegar aqui.
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
    # None deixa o que o formulario poe: vazio, ou o que o subtipo com contagem
    # de prazo sugerir (a data de hoje).
    data_publicacao: str | None = None
    data_disponibilizacao: str | None = None

    @classmethod
    def do_perfil(cls, perfil: PerfilTarefa, quando: "str | datas.Datas",
                  confirmar_data_passada: bool = False) -> "Tarefa":
        """A tarefa de um perfil com as datas de um cadastro.

        `quando` e uma data so (inicio e fim no mesmo dia, hora do formulario)
        ou as Datas completas que a Agenda resolveu.
        """
        if isinstance(quando, str):
            quando = datas.Datas(quando, None, quando, None)
        return cls(
            descricao=perfil.descricao,
            tipo=perfil.tipo,
            status=perfil.status,
            responsavel=perfil.responsavel,
            data_inicio=quando.inicio,
            data_fim=quando.fim,
            hora_inicio=quando.hora_inicio,
            hora_fim=quando.hora_fim,
            confirmar_data_passada=confirmar_data_passada,
            tipo_id=perfil.tipo_id,
            data_publicacao=quando.publicacao,
            data_disponibilizacao=quando.disponibilizacao,
        )

    @property
    def inicio(self) -> str:
        """Data (e hora, se houver) de inicio, como vai para o ledger."""
        return " ".join(filter(None, (self.data_inicio, self.hora_inicio)))

    @property
    def fim(self) -> str:
        return " ".join(filter(None, (self.data_fim, self.hora_fim)))


# "Nao cumprido" contem "Cumprido": o casamento no lookup precisa ser exato.
STATUS_VALIDOS = {
    "Pendente": "0",
    "Cumprido": "1",
    "Não cumprido": "2",
    "Cancelado": "3",
    "Iniciado": "4",
    "Recusado": "5",
}


def _sem_acento(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", str(texto))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(texto.split()).casefold()


_STATUS_POR_FORMA = {_sem_acento(s): s for s in STATUS_VALIDOS}


def status_canonico(texto: str) -> str | None:
    """O status como o Legal One o escreve, ou None se nao for um dos seis.

    Aceita sem acento e em qualquer caixa ("nao cumprido"), porque vem de
    planilha, de linha de comando e do tarefas.toml. O que sai daqui e o texto
    exato que o lookup de status precisa.
    """
    return _STATUS_POR_FORMA.get(_sem_acento(texto))


# Modo em que a tarefa de cada processo sai da coluna TIPO DE COBRANCA, linha a
# linha, em vez de valer uma so para a rodada inteira. E para a planilha que
# mistura as duas tarefas na mesma aba.
NOME_AUTO = "auto"
# Modo em que a tarefa inteira (descricao, tipo, status, responsavel) sai das
# colunas "... DA TAREFA" de cada linha. Ver COLUNAS_DA_TAREFA.
NOME_PLANILHA = "planilha"
# A tarefa de --descricao, sem perfil do arquivo.
NOME_AVULSA = "avulsa"
NOMES_RESERVADOS = {NOME_AUTO, NOME_PLANILHA, NOME_AVULSA}

# Aqui nao ha trava de nome de arquivo, e de proposito: a garantia de nao parear
# planilha errada com tarefa errada vem da propria celula de cada linha, que e
# mais forte do que o nome do arquivo.
PERFIL_AUTO = PerfilTarefa(NOME_AUTO, "(da coluna TIPO DE COBRANÇA)")
PERFIL_PLANILHA = PerfilTarefa(NOME_PLANILHA, "(da coluna DESCRIÇÃO DA TAREFA)")

# --- Arquivo de perfis -------------------------------------------------------

# Os perfis ficam num arquivo, e nao no codigo, para que uma tarefa nova seja
# uma secao a mais num texto — sem editar Python. Fica no repositorio, e nao
# fora dele, para as duas maquinas cadastrarem exatamente a mesma coisa.
ARQUIVO_PERFIS = BASE_DIR / "tarefas.toml"

_OBRIGATORIOS = ("descricao", "tipo", "status", "responsavel")
_OPCIONAIS = ("dica_arquivo",)


class ErroPerfis(ValueError):
    """O tarefas.toml nao descreve perfis validos."""


def carregar_perfis(caminho: str | Path) -> dict[str, PerfilTarefa]:
    """Le e valida os perfis do arquivo TOML.

    Tipo, status e responsavel sao obrigatorios em todo perfil do arquivo:
    padrao escondido no codigo e como uma tarefa nova acaba no nome de quem
    ninguem escolheu. Chave desconhecida tambem e erro — "responsável" com
    acento, digitado a mao, seria ignorada em silencio e o perfil sairia sem
    responsavel.
    """
    try:
        with open(caminho, "rb") as f:
            bruto = tomllib.load(f)
    except FileNotFoundError:
        raise ErroPerfis(f"arquivo de perfis nao encontrado: {caminho}")
    except tomllib.TOMLDecodeError as e:
        raise ErroPerfis(f"{caminho} nao e um TOML valido: {e}")

    perfis: dict[str, PerfilTarefa] = {}
    problemas: list[str] = []
    for nome, campos in bruto.items():
        if not isinstance(campos, dict):
            problemas.append(f"{nome!r} nao e uma secao [{nome}]")
            continue
        if nome in NOMES_RESERVADOS:
            problemas.append(f"[{nome}]: nome reservado")
            continue
        desconhecidos = set(campos) - set(_OBRIGATORIOS) - set(_OPCIONAIS)
        faltando = [c for c in _OBRIGATORIOS if not str(campos.get(c, "")).strip()]
        if desconhecidos:
            problemas.append(f"[{nome}]: campo(s) desconhecido(s) "
                             f"{sorted(desconhecidos)}")
        if faltando:
            problemas.append(f"[{nome}]: falta {', '.join(faltando)}")
        if desconhecidos or faltando:
            continue
        status = status_canonico(campos["status"])
        if status is None:
            problemas.append(f"[{nome}]: status {campos['status']!r} nao existe "
                             f"(use {', '.join(STATUS_VALIDOS)})")
            continue
        perfis[nome] = PerfilTarefa(
            nome=nome,
            descricao=" ".join(str(campos["descricao"]).split()),
            tipo=str(campos["tipo"]).strip(),
            status=status,
            responsavel=" ".join(str(campos["responsavel"]).split()),
            dica_arquivo=str(campos.get("dica_arquivo", "")).strip(),
        )

    # Duas secoes com a mesma descricao dividiriam as mesmas linhas do ledger:
    # a rodada de uma pularia os processos da outra.
    por_descricao: dict[str, list[str]] = {}
    for perfil in perfis.values():
        por_descricao.setdefault(perfil.descricao.upper(), []).append(perfil.nome)
    for descricao, nomes in por_descricao.items():
        if len(nomes) > 1:
            problemas.append(f"descricao {descricao!r} repetida em {nomes}")

    if problemas:
        raise ErroPerfis(f"{caminho}:\n  " + "\n  ".join(problemas))
    return perfis


# Arquivo com problema nao derruba o import: --help e --relatorio nao precisam
# de perfil, e a rodada transforma ERRO_PERFIS em erro de uso (codigo 2).
try:
    PERFIS = carregar_perfis(ARQUIVO_PERFIS)
    ERRO_PERFIS = ""
except ErroPerfis as _e:
    PERFIS = {}
    ERRO_PERFIS = str(_e)

# Vale quando --tarefa nao e dado (e nem --descricao). Se o arquivo nao tiver
# este perfil, a rodada exige --tarefa.
PERFIL_PADRAO = "faturamento-final" if "faturamento-final" in PERFIS else None

# Descricao -> perfil. Serve para resolver a tarefa de uma linha da planilha no
# modo auto (e no modo planilha, quando a descricao e a de um perfil).
PERFIS_POR_DESCRICAO = {p.descricao: p for p in PERFIS.values()}

_DESCRICOES_POR_TIPO = {d.upper(): d for d in PERFIS_POR_DESCRICAO}


def tarefa_do_tipo(tipo: str) -> str | None:
    """Tarefa correspondente a um TIPO DE COBRANCA da planilha (None se nenhuma).

    O casamento e pelo texto inteiro, ignorando caixa e espacos sobrando. Nao ha
    sinonimo nem casamento por pedaco: a coluna e texto livre, e "CONTESTACAO" ou
    "ENCERRAMENTO S/ EXITO" nao devem virar tarefa por acidente.
    """
    return _DESCRICOES_POR_TIPO.get(" ".join(str(tipo).split()).upper())


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

# As colunas do modo --tarefa planilha. O sufixo "DA TAREFA" e de proposito:
# planilha juridica costuma ter RESPONSAVEL (o advogado do caso) e STATUS (o do
# processo), e nenhuma delas pode mudar a tarefa sem ninguem pedir.
COLUNA_DESCRICAO_TAREFA = "DESCRIÇÃO DA TAREFA"
COLUNA_TIPO_TAREFA = "TIPO DA TAREFA"
COLUNA_STATUS_TAREFA = "STATUS DA TAREFA"
COLUNA_RESPONSAVEL_TAREFA = "RESPONSÁVEL DA TAREFA"
# Datas no mesmo modo. Inicio e conclusao aceitam hora na mesma celula
# ("29/09/2026 09:00") ou celula de data/hora do Excel; publicacao e
# disponibilizacao sao so data, como no formulario.
COLUNA_INICIO_TAREFA = "INÍCIO DA TAREFA"
COLUNA_CONCLUSAO_TAREFA = "CONCLUSÃO DA TAREFA"
COLUNA_PUBLICACAO_TAREFA = "PUBLICAÇÃO DA TAREFA"
COLUNA_DISPONIBILIZACAO_TAREFA = "DISPONIBILIZAÇÃO DA TAREFA"

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
