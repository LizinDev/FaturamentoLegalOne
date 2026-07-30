"""Cadastro em lote da tarefa FATURAMENTO FINAL no Legal One.

Le a planilha de cobrancas, encontra cada processo no Legal One e cadastra a
tarefa. Por padrao roda em simulacao — precisa de --executar para gravar.
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


def argumentos():
    p = argparse.ArgumentParser(
        description="Cadastra a tarefa FATURAMENTO FINAL em lote no Legal One.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""exemplos:
  # simulacao das 20 primeiras (nao grava nada)
  python main.py --planilha "C:/Users/Kamila/Downloads/Processos.xlsx" --limite 20

  # cota do dia: para depois de 500 tarefas cadastradas
  python main.py --planilha "C:/.../Processos.xlsx" --max-cadastros 500 --executar

  # rodada real, planilha inteira, retomavel
  python main.py --planilha "C:/.../Processos.xlsx" --executar

  # so a aba 2026, encerramentos finais
  python main.py --planilha "..." --abas 2026 --tipo-contem "ENCERRAMENTO FINAL" --executar

  # refazer os relatorios, ou a planilha de um dia especifico
  python main.py --relatorio
  python main.py --relatorio --dia 2026-07-30
""",
    )
    p.add_argument("--planilha", help="caminho do .xlsx de cobrancas")
    p.add_argument("--tarefa", choices=sorted(config.PERFIS), default=config.PERFIL_PADRAO,
                   help=f"qual tarefa cadastrar (padrao: {config.PERFIL_PADRAO}). "
                        + " | ".join(f"{n} = {p.descricao!r}"
                                     for n, p in sorted(config.PERFIS.items())))
    p.add_argument("--forcar-planilha", action="store_true",
                   help="ignora a trava que confere se a planilha combina com --tarefa")
    p.add_argument("--abas", nargs="+", help="abas a considerar (padrao: todas)")
    p.add_argument("--tipo-contem", help="filtra TIPO DE COBRANCA por substring")
    p.add_argument("--status-planilha", help="filtra STATUS LEGAL ONE exato (ex.: Ativo)")
    p.add_argument("--limite", type=int,
                   help="examina no maximo N processos (inclui os nao encontrados)")
    p.add_argument("--max-cadastros", type=int, metavar="N",
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
                   help="pula a checagem de tarefa duplicada (1 pagina a menos por processo)")
    p.add_argument("--so-buscar", action="store_true",
                   help="pre-voo: so procura os processos e relata quais nao existem, "
                        "sem abrir formulario (bem mais rapido)")
    p.add_argument("--relatorio", action="store_true",
                   help="so exporta os relatorios do que ja rodou e sai")
    p.add_argument("--dia", metavar="AAAA-MM-DD",
                   help="com --relatorio, refaz a planilha de um dia especifico")
    return p.parse_args()


def _gerar_planilha_do_dia(registro, dia: str) -> int:
    linhas = registro.cadastrados_em(dia)
    return relatorio.gerar_planilha_do_dia(
        config.planilha_do_dia(dia), dia, linhas
    )


def _formatar_tempo(segundos: float) -> str:
    segundos = int(segundos)
    h, resto = divmod(segundos, 3600)
    m, s = divmod(resto, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def main() -> int:
    args = argumentos()

    logger.info("=" * 60)
    logger.info("%s v%s", config.PROJECT_NAME, config.VERSION)
    logger.info("=" * 60)

    registro = ledger_mod.Ledger(config.LEDGER_FILE)

    if args.relatorio:
        registro.exportar_csv(config.RELATORIO_CSV)
        registro.exportar_nao_encontrados(config.NAO_ENCONTRADOS_CSV)
        dias = [args.dia] if args.dia else registro.dias_com_cadastro()
        if not dias:
            logger.info("Nenhum cadastro registrado ainda.")
        for dia in dias:
            _gerar_planilha_do_dia(registro, dia)
        logger.info("Situacao atual: %s", dict(registro.resumo()))
        registro.close()
        return 0

    if not args.planilha:
        logger.error("Falta --planilha (ou use --relatorio)")
        return 2

    if args.so_buscar and args.executar:
        logger.error("--so-buscar nao cadastra nada; nao combine com --executar, "
                     "senao o ledger marcaria como feito o que nunca foi cadastrado.")
        return 2

    perfil = config.PERFIS[args.tarefa]

    # Parear a planilha de uma tarefa com o perfil da outra criaria centenas de
    # tarefas erradas, e desfazer isso e trabalho manual. Quando o perfil declara
    # um trecho esperado no nome do arquivo, ele e conferido antes de comecar.
    if perfil.dica_arquivo and not args.forcar_planilha:
        if perfil.dica_arquivo.lower() not in str(args.planilha).lower():
            logger.error(
                "A planilha nao parece ser a de %r: esperava %r no caminho, "
                "e veio %r. Confira o par planilha/--tarefa, ou use "
                "--forcar-planilha se estiver certo.",
                perfil.descricao, perfil.dica_arquivo, args.planilha,
            )
            return 2

    data_tarefa = args.data or datetime.date.today().strftime("%d/%m/%Y")
    try:
        datetime.datetime.strptime(data_tarefa, "%d/%m/%Y")
    except ValueError:
        logger.error("Data invalida: %r (esperado DD/MM/AAAA)", data_tarefa)
        return 2

    try:
        processos = planilha.ler(
            args.planilha,
            abas=args.abas,
            tipo_contem=args.tipo_contem,
            status_planilha=args.status_planilha,
        )
    except (FileNotFoundError, ValueError) as e:
        logger.error("%s", e)
        return 2

    # Uma simulacao nao deve deixar rastro no ledger, senao a rodada real
    # seguinte "pularia" processos que nunca foram cadastrados de fato.
    if not args.executar:
        pular = set()
    elif args.retentar:
        pular = registro.concluidos(perfil.descricao)
    else:
        pular = registro.todos(perfil.descricao)

    if args.processo:
        # Normaliza igual a planilha, para o numero digitado casar.
        alvo = {planilha.normalizar(n)[0] for n in args.processo}
        processos = [p for p in processos if p.cnj in alvo]
        faltando = alvo - {p.cnj for p in processos}
        if faltando:
            logger.warning("Nao esta(o) na planilha: %s", ", ".join(sorted(faltando)))
        pular = set()

    fila = [p for p in processos if p.cnj not in pular]
    if args.limite:
        fila = fila[: args.limite]

    logger.info("Perfil:      %s", perfil.nome)
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

    if not fila:
        logger.info("Nada a fazer.")
        registro.exportar_csv(config.RELATORIO_CSV)
        registro.close()
        return 0

    try:
        driver = legalone.conectar()
    except Exception as e:
        logger.error("Nao consegui conectar ao Chrome em %s: %s", config.DEBUG_ADDRESS, e)
        logger.error("Abra o Chrome com --remote-debugging-port=%d e logue no Legal One.",
                     config.DEBUG_PORT)
        registro.close()
        return 1

    automador = legalone.AutomadorLegalOne(driver, data_tarefa, perfil)
    automador.usar_aba_propria()

    inicio = time.monotonic()
    contagem = {"ok": 0, "erro": 0, "nao_encontrado": 0, "ja_existia": 0}

    def anotar(cnj: str, situacao: str, *a, **kw) -> None:
        """Grava no ledger apenas em rodada real — simulacao nao deixa rastro."""
        if args.executar:
            registro.registrar(cnj, perfil.descricao, situacao, *a, **kw)

    trilha_sem_achar: list[str] = []
    nao_encontrados: list[dict] = []
    disjuntor = False
    cadastradas = 0
    cota_atingida = False

    try:
        for i, proc in enumerate(fila, 1):
            try:
                if not automador.aba_viva():
                    logger.warning("A aba de trabalho sumiu (fechada?) — recriando")
                    automador.usar_aba_propria()

                busca = automador.buscar_processo(proc.cnj)

                if not busca.encontrado:
                    detalhe = busca.detalhe
                    if not proc.formato_ok:
                        detalhe = f"{detalhe} (numero fora do padrao CNJ)"
                    situacao = (ledger_mod.AMBIGUO if "ambiguo" in busca.detalhe
                                else ledger_mod.NAO_ENCONTRADO)
                    anotar(proc.cnj, situacao, detalhe=detalhe, origem=proc.origem,
                           tipo_cobranca="; ".join(proc.tipos_cobranca),
                           status_planilha="; ".join(proc.status_planilha))
                    nao_encontrados.append({
                        "cnj": proc.cnj_original,
                        # O numero de fato pesquisado; difere do original quando
                        # a planilha traz ponto no lugar do primeiro hifen.
                        "cnj_busca": proc.cnj,
                        "tarefa": perfil.descricao,
                        "situacao": situacao,
                        "motivo": detalhe,
                        "tipo_cobranca": "; ".join(proc.tipos_cobranca),
                        "status_planilha": "; ".join(proc.status_planilha),
                        "origem": proc.origem,
                    })
                    contagem["nao_encontrado"] += 1
                    trilha_sem_achar.append(proc.cnj)
                    logger.warning("[%d/%d] %s — %s", i, len(fila), proc.cnj, detalhe)

                    if len(trilha_sem_achar) >= config.MAX_NAO_ENCONTRADOS_SEGUIDOS:
                        disjuntor = True
                        break
                    continue

                trilha_sem_achar.clear()

                if args.so_buscar:
                    contagem["ok"] += 1
                    logger.info("[%d/%d] %s -> id %s (%s)",
                                i, len(fila), proc.cnj, busca.id_legalone, busca.status)
                    continue

                if not args.rapido and automador.tarefa_ja_existe(
                    busca.id_legalone, perfil.descricao
                ):
                    anotar(proc.cnj, ledger_mod.JA_EXISTIA, busca.id_legalone,
                           "processo ja tinha a tarefa", proc.origem)
                    contagem["ja_existia"] += 1
                    logger.info("[%d/%d] %s — ja tinha a tarefa, pulando",
                                i, len(fila), proc.cnj)
                    continue

                resultado = automador.cadastrar_tarefa(busca.id_legalone, args.executar)

                anotar(proc.cnj, ledger_mod.OK, busca.id_legalone,
                       resultado, proc.origem,
                       tipo_cobranca="; ".join(proc.tipos_cobranca),
                       status_planilha="; ".join(proc.status_planilha))
                contagem["ok"] += 1
                cadastradas += 1
                logger.info("[%d/%d] %s -> id %s (%s) %s",
                            i, len(fila), proc.cnj, busca.id_legalone,
                            busca.status, resultado)

                if args.max_cadastros and cadastradas >= args.max_cadastros:
                    cota_atingida = True
                    break

            except legalone.SessaoExpirada:
                raise
            except Exception as e:
                anotar(proc.cnj, ledger_mod.ERRO,
                       detalhe=f"{type(e).__name__}: {e}"[:400],
                       origem=proc.origem)
                contagem["erro"] += 1
                logger.error("[%d/%d] %s — ERRO: %s: %s",
                             i, len(fila), proc.cnj, type(e).__name__, e)

            finally:
                # Progresso e pausa valem para todo processo, inclusive os que
                # sairam por 'continue' — senao o ETA some justo nas rodadas
                # cheias de nao-encontrado.
                if i % 25 == 0 or i == len(fila):
                    decorrido = time.monotonic() - inicio
                    por_item = decorrido / i
                    logger.info(
                        "  ... %d/%d | %.1fs/processo | decorrido %s | falta ~%s | %s",
                        i, len(fila), por_item, _formatar_tempo(decorrido),
                        _formatar_tempo(por_item * (len(fila) - i)), contagem,
                    )
                time.sleep(config.PAUSA_ENTRE_PROCESSOS)

        if cota_atingida:
            logger.info("Cota do dia atingida: %d tarefa(s) cadastrada(s). "
                        "O restante da fila fica para a proxima rodada.",
                        cadastradas)

        if disjuntor:
            # Esses "nao encontrado" sao suspeitos: apagar do ledger faz a
            # retomada avalia-los de novo em vez de pula-los como resolvidos.
            apagados = (registro.esquecer(trilha_sem_achar, perfil.descricao)
                        if args.executar else 0)
            contagem["nao_encontrado"] -= len(trilha_sem_achar)
            # Tambem saem da lista de conferencia: nao sao casos para o usuario
            # investigar, sao efeito colateral da queda.
            suspeitos = set(trilha_sem_achar)
            nao_encontrados[:] = [n for n in nao_encontrados
                                  if n["cnj_busca"] not in suspeitos]
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

    except KeyboardInterrupt:
        logger.warning("Interrompido pelo usuario — progresso salvo no ledger.")
    except legalone.SessaoExpirada as e:
        logger.error("SESSAO EXPIRADA: %s", e)
    finally:
        registro.exportar_csv(config.RELATORIO_CSV)
        ledger_mod.escrever_nao_encontrados(
            config.NAO_ENCONTRADOS_CSV, nao_encontrados
        )
        hoje = datetime.date.today().isoformat()
        if args.executar:
            _gerar_planilha_do_dia(registro, hoje)

        logger.info("Resumo desta rodada: %s", contagem)
        logger.info("Acumulado em %-18s %s", perfil.descricao + ":",
                    dict(registro.resumo(perfil.descricao)))
        logger.info("Acumulado geral:     %s", dict(registro.resumo()))
        logger.info("Relatorio:            %s", config.RELATORIO_CSV)
        logger.info("Para conferir a mao:  %s (%d processo[s])",
                    config.NAO_ENCONTRADOS_CSV, len(nao_encontrados))
        if args.executar:
            logger.info("Planilha do dia:      %s", config.planilha_do_dia(hoje))
        else:
            logger.info("Foi SIMULACAO — nada foi gravado no Legal One.")
        registro.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
