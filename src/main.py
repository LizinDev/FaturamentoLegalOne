"""Cadastro em lote de tarefas no Legal One.

Le a planilha de cobrancas, encontra cada processo no Legal One e cadastra a
tarefa de cada um. A tarefa vem de um perfil do tarefas.toml (--tarefa), de
--descricao/--tipo/--status/--responsavel, da coluna TIPO DE COBRANCA
(--tarefa auto) ou das colunas "... DA TAREFA" de cada linha (--tarefa
planilha). Por padrao roda em simulacao — precisa de --executar para gravar.

Codigos de saida:
    0    rodada completa (ou nada a fazer)
    1    rodada abortada: disjuntor ou Chrome fora do ar
    2    erro de uso (argumento ou planilha invalida)
    3    sessao expirada: precisa de login antes de repetir o comando
    130  interrompida com Ctrl+C
"""
import argparse
import collections
import dataclasses
import datetime
import logging
import re
import sys
import time

import catalogo
import config
import ledger as ledger_mod
import legalone
import planilha
import relatorio

logger = logging.getLogger(__name__)

FORMATO_DATA = "%d/%m/%Y"     # como o Legal One espera a data da tarefa
FORMATO_DIA = "%Y-%m-%d"      # como o ledger guarda e como se nomeia a planilha
PASSO_PROGRESSO = 25          # de quantos em quantos processos sai o ETA

# Marca que um Salvar incerto deixa no detalhe do ledger, com a contagem de
# tarefas de antes do clique. Ver Rodada._salvar_anterior_gravou.
MARCA_ANTES_DO_SALVAR = "[tarefas antes do Salvar: {}]"
_RE_ANTES_DO_SALVAR = re.compile(r"\[tarefas antes do Salvar: (\d+)\]")

SAIDA_OK = 0
SAIDA_ABORTADA = 1
SAIDA_USO = 2
# Separada do 1 porque pede outra reacao: disjuntor e Chrome caido se resolvem
# repetindo o comando, sessao expirada so com alguem fazendo login. Um script
# que reinicia a rodada sozinho precisa distinguir sem ler o log.
SAIDA_SESSAO = 3
SAIDA_INTERROMPIDA = 130


class ErroDeUso(Exception):
    """Argumento ou planilha invalida — a rodada nem chega a comecar."""


# --- linha de comando --------------------------------------------------------

def inteiro_positivo(texto: str) -> int:
    """Valida --limite/--max-cadastros.

    Sem isto, `--max-cadastros 0` seria falso em Python e valeria como "sem
    cota": a rodada iria ate o fim da planilha em vez de parar imediatamente.
    """
    try:
        valor = int(texto)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{texto!r} nao e um numero inteiro")
    if valor < 1:
        raise argparse.ArgumentTypeError(f"precisa ser maior que zero (veio {valor})")
    return valor


def dia_iso(texto: str) -> str:
    """Valida --dia. O valor vira nome de arquivo e filtro do ledger."""
    try:
        return datetime.datetime.strptime(texto, FORMATO_DIA).date().isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"{texto!r} nao esta no formato AAAA-MM-DD")


def status_arg(texto: str) -> str:
    """Valida --status, aceitando sem acento e em qualquer caixa."""
    status = config.status_canonico(texto)
    if status is None:
        raise argparse.ArgumentTypeError(
            f"{texto!r} nao e status do Legal One "
            f"({', '.join(config.STATUS_VALIDOS)})"
        )
    return status


def argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Cadastra tarefas em lote no Legal One a partir de uma planilha.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""exemplos:
  # simulacao das 20 primeiras (nao grava nada)
  python main.py --planilha "C:/Users/Kamila/Downloads/Faturamento.xlsx" --limite 20

  # cota do dia: para depois de 500 tarefas cadastradas
  python main.py --planilha "C:/.../Faturamento.xlsx" --max-cadastros 500 --executar

  # o dia seguinte, na outra planilha, com a outra tarefa
  python main.py --planilha "C:/.../Defesa.xlsx" --tarefa defesa-faturada \\
      --max-cadastros 500 --executar

  # rodada real, planilha inteira, retomavel
  python main.py --planilha "C:/.../Faturamento.xlsx" --executar

  # so a aba 2026, cobrancas de encerramento
  python main.py --planilha "..." --abas 2026 --tipo-contem "ENCERRAMENTO" --executar

  # uma aba que mistura as duas tarefas: cada linha recebe a sua
  python main.py --planilha "Planilha de Faturamento.xlsx" \\
      --abas "2019-2020-2021" --tarefa auto --executar

  # uma tarefa que nao esta no tarefas.toml
  python main.py --planilha "..." --descricao "CONFERIR CUSTAS" \\
      --tipo "Diversos" --status Pendente --responsavel "Nathalia Maria Gatto Pinto"

  # um perfil do arquivo, com outro responsavel so nesta rodada
  python main.py --planilha "..." --tarefa defesa-faturada --responsavel "Nathalia"

  # a tarefa inteira vem das colunas "... DA TAREFA" de cada linha
  python main.py --planilha "Tarefas.xlsx" --tarefa planilha --executar

  # refazer os relatorios, ou a planilha de um dia especifico
  python main.py --relatorio
  python main.py --relatorio --dia 2026-07-30
""",
    )
    p.add_argument("--planilha", help="caminho do .xlsx de cobrancas")
    p.add_argument("--tarefa",
                   # Com o tarefas.toml quebrado nao ha lista de perfis, e o
                   # argparse responderia so "invalid choice". Sem a lista, a
                   # rodada chega a mostrar o que esta errado no arquivo.
                   choices=None if config.ERRO_PERFIS else sorted(
                       [*config.PERFIS, config.NOME_AUTO, config.NOME_PLANILHA]),
                   help=f"perfil do {config.ARQUIVO_PERFIS.name} (padrao: "
                        f"{config.PERFIL_PADRAO}). "
                        + " | ".join(f"{n} = {p.descricao!r}"
                                     for n, p in sorted(config.PERFIS.items()))
                        + f" | {config.NOME_AUTO} = a tarefa de cada linha vem da "
                          f"coluna {config.COLUNA_TIPO_COBRANCA!r}"
                        + f" | {config.NOME_PLANILHA} = a tarefa inteira vem das "
                          f"colunas {config.COLUNA_DESCRICAO_TAREFA!r}, "
                          f"{config.COLUNA_TIPO_TAREFA!r}, "
                          f"{config.COLUNA_STATUS_TAREFA!r} e "
                          f"{config.COLUNA_RESPONSAVEL_TAREFA!r}")
    valores = p.add_argument_group(
        "valores da tarefa",
        "Sobrepoem os do perfil nesta rodada. Com --tarefa planilha, valem para "
        "as linhas com a coluna vazia. --descricao cadastra uma tarefa fora do "
        "tarefas.toml, e ai --status e --responsavel sao obrigatorios.")
    valores.add_argument("--descricao", help="descricao de uma tarefa avulsa")
    valores.add_argument("--tipo",
                         help='caminho na arvore de tipos: "Diversos" ou '
                              '"Diversos / Contato Telefônico"')
    valores.add_argument("--status", type=status_arg,
                         help=", ".join(config.STATUS_VALIDOS))
    valores.add_argument("--responsavel", help="usuario ativo do Legal One")
    p.add_argument("--forcar-planilha", action="store_true",
                   help="ignora a trava que confere se a planilha combina com --tarefa")
    p.add_argument("--abas", nargs="+", help="abas a considerar (padrao: todas)")
    p.add_argument("--tipo-contem", help="filtra TIPO DE COBRANCA por substring")
    p.add_argument("--status-planilha", help="filtra STATUS LEGAL ONE exato (ex.: Ativo)")
    p.add_argument("--limite", type=inteiro_positivo,
                   help="examina no maximo N processos (inclui os nao encontrados)")
    p.add_argument("--max-cadastros", type=inteiro_positivo, metavar="N",
                   help="para depois de N tarefas efetivamente cadastradas — "
                        "use este para uma cota diaria de cadastros")
    p.add_argument("--processo", nargs="+", metavar="CNJ",
                   help="roda so estes numeros (util para testar ou refazer um caso)")
    p.add_argument("--data", help="data da tarefa DD/MM/AAAA (padrao: hoje)")
    p.add_argument("--executar", action="store_true",
                   help="grava de verdade (sem isso, apenas simula)")
    p.add_argument("--retentar", action="store_true",
                   help="tenta de novo os que deram erro/nao encontrado")
    p.add_argument("--rapido", action="store_true",
                   help="pula a checagem de tarefa duplicada "
                        "(1 pagina a menos por processo; o relatorio deixa de "
                        "distinguir cadastro novo de recadastro)")
    p.add_argument("--pular-existentes", action="store_true",
                   help="nao cadastra onde a tarefa ja existe (por padrao ela e "
                        "cadastrada de novo e marcada como recadastrada)")
    p.add_argument("--so-buscar", action="store_true",
                   help="pre-voo: so procura os processos e relata quais nao existem, "
                        "sem abrir formulario (bem mais rapido)")
    p.add_argument("--relatorio", action="store_true",
                   help="so exporta os relatorios do que ja rodou e sai")
    p.add_argument("--dia", metavar="AAAA-MM-DD", type=dia_iso,
                   help="com --relatorio, refaz a planilha de um dia especifico")
    return p.parse_args(argv)


# --- utilidades --------------------------------------------------------------

def _formatar_tempo(segundos: float) -> str:
    segundos = int(segundos)
    h, resto = divmod(segundos, 3600)
    m, s = divmod(resto, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def _gerar_planilha_do_dia(registro: ledger_mod.Ledger, dia: str) -> int:
    linhas = registro.cadastrados_em(dia)
    return relatorio.gerar_planilha_do_dia(
        config.planilha_do_dia(dia), dia, linhas
    )


def _tentar(oque: str, funcao, *args):
    """Executa uma exportacao isolando a falha dela.

    Isto roda no `finally` da rodada. Um relatorio.csv aberto no Excel devolve
    PermissionError no Windows, e sem isolar cada gravacao essa falha levaria
    junto a lista de conferencia, a planilha do dia e o resumo — depois de
    horas de execucao.
    """
    try:
        return funcao(*args)
    except Exception as e:
        logger.error("Nao consegui gravar %s: %s: %s", oque, type(e).__name__, e)
        return None


def _exportar_finais(registro: ledger_mod.Ledger, *, acumulada: bool = True,
                     nao_encontrados=(), dias=()) -> tuple[str, int]:
    """Grava tudo que a rodada deixa em disco. Nunca levanta excecao.

    Devolve (arquivo de conferencia, quantos processos ele tem).
    """
    _tentar("o relatorio", registro.exportar_csv, config.RELATORIO_CSV)

    if acumulada:
        # Sai do ledger, entao acumula o que as rodadas anteriores tambem
        # levantaram. Uma simulacao so enxerga a propria fila e por isso
        # escreve noutro arquivo, sem encostar na lista de verdade.
        conferencia = config.NAO_ENCONTRADOS_CSV
        pendentes = _tentar("a lista de conferencia",
                            registro.exportar_nao_encontrados, conferencia)
    else:
        conferencia = config.NAO_ENCONTRADOS_SIMULACAO_CSV
        pendentes = _tentar("a lista de conferencia da simulacao",
                            ledger_mod.escrever_nao_encontrados,
                            conferencia, nao_encontrados)

    # Uma planilha por dia tocado: rodada que atravessa a meia-noite gera as
    # duas, cada uma so com o que foi cadastrado naquele dia.
    for dia in dias:
        _tentar(f"a planilha de {dia}", _gerar_planilha_do_dia, registro, dia)

    return conferencia, pendentes or 0


# --- a rodada ----------------------------------------------------------------

class Rodada:
    """O laco principal: percorre a fila e cadastra a tarefa em cada processo.

    A regra de ouro mora aqui: um processo com problema vira 'erro' e a fila
    continua. So tres coisas interrompem o lote — a cota do dia, a sessao
    expirada e o disjuntor de "nao encontrados" seguidos.

    A tarefa e por processo, nao por rodada: quem traz a sua propria (modo auto)
    manda, e `perfil` fica so como padrao para quem vem sem nenhuma.

    Nao conhece argparse nem Selenium concreto: recebe um automador pronto,
    o que permite exercitar disjuntor, cota e placar sem abrir o Chrome.
    """

    def __init__(self, automador, registro: ledger_mod.Ledger,
                 perfil: config.PerfilTarefa, *, executar: bool = False,
                 so_buscar: bool = False, rapido: bool = False,
                 pular_existentes: bool = False,
                 max_cadastros: int | None = None,
                 data_fixa: str | None = None,
                 resolucao: catalogo.Resolucao | None = None):
        self.automador = automador
        self.registro = registro
        self.perfil = perfil
        # --data. Sem ela, a data e a de hoje no instante de cada cadastro.
        self.data_fixa = data_fixa
        # Tipo e responsavel conferidos no Legal One (ver _resolver_perfis).
        # Valor fora dela vai como esta, e o formulario so confere o tipo que
        # ja vem nele.
        self.resolucao = resolucao or catalogo.Resolucao()
        # As descricoes da fila, para o acumulado do resumo.
        self.descricoes: list[str] = [perfil.descricao]
        self.executar = executar
        self.so_buscar = so_buscar
        self.rapido = rapido
        self.pular_existentes = pular_existentes
        self.max_cadastros = max_cadastros

        self.contagem = {"ok": 0, "recadastrada": 0, "erro": 0,
                         "nao_encontrado": 0, "ja_existia": 0}
        self.nao_encontrados: list[dict] = []
        # Dias em que esta rodada cadastrou alguma coisa. E um conjunto porque
        # uma rodada longa atravessa a meia-noite, e cada dia tem a sua planilha.
        self.dias_cadastrados: set[str] = set()
        self.cadastradas = 0
        # Pares (processo, tarefa): numa rodada auto o mesmo numero pode estar
        # na fila com as duas tarefas, e so uma delas pode ser suspeita.
        self.trilha_sem_achar: list[tuple[str, str]] = []
        self.disjuntor = False
        self.disjuntor_nao_encontrados = False
        self.disjuntor_erros = False
        self.erros_seguidos = 0
        self.cota_atingida = False
        self.interrompida = False
        self.sessao_expirada: legalone.SessaoExpirada | None = None
        self._busca: legalone.ResultadoBusca | None = None
        # Tarefas iguais que o processo tinha antes do Salvar; None quando nao
        # houve contagem (--rapido) ou o processo nao chegou la.
        self._antes: int | None = None
        # A tarefa enviada ao formulario do processo atual; None enquanto o
        # processo nao chegou ao cadastro. E o que o ledger grava como tipo,
        # status, responsavel e datas.
        self._tarefa: config.Tarefa | None = None

    # --- laco ----------------------------------------------------------------

    def executar_fila(self, fila: list[planilha.Processo]) -> None:
        total = len(fila)
        self.descricoes = sorted({self._perfil_de(p).descricao for p in fila}) \
            or self.descricoes
        inicio = time.monotonic()

        try:
            for i, proc in enumerate(fila, 1):
                self._busca = None
                self._antes = None
                self._tarefa = None
                try:
                    if not self._um_processo(proc, i, total):
                        break
                    self.erros_seguidos = 0
                except legalone.SessaoExpirada:
                    raise
                except Exception as e:
                    detalhe = f"{type(e).__name__}: {e}"[:400]
                    if (isinstance(e, legalone.SalvarIncerto)
                            and self._antes is not None):
                        detalhe = (detalhe[:350] + " "
                                   + MARCA_ANTES_DO_SALVAR.format(self._antes))
                    self._anotar(
                        proc, ledger_mod.ERRO,
                        # Se a busca chegou a achar o processo, guarda o id: e
                        # por ele que se abre o caso a mao depois.
                        id_legalone=(self._busca.id_legalone
                                     if self._busca and self._busca.encontrado
                                     else ""),
                        detalhe=detalhe,
                    )
                    self.contagem["erro"] += 1
                    logger.error("[%d/%d] %s — ERRO: %s: %s",
                                 i, total, proc.cnj, type(e).__name__, e)
                    self.erros_seguidos += 1
                    self.trilha_sem_achar.clear()
                    if self.erros_seguidos >= config.MAX_ERROS_SEGUIDOS:
                        self.disjuntor = True
                        self.disjuntor_erros = True
                        logger.error(
                            "PARADO: %d falhas consecutivas. O Chrome ou o "
                            "Legal One pode estar fora do ar; confira a "
                            "conexao e rode novamente com --retentar.",
                            config.MAX_ERROS_SEGUIDOS,
                        )
                        break
                finally:
                    # Progresso e pausa valem para todo processo, inclusive os
                    # que sairam cedo — senao o ETA some justo nas rodadas
                    # cheias de nao-encontrado.
                    self._progresso(i, total, inicio)
                    time.sleep(config.PAUSA_ENTRE_PROCESSOS)

        except KeyboardInterrupt:
            self.interrompida = True
            logger.warning("Interrompido pelo usuario — progresso salvo no ledger.")
        except legalone.SessaoExpirada as e:
            self.sessao_expirada = e
            logger.error("SESSAO EXPIRADA: %s", e)

        if self.cota_atingida:
            logger.info("Cota do dia atingida: %d tarefa(s) cadastrada(s). "
                        "O restante da fila fica para a proxima rodada.",
                        self.cadastradas)
        if self.disjuntor_nao_encontrados:
            self._descartar_suspeitos()

    def _perfil_de(self, proc: planilha.Processo) -> config.PerfilTarefa:
        """Perfil da tarefa deste processo, com tipo e responsavel conferidos.

        Vem do proprio processo (a preparacao da rodada atribui um a cada um);
        na falta dele, da descricao da linha ou do perfil da rodada.
        """
        perfil = proc.perfil or config.PERFIS_POR_DESCRICAO.get(
            proc.tarefa, self.perfil)
        return self.resolucao.aplicar(perfil)

    def _tarefa_de(self, proc: planilha.Processo) -> config.Tarefa:
        """A tarefa a enviar para este processo, com a data resolvida agora.

        Chamada logo antes do formulario, e nao no inicio da rodada: sem --data,
        uma rodada que atravessa a meia-noite tem que mandar a data do dia novo.
        """
        data = self.data_fixa or datetime.date.today().strftime(FORMATO_DATA)
        # Com --data, a data foi escolhida: se for passada (ou virar passada na
        # meia-noite), o aviso do Legal One e confirmado. Sem --data, o aviso so
        # aparece num processo pego pela virada, e ai ele vira erro.
        return config.Tarefa.do_perfil(
            self._perfil_de(proc), data,
            confirmar_data_passada=self.data_fixa is not None,
        )

    def _um_processo(self, proc: planilha.Processo, i: int, total: int) -> bool:
        """Trata um processo. Devolve False quando a rodada tem que parar."""
        if not self.automador.aba_viva():
            logger.warning("A aba de trabalho sumiu (fechada?) — recriando")
            self.automador.usar_aba_propria()

        perfil = self._perfil_de(proc)
        self._busca = busca = self.automador.buscar_processo(proc.cnj)
        if not busca.encontrado:
            return self._nao_encontrado(proc, busca, i, total)

        self.trilha_sem_achar.clear()

        if self.so_buscar:
            self.contagem["ok"] += 1
            logger.info("[%d/%d] %s -> id %s (%s)",
                        i, total, proc.cnj, busca.id_legalone, busca.status)
            return True

        # Lido antes de qualquer _anotar, que sobrescreve o detalhe.
        detalhe_anterior = self.registro.detalhe(proc.cnj, perfil.descricao)

        # A checagem nao decide mais se cadastra — decide o que registrar. A
        # orientacao de operacao para a tarefa que ja existe e cadastrar de novo
        # ("pecar pelo excesso"), e o valor da checagem virou saber quais foram.
        if not self.rapido:
            self._antes = self.automador.contar_tarefas(
                busca.id_legalone, perfil.descricao
            )
        ja_existia = bool(self._antes)

        if self._salvar_anterior_gravou(detalhe_anterior):
            self._anotar(proc, ledger_mod.OK, busca.id_legalone,
                         "cadastrada (o Salvar da tentativa anterior tinha "
                         "gravado; conferido pela contagem)")
            return self._contar_cadastro(
                proc, busca, perfil, i, total, ledger_mod.OK,
                "ja estava cadastrada pela tentativa anterior — nao cadastrei de novo",
            )

        if ja_existia and self.pular_existentes:
            self._anotar(proc, ledger_mod.JA_EXISTIA, busca.id_legalone,
                         "processo ja tinha a tarefa")
            self.contagem["ja_existia"] += 1
            logger.info("[%d/%d] %s — ja tinha %r, pulando",
                        i, total, proc.cnj, perfil.descricao)
            return True

        # O cadastro no Legal One e a gravacao local nao formam uma transacao:
        # se o usuario der Ctrl+C depois do clique e antes do retorno, o
        # processo precisa continuar visivel no ledger para ser retomado com
        # --retentar, em vez de desaparecer da fila como se nada tivesse sido
        # tentado.
        # Um Ctrl+C entre o clique e o retorno deixa esta marca no ledger, e a
        # retentativa confere a contagem do mesmo jeito que num SalvarIncerto.
        andamento = "cadastro em andamento"
        if self._antes is not None:
            andamento += " " + MARCA_ANTES_DO_SALVAR.format(self._antes)
        self._tarefa = tarefa = self._tarefa_de(proc)
        self._anotar(proc, ledger_mod.ERRO, busca.id_legalone, andamento)
        resultado = self.automador.cadastrar_tarefa(
            busca.id_legalone, self.executar, tarefa
        )
        situacao = ledger_mod.RECADASTRADA if ja_existia else ledger_mod.OK
        detalhe = f"{resultado} (ja tinha a tarefa)" if ja_existia else resultado
        self._anotar(proc, situacao, busca.id_legalone, detalhe)
        return self._contar_cadastro(proc, busca, perfil, i, total, situacao, detalhe)

    def _salvar_anterior_gravou(self, detalhe_anterior: str) -> bool:
        """A tentativa anterior clicou Salvar sem confirmar, e a tarefa esta la?

        Sem esta conferencia o --retentar recadastra, e o processo fica com a
        tarefa em dobro — foi o que aconteceu em 15/09/2026. A prova e a
        contagem ter subido desde antes daquele clique: so "ja existe" nao
        basta, porque boa parte dos processos ja tinha a tarefa de anos
        anteriores. Registro antigo, sem a marca, segue o caminho de sempre.
        """
        marca = _RE_ANTES_DO_SALVAR.search(detalhe_anterior)
        return (marca is not None and self._antes is not None
                and self._antes > int(marca.group(1)))

    def _contar_cadastro(self, proc: planilha.Processo,
                         busca: legalone.ResultadoBusca,
                         perfil: config.PerfilTarefa, i: int, total: int,
                         situacao: str, detalhe: str) -> bool:
        """Placar, cota e log de um processo que terminou com a tarefa gravada."""
        self.contagem[situacao] += 1
        # Recadastro tambem cria tarefa no Legal One, entao consome a cota do
        # dia. O Salvar confirmado na retentativa tambem conta: a tarefa nunca
        # tinha entrado no placar, e o ledger passa a dizer 'ok' hoje.
        self.cadastradas += 1
        if self.executar:
            self.dias_cadastrados.add(datetime.date.today().isoformat())
        logger.info("[%d/%d] %s -> id %s (%s) %r %s",
                    i, total, proc.cnj, busca.id_legalone, busca.status,
                    perfil.descricao, detalhe)

        if self.max_cadastros and self.cadastradas >= self.max_cadastros:
            self.cota_atingida = True
            return False
        return True

    def _nao_encontrado(self, proc: planilha.Processo,
                        busca: legalone.ResultadoBusca, i: int, total: int) -> bool:
        detalhe = busca.detalhe
        if not proc.formato_ok:
            detalhe = f"{detalhe} (numero fora do padrao CNJ)"
        situacao = ledger_mod.AMBIGUO if busca.ambiguo else ledger_mod.NAO_ENCONTRADO
        tarefa = self._perfil_de(proc).descricao

        self._anotar(proc, situacao, detalhe=detalhe)
        self.nao_encontrados.append({
            "cnj": proc.cnj_original,
            # O numero de fato pesquisado; difere do original quando a planilha
            # traz ponto no lugar do primeiro hifen.
            "cnj_busca": proc.cnj,
            "tarefa": tarefa,
            "situacao": situacao,
            "motivo": detalhe,
            "tipo_cobranca": "; ".join(proc.tipos_cobranca),
            "status_planilha": "; ".join(proc.status_planilha),
            "origem": proc.origem,
        })
        self.contagem["nao_encontrado"] += 1
        self.trilha_sem_achar.append((proc.cnj, tarefa))
        logger.warning("[%d/%d] %s — %s", i, total, proc.cnj, detalhe)

        if len(self.trilha_sem_achar) >= config.MAX_NAO_ENCONTRADOS_SEGUIDOS:
            self.disjuntor = True
            self.disjuntor_nao_encontrados = True
            return False
        return True

    # --- estado --------------------------------------------------------------

    def _anotar(self, proc: planilha.Processo, situacao: str,
                id_legalone: str = "", detalhe: str = "") -> None:
        """Grava no ledger apenas em rodada real — simulacao nao deixa rastro.

        Todo registro leva junto a origem na planilha: e o que preenche as
        colunas do relatorio e da planilha do dia, inclusive quando o processo
        termina em erro. Os campos da tarefa so vao quando o processo chegou ao
        formulario; antes disso ficam vazios, e o ledger mantem o que ja tinha.
        """
        if not self.executar:
            return
        tarefa = self._tarefa
        self.registro.registrar(
            proc.cnj, self._perfil_de(proc).descricao, situacao,
            id_legalone=id_legalone,
            detalhe=detalhe,
            origem=proc.origem,
            tipo_cobranca="; ".join(proc.tipos_cobranca),
            status_planilha="; ".join(proc.status_planilha),
            # Numero como estava escrito na planilha: e por ele que se acha a
            # linha de origem quando o processo cai na conferencia manual.
            cnj_original=proc.cnj_original,
            tipo=tarefa.tipo if tarefa else "",
            status=tarefa.status if tarefa else "",
            responsavel=tarefa.responsavel if tarefa else "",
            data_inicio=tarefa.inicio if tarefa else "",
            data_fim=tarefa.fim if tarefa else "",
        )

    def _descartar_suspeitos(self) -> None:
        """Tira do ledger os "nao encontrado" que sao efeito da sessao caida.

        Deixa-los gravados faria a retomada pular justamente os processos que
        nunca chegaram a ser avaliados de verdade.
        """
        por_tarefa: dict[str, list[str]] = {}
        for cnj, tarefa in self.trilha_sem_achar:
            por_tarefa.setdefault(tarefa, []).append(cnj)
        apagados = (sum(self.registro.esquecer(cnjs, tarefa)
                        for tarefa, cnjs in por_tarefa.items())
                    if self.executar else 0)
        self.contagem["nao_encontrado"] -= len(self.trilha_sem_achar)
        # Tambem saem da lista de conferencia: nao sao casos para o usuario
        # investigar, sao efeito colateral da queda.
        suspeitos = set(self.trilha_sem_achar)
        self.nao_encontrados[:] = [
            n for n in self.nao_encontrados
            if (n["cnj_busca"], n["tarefa"]) not in suspeitos
        ]
        logger.error(
            "PARADO: %d processos seguidos sem ser encontrados. Numa rodada "
            "normal isso nao acontece por acaso — provavelmente a sessao do "
            "Legal One caiu. Confira o Chrome, refaca o login e rode o mesmo "
            "comando de novo.",
            config.MAX_NAO_ENCONTRADOS_SEGUIDOS,
        )
        if apagados:
            logger.error("Descartei do ledger os %d registros suspeitos — "
                         "eles voltam para a fila na proxima rodada.", apagados)

    def _progresso(self, i: int, total: int, inicio: float) -> None:
        if i % PASSO_PROGRESSO and i != total:
            return
        decorrido = time.monotonic() - inicio
        por_item = decorrido / i
        logger.info(
            "  ... %d/%d | %.1fs/processo | decorrido %s | falta ~%s | %s",
            i, total, por_item, _formatar_tempo(decorrido),
            _formatar_tempo(por_item * (total - i)), self.contagem,
        )

    def dias_de_planilha(self) -> list[str]:
        """Dias cujo .xlsx a rodada deve (re)gerar."""
        return sorted(self.dias_cadastrados)

    def codigo_saida(self) -> int:
        if self.interrompida:
            return SAIDA_INTERROMPIDA
        if self.sessao_expirada:
            return SAIDA_SESSAO
        if self.disjuntor:
            return SAIDA_ABORTADA
        return SAIDA_OK

    def resumir(self, conferencia: str, pendentes: int) -> None:
        logger.info("Resumo desta rodada: %s", self.contagem)
        for descricao in self.descricoes:
            logger.info("Acumulado em %-18s %s", descricao + ":",
                        dict(self.registro.resumo(descricao)))
        logger.info("Acumulado geral:     %s", dict(self.registro.resumo()))
        logger.info("Relatorio:            %s", config.RELATORIO_CSV)
        logger.info("Para conferir a mao:  %s (%d processo[s])",
                    conferencia, pendentes)
        for dia in self.dias_de_planilha():
            logger.info("Planilha do dia:      %s", config.planilha_do_dia(dia))
        if not self.executar:
            logger.info("Foi SIMULACAO — nada foi gravado no Legal One.")
        elif not self.dias_cadastrados:
            logger.info("Nenhuma tarefa nova cadastrada — sem planilha do dia.")


# --- preparacao da rodada ----------------------------------------------------

def _perfil_da_rodada(args: argparse.Namespace) -> config.PerfilTarefa:
    """O perfil que a linha de comando pede, com as flags por cima.

    Nos modos auto e planilha e um marcador: cada processo recebe o seu em
    _atribuir_perfis.
    """
    if args.descricao:
        if args.tarefa:
            raise ErroDeUso(
                "--descricao ja define a tarefa; nao combine com --tarefa. Para "
                "mudar um valor de um perfil, use --tipo/--status/--responsavel "
                "com --tarefa."
            )
        faltando = [f for f, v in (("--status", args.status),
                                   ("--responsavel", args.responsavel)) if not v]
        if faltando:
            # Sem padrao escondido: uma tarefa nova que caisse em Cumprido e no
            # nome de quem ninguem escolheu passaria por certa.
            raise ErroDeUso(f"Tarefa avulsa (--descricao) precisa de "
                            f"{' e '.join(faltando)}.")
        return config.PerfilTarefa(
            config.NOME_AVULSA, " ".join(args.descricao.split()),
            tipo=args.tipo or "Diversos", status=args.status,
            responsavel=args.responsavel,
        )
    nome = args.tarefa or config.PERFIL_PADRAO
    if nome is None:
        raise ErroDeUso(f"Falta --tarefa (o {config.ARQUIVO_PERFIS.name} nao tem "
                        f"o perfil padrao) ou --descricao.")
    if nome == config.NOME_AUTO:
        return config.PERFIL_AUTO
    if nome == config.NOME_PLANILHA:
        return config.PERFIL_PLANILHA
    return _com_flags(config.PERFIS[nome], args)


def _com_flags(perfil: config.PerfilTarefa,
               args: argparse.Namespace) -> config.PerfilTarefa:
    """O perfil com --tipo, --status e --responsavel por cima, se dados."""
    trocas = {campo: valor for campo, valor in (
        ("tipo", args.tipo), ("status", args.status),
        ("responsavel", args.responsavel)) if valor}
    return dataclasses.replace(perfil, **trocas) if trocas else perfil


def _perfil_da_linha(proc: planilha.Processo,
                     args: argparse.Namespace) -> tuple[config.PerfilTarefa | None, str]:
    """O perfil de uma linha do modo planilha: linha > flag > tarefas.toml.

    Se a descricao da linha e a de um perfil do arquivo, o perfil completa o
    que a linha e as flags nao disserem, e a descricao passa a ser escrita como
    no perfil — o ledger e a checagem de duplicata comparam por ela. Devolve
    (perfil, "") ou (None, motivo).
    """
    base = config.PERFIS_POR_DESCRICAO.get(
        config.tarefa_do_tipo(proc.tarefa) or "")
    campos = proc.campos_tarefa

    def valor(campo: str) -> str:
        return (campos.get(campo) or getattr(args, campo)
                or (getattr(base, campo) if base else ""))

    status_bruto = valor("status")
    status = config.status_canonico(status_bruto) if status_bruto else None
    responsavel = valor("responsavel")
    faltando = [c for c, v in (("status", status_bruto),
                               ("responsavel", responsavel)) if not v]
    if faltando:
        return None, f"sem {' e '.join(faltando)}"
    if status is None:
        return None, f"status {status_bruto!r} nao existe"
    return config.PerfilTarefa(
        base.nome if base else config.NOME_PLANILHA,
        base.descricao if base else proc.tarefa,
        tipo=valor("tipo") or "Diversos", status=status,
        responsavel=responsavel,
    ), ""


def _atribuir_perfis(args: argparse.Namespace, perfil: config.PerfilTarefa,
                     processos: list[planilha.Processo]) -> None:
    """Da a cada processo o perfil completo da sua tarefa.

    Depois daqui a rodada nao precisa saber de onde a tarefa veio: perfil do
    arquivo, flags, coluna de cobranca ou colunas da tarefa. Linha do modo
    planilha que nao da uma tarefa completa e erro de uso, com as linhas.
    """
    problemas: list[str] = []
    for proc in processos:
        if perfil.nome == config.NOME_AUTO:
            proc.perfil = _com_flags(config.PERFIS_POR_DESCRICAO[proc.tarefa], args)
        elif perfil.nome == config.NOME_PLANILHA:
            proc.perfil, motivo = _perfil_da_linha(proc, args)
            if proc.perfil is None:
                problemas.append(f"{proc.origem} ({proc.cnj_original}): {motivo}")
                continue
            proc.tarefa = proc.perfil.descricao
        else:
            proc.perfil = perfil
    if problemas:
        raise ErroDeUso(
            "Linha(s) sem tarefa completa (nem na coluna, nem nas flags, nem no "
            f"{config.ARQUIVO_PERFIS.name}):\n  " + "\n  ".join(problemas[:20])
            + (f"\n  (e mais {len(problemas) - 20})" if len(problemas) > 20 else "")
        )


def _perfis_distintos(processos: list[planilha.Processo]) -> list[config.PerfilTarefa]:
    return list(dict.fromkeys(p.perfil for p in processos if p.perfil))


def _conferir_planilha(args: argparse.Namespace, perfil: config.PerfilTarefa) -> None:
    """Trava contra rodar a planilha de uma tarefa com o perfil da outra.

    Parear errado criaria centenas de tarefas indevidas, e desfazer isso e
    trabalho manual.
    """
    if not perfil.dica_arquivo or args.forcar_planilha:
        return
    if perfil.dica_arquivo.lower() in str(args.planilha).lower():
        return
    raise ErroDeUso(
        f"A planilha nao parece ser a de {perfil.descricao!r}: esperava "
        f"{perfil.dica_arquivo!r} no caminho, e veio {args.planilha!r}. "
        f"Confira o par planilha/--tarefa, ou use --forcar-planilha se "
        f"estiver certo."
    )


def _data_da_tarefa(args: argparse.Namespace) -> str:
    data = args.data or datetime.date.today().strftime(FORMATO_DATA)
    try:
        datetime.datetime.strptime(data, FORMATO_DATA)
    except ValueError:
        raise ErroDeUso(f"Data invalida: {data!r} (esperado DD/MM/AAAA)")
    return data


def _conferir_data_passada(args: argparse.Namespace,
                           perfis: list[config.PerfilTarefa]) -> None:
    """Recusa, antes de abrir o Chrome, a data passada que o Legal One barra.

    Sem isto cada processo da fila viraria erro com a mesma mensagem, ate o
    disjuntor parar a rodada. Data passada com status aceito so gera um aviso:
    o Legal One pede confirmacao, e a rodada confirma (ver Tarefa).
    """
    if not args.data:
        return
    data = datetime.datetime.strptime(args.data, FORMATO_DATA).date()
    if data >= datetime.date.today():
        return
    recusados = sorted({p.status for p in perfis}
                       & config.STATUS_RECUSADOS_NO_PASSADO)
    if recusados:
        raise ErroDeUso(
            f"--data {args.data} e anterior a hoje, e o Legal One nao aceita "
            f"tarefa {', '.join(recusados)} com data passada."
        )
    logger.warning("--data %s e anterior a hoje: o Legal One pede confirmacao "
                   "e ela sera dada em cada cadastro.", args.data)


def _resolver_perfis(automador, perfis: list[config.PerfilTarefa]
                     ) -> catalogo.Resolucao:
    """Confere no Legal One cada tipo e cada responsavel pedidos na rodada.

    Roda uma vez, antes do primeiro cadastro: um tipo que nao existe ou um
    responsavel ambiguo viraria o mesmo erro em cada processo da fila, e um
    casamento errado criaria centenas de tarefas no lugar errado. Cada valor
    distinto e conferido uma vez so, venha de quantos perfis vier. Qualquer
    problema junta tudo numa mensagem so e vira ErroDeUso.
    """
    def onde(campo: str, valor: str) -> str:
        return ", ".join(sorted({repr(p.descricao) for p in perfis
                                 if getattr(p, campo) == valor}))

    resolucao = catalogo.Resolucao()
    problemas: list[str] = []
    tipos = automador.listar_tipos()
    for pedido in dict.fromkeys(p.tipo for p in perfis):
        try:
            tipo = resolucao.tipos[pedido] = catalogo.resolver_tipo(pedido, tipos)
            logger.info("Conferido:   tipo %r -> %r (%s)", pedido, tipo.caminho,
                        tipo.id)
        except catalogo.NaoResolvido as e:
            problemas.append(f"{e} [em {onde('tipo', pedido)}]")
    for pedido in dict.fromkeys(p.responsavel for p in perfis):
        try:
            nome = resolucao.responsaveis[pedido] = catalogo.resolver_usuario(
                pedido, automador.buscar_usuarios(pedido))
            logger.info("Conferido:   responsavel %r -> %r", pedido, nome)
        except catalogo.NaoResolvido as e:
            problemas.append(f"{e} [em {onde('responsavel', pedido)}]")
    if problemas:
        raise ErroDeUso("Tarefa que nao da para cadastrar no Legal One:\n  "
                        + "\n  ".join(problemas))
    return resolucao


def _montar_fila(args: argparse.Namespace, registro: ledger_mod.Ledger,
                 perfil: config.PerfilTarefa, processos: list[planilha.Processo]
                 ) -> tuple[list[planilha.Processo], list[planilha.Processo],
                            set[tuple[str, str]]]:
    """Aplica ledger, --processo e --limite. Devolve (processos, fila, pular).

    O que ja foi feito e um par (processo, tarefa), nunca so o processo: um
    numero que ja recebeu a defesa continua devendo o faturamento final.
    """
    # Uma simulacao nao deixa rastro no ledger; pular o que ja esta la faria a
    # simulacao mentir sobre o tamanho da rodada real seguinte.
    if not args.executar:
        pular: set[tuple[str, str]] = set()
    else:
        feitos = registro.concluidos if args.retentar else registro.todos
        # As descricoes que esta planilha de fato pede — no modo planilha elas
        # nem existem num perfil.
        descricoes = {p.tarefa or perfil.descricao for p in processos}
        pular = {(cnj, descricao)
                 for descricao in descricoes
                 for cnj in feitos(descricao)}

    if args.processo:
        # Normaliza igual a planilha, para o numero digitado casar.
        alvo = {planilha.normalizar(n)[0] for n in args.processo}
        processos = [p for p in processos if p.cnj in alvo]
        faltando = alvo - {p.cnj for p in processos}
        if faltando:
            logger.warning("Nao esta(o) na planilha: %s", ", ".join(sorted(faltando)))
        pular = set()

    # Processo sem tarefa propria e o caso de sempre: a tarefa da rodada inteira.
    fila = [p for p in processos
            if (p.cnj, p.tarefa or perfil.descricao) not in pular]
    if args.limite:
        fila = fila[: args.limite]
    return processos, fila, pular


def _log_cabecalho(args: argparse.Namespace, perfil: config.PerfilTarefa,
                   data_tarefa: str, processos: list, fila: list,
                   pular: set[tuple[str, str]]) -> None:
    logger.info("Perfil:      %s", perfil.nome)
    if perfil.nome == config.NOME_AUTO:
        logger.info("Tarefa:      de cada linha, pela coluna %r",
                    config.COLUNA_TIPO_COBRANCA)
    elif perfil.nome == config.NOME_PLANILHA:
        logger.info("Tarefa:      de cada linha, pelas colunas %r e seguintes",
                    config.COLUNA_DESCRICAO_TAREFA)
    # Cada combinacao distinta, com quantos processos a pedem: e aqui que se ve,
    # antes de --executar, uma coluna de responsavel trocada ou um status
    # errado na planilha inteira.
    combinacoes = collections.Counter(p.perfil for p in processos if p.perfil)
    for combinacao, quantos in combinacoes.most_common():
        logger.info("Tarefa:      %r / tipo %r / status %r / responsavel %r: "
                    "%d processo(s)", combinacao.descricao, combinacao.tipo,
                    combinacao.status, combinacao.responsavel, quantos)
    if not combinacoes:
        logger.info("Tarefa:      %r / tipo %r / status %r / responsavel %r",
                    perfil.descricao, perfil.tipo, perfil.status,
                    perfil.responsavel)
    logger.info("Data:        %s", data_tarefa)
    logger.info("Planilha:    %s", args.planilha)
    logger.info("             %d processo(s) unico(s)", len(processos))
    logger.info("Ja no ledger (%s): %d — fila desta rodada: %d",
                perfil.nome, len(pular), len(fila))
    logger.info("MODO: %s", "EXECUCAO REAL (grava)" if args.executar
                else "SIMULACAO (nao grava — use --executar para valer)")


# --- modos -------------------------------------------------------------------

def _modo_relatorio(args: argparse.Namespace) -> int:
    with ledger_mod.Ledger(config.LEDGER_FILE) as registro:
        dias = [args.dia] if args.dia else registro.dias_com_cadastro()
        _exportar_finais(registro, dias=dias)
        if not dias:
            logger.info("Nenhum cadastro registrado ainda.")
        logger.info("Situacao atual: %s", dict(registro.resumo()))
    return SAIDA_OK


def _modo_rodada(args: argparse.Namespace) -> int:
    # As validacoes vem antes de abrir o ledger: nenhuma delas precisa dele, e
    # assim uma saida por erro de uso nao deixa banco aberto para tras.
    if not args.planilha:
        raise ErroDeUso("Falta --planilha (ou use --relatorio)")
    if args.so_buscar and args.executar:
        raise ErroDeUso(
            "--so-buscar nao cadastra nada; nao combine com --executar, senao o "
            "ledger marcaria como feito o que nunca foi cadastrado."
        )
    if args.pular_existentes and args.rapido:
        raise ErroDeUso(
            "--pular-existentes precisa da checagem de duplicata que "
            "--rapido desliga; escolha uma das duas."
        )
    if args.so_buscar and args.max_cadastros:
        logger.warning("--max-cadastros nao tem efeito com --so-buscar (nada e "
                       "cadastrado); para encurtar o pre-voo use --limite.")
    if args.dia:
        logger.warning("--dia so vale com --relatorio; ignorando.")

    if config.ERRO_PERFIS:
        raise ErroDeUso(f"Perfis com problema: {config.ERRO_PERFIS}")

    perfil = _perfil_da_rodada(args)
    _conferir_planilha(args, perfil)
    data_tarefa = _data_da_tarefa(args)

    try:
        processos = planilha.ler(
            args.planilha,
            abas=args.abas,
            tipo_contem=args.tipo_contem,
            status_planilha=args.status_planilha,
            tarefa_da_linha=(config.tarefa_do_tipo
                             if perfil.nome == config.NOME_AUTO else None),
            colunas_da_tarefa=perfil.nome == config.NOME_PLANILHA,
        )
    except (FileNotFoundError, ValueError) as e:
        raise ErroDeUso(str(e)) from e
    _atribuir_perfis(args, perfil, processos)
    _conferir_data_passada(args, _perfis_distintos(processos))

    with ledger_mod.Ledger(config.LEDGER_FILE) as registro:
        processos, fila, pular = _montar_fila(args, registro, perfil, processos)
        _log_cabecalho(args, perfil, data_tarefa, processos, fila, pular)

        if not fila:
            logger.info("Nada a fazer.")
            _tentar("o relatorio", registro.exportar_csv, config.RELATORIO_CSV)
            return SAIDA_OK

        try:
            driver = legalone.conectar()
        except Exception as e:
            logger.error("Nao consegui conectar ao Chrome em %s: %s",
                         config.DEBUG_ADDRESS, e)
            logger.error("Abra o Chrome com --remote-debugging-port=%d e logue "
                         "no Legal One.", config.DEBUG_PORT)
            return SAIDA_ABORTADA

        automador = legalone.AutomadorLegalOne(driver)
        automador.usar_aba_propria()

        # --so-buscar nao abre formulario: nao ha tipo nem responsavel a conferir.
        resolucao = catalogo.Resolucao()
        if not args.so_buscar:
            try:
                resolucao = _resolver_perfis(automador, _perfis_distintos(fila))
            except legalone.SessaoExpirada as e:
                logger.error("SESSAO EXPIRADA: %s", e)
                return SAIDA_SESSAO
            except ErroDeUso:
                raise
            except Exception as e:
                # Sem a lista do Legal One nao da para conferir nada; seguir sem
                # a checagem seria justamente o que ela existe para evitar.
                logger.error("Nao consegui conferir tipo e responsavel no Legal "
                             "One: %s: %s", type(e).__name__, e)
                return SAIDA_ABORTADA

        rodada = Rodada(
            automador, registro, perfil,
            executar=args.executar,
            so_buscar=args.so_buscar,
            rapido=args.rapido,
            pular_existentes=args.pular_existentes,
            max_cadastros=args.max_cadastros,
            data_fixa=args.data,
            resolucao=resolucao,
        )
        try:
            rodada.executar_fila(fila)
        finally:
            # Em qualquer saida — Ctrl+C, sessao caida ou erro fatal — os
            # relatorios saem antes de terminar.
            conferencia, pendentes = _exportar_finais(
                registro,
                acumulada=args.executar,
                nao_encontrados=rodada.nao_encontrados,
                dias=rodada.dias_de_planilha(),
            )
            rodada.resumir(conferencia, pendentes)
        return rodada.codigo_saida()


def main(argv: list[str] | None = None) -> int:
    args = argumentos(argv)

    logger.info("=" * 60)
    logger.info("%s v%s", config.PROJECT_NAME, config.VERSION)
    logger.info("=" * 60)

    try:
        if args.relatorio:
            return _modo_relatorio(args)
        return _modo_rodada(args)
    except ErroDeUso as e:
        logger.error("%s", e)
        return SAIDA_USO


if __name__ == "__main__":
    sys.exit(main())
