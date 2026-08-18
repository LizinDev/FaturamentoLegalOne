"""Cadastro em lote de tarefas no Legal One.

Le a planilha de cobrancas, encontra cada processo no Legal One e cadastra a
tarefa do perfil escolhido em --tarefa (FATURAMENTO FINAL ou DEFESA FATURADA),
ou, com --tarefa auto, a tarefa que a coluna TIPO DE COBRANCA indica em cada
linha. Por padrao roda em simulacao — precisa de --executar para gravar.

Codigos de saida:
    0    rodada completa (ou nada a fazer)
    1    rodada abortada: sessao expirada, disjuntor ou Chrome fora do ar
    2    erro de uso (argumento ou planilha invalida)
    130  interrompida com Ctrl+C
"""
import argparse
import datetime
import logging
import sys
import time

import config
import ledger as ledger_mod
import legalone
import planilha
import relatorio

logger = logging.getLogger(__name__)

FORMATO_DATA = "%d/%m/%Y"     # como o Legal One espera a data da tarefa
FORMATO_DIA = "%Y-%m-%d"      # como o ledger guarda e como se nomeia a planilha
PASSO_PROGRESSO = 25          # de quantos em quantos processos sai o ETA

SAIDA_OK = 0
SAIDA_ABORTADA = 1
SAIDA_USO = 2
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

  # refazer os relatorios, ou a planilha de um dia especifico
  python main.py --relatorio
  python main.py --relatorio --dia 2026-07-30
""",
    )
    p.add_argument("--planilha", help="caminho do .xlsx de cobrancas")
    p.add_argument("--tarefa",
                   choices=sorted([*config.PERFIS, config.NOME_AUTO]),
                   default=config.PERFIL_PADRAO,
                   help=f"qual tarefa cadastrar (padrao: {config.PERFIL_PADRAO}). "
                        + " | ".join(f"{n} = {p.descricao!r}"
                                     for n, p in sorted(config.PERFIS.items()))
                        + f" | {config.NOME_AUTO} = a tarefa de cada linha vem da "
                          f"coluna {config.COLUNA_TIPO_COBRANCA!r}")
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
                        "(1 pagina a menos por processo)")
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
                 max_cadastros: int | None = None):
        self.automador = automador
        self.registro = registro
        self.perfil = perfil
        self.executar = executar
        self.so_buscar = so_buscar
        self.rapido = rapido
        self.max_cadastros = max_cadastros

        self.contagem = {"ok": 0, "erro": 0, "nao_encontrado": 0, "ja_existia": 0}
        self.nao_encontrados: list[dict] = []
        # Dias em que esta rodada cadastrou alguma coisa. E um conjunto porque
        # uma rodada longa atravessa a meia-noite, e cada dia tem a sua planilha.
        self.dias_cadastrados: set[str] = set()
        self.cadastradas = 0
        # Pares (processo, tarefa): numa rodada auto o mesmo numero pode estar
        # na fila com as duas tarefas, e so uma delas pode ser suspeita.
        self.trilha_sem_achar: list[tuple[str, str]] = []
        self.disjuntor = False
        self.cota_atingida = False
        self.interrompida = False
        self.sessao_expirada: legalone.SessaoExpirada | None = None
        self._busca: legalone.ResultadoBusca | None = None

    # --- laco ----------------------------------------------------------------

    def executar_fila(self, fila: list[planilha.Processo]) -> None:
        total = len(fila)
        inicio = time.monotonic()

        try:
            for i, proc in enumerate(fila, 1):
                self._busca = None
                try:
                    if not self._um_processo(proc, i, total):
                        break
                except legalone.SessaoExpirada:
                    raise
                except Exception as e:
                    self._anotar(
                        proc, ledger_mod.ERRO,
                        # Se a busca chegou a achar o processo, guarda o id: e
                        # por ele que se abre o caso a mao depois.
                        id_legalone=(self._busca.id_legalone
                                     if self._busca and self._busca.encontrado
                                     else ""),
                        detalhe=f"{type(e).__name__}: {e}"[:400],
                    )
                    self.contagem["erro"] += 1
                    logger.error("[%d/%d] %s — ERRO: %s: %s",
                                 i, total, proc.cnj, type(e).__name__, e)
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
        if self.disjuntor:
            self._descartar_suspeitos()

    def _perfil_de(self, proc: planilha.Processo) -> config.PerfilTarefa:
        """Perfil da tarefa deste processo — o da linha, ou o padrao da rodada."""
        return config.PERFIS_POR_DESCRICAO.get(proc.tarefa, self.perfil)

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

        if not self.rapido and self.automador.tarefa_ja_existe(
            busca.id_legalone, perfil.descricao
        ):
            self._anotar(proc, ledger_mod.JA_EXISTIA, busca.id_legalone,
                         "processo ja tinha a tarefa")
            self.contagem["ja_existia"] += 1
            logger.info("[%d/%d] %s — ja tinha %r, pulando",
                        i, total, proc.cnj, perfil.descricao)
            return True

        resultado = self.automador.cadastrar_tarefa(
            busca.id_legalone, self.executar, perfil
        )
        self._anotar(proc, ledger_mod.OK, busca.id_legalone, resultado)
        self.contagem["ok"] += 1
        self.cadastradas += 1
        if self.executar:
            self.dias_cadastrados.add(datetime.date.today().isoformat())
        logger.info("[%d/%d] %s -> id %s (%s) %r %s",
                    i, total, proc.cnj, busca.id_legalone, busca.status,
                    perfil.descricao, resultado)

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
            return False
        return True

    # --- estado --------------------------------------------------------------

    def _anotar(self, proc: planilha.Processo, situacao: str,
                id_legalone: str = "", detalhe: str = "") -> None:
        """Grava no ledger apenas em rodada real — simulacao nao deixa rastro.

        Todo registro leva junto a origem na planilha: e o que preenche as
        colunas do relatorio e da planilha do dia, inclusive quando o processo
        termina em erro.
        """
        if not self.executar:
            return
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
        if self.disjuntor or self.sessao_expirada:
            return SAIDA_ABORTADA
        return SAIDA_OK

    def resumir(self, conferencia: str, pendentes: int) -> None:
        logger.info("Resumo desta rodada: %s", self.contagem)
        for descricao in _descricoes_da_rodada(self.perfil):
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

def _descricoes_da_rodada(perfil: config.PerfilTarefa) -> list[str]:
    """Tarefas que a rodada pode cadastrar — no modo auto, todas elas."""
    if perfil.nome == config.NOME_AUTO:
        return list(config.PERFIS_POR_DESCRICAO)
    return [perfil.descricao]


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
        pular = {(cnj, descricao)
                 for descricao in _descricoes_da_rodada(perfil)
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
        for descricao in _descricoes_da_rodada(perfil):
            quantos = sum(1 for p in processos if p.tarefa == descricao)
            logger.info("             %-18s %d processo(s)",
                        descricao + ":", quantos)
    else:
        logger.info("Tarefa:      %r / tipo %r / status %r",
                    perfil.descricao, perfil.tipo, perfil.status)
        logger.info("Responsavel: %s", perfil.responsavel_esperado)
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
    if args.so_buscar and args.max_cadastros:
        logger.warning("--max-cadastros nao tem efeito com --so-buscar (nada e "
                       "cadastrado); para encurtar o pre-voo use --limite.")
    if args.dia:
        logger.warning("--dia so vale com --relatorio; ignorando.")

    auto = args.tarefa == config.NOME_AUTO
    perfil = config.PERFIL_AUTO if auto else config.PERFIS[args.tarefa]
    _conferir_planilha(args, perfil)
    data_tarefa = _data_da_tarefa(args)

    try:
        processos = planilha.ler(
            args.planilha,
            abas=args.abas,
            tipo_contem=args.tipo_contem,
            status_planilha=args.status_planilha,
            tarefa_da_linha=config.tarefa_do_tipo if auto else None,
        )
    except (FileNotFoundError, ValueError) as e:
        raise ErroDeUso(str(e)) from e

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

        automador = legalone.AutomadorLegalOne(driver, data_tarefa, perfil)
        automador.usar_aba_propria()

        rodada = Rodada(
            automador, registro, perfil,
            executar=args.executar,
            so_buscar=args.so_buscar,
            rapido=args.rapido,
            max_cadastros=args.max_cadastros,
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
