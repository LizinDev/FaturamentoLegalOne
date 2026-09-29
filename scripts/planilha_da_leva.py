"""Gera a planilha de cadastrados de uma leva — um recorte de horario no ledger.

A planilha do dia (--relatorio --dia) corta de meia-noite a meia-noite, mas as
levas entregues a supervisao nao coincidem com dias: em 09-10/09/2026 uma leva
atravessou a meia-noite e o dia 10 teve duas. Aqui o recorte e o que se quiser,
com o mesmo formato de planilha do dia.

    python scripts/planilha_da_leva.py --desde 2026-09-10T14:00 --ate 2026-09-10T18:20 \\
        --saida "Cadastros/Cadastrados Quarta Leva dia 10 de setembro.xlsx"

Nao abre o Chrome e nao muda nada: so le o ledger.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config
import ledger as ledger_mod
import relatorio


def argumentos(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--desde", required=True,
                   help="inicio da leva (AAAA-MM-DD ou AAAA-MM-DDTHH:MM)")
    p.add_argument("--ate", required=True, help="fim da leva, exclusivo")
    p.add_argument("--saida", required=True, help="caminho do .xlsx a gravar")
    p.add_argument("--aba", help="nome da aba (padrao: o dia de --desde)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = argumentos(argv)
    with ledger_mod.Ledger(config.LEDGER_FILE) as registro:
        linhas = registro.cadastrados_entre(args.desde, args.ate)
    n = relatorio.gerar_planilha_do_dia(args.saida, args.aba or args.desde[:10], linhas)
    recadastros = sum(1 for linha in linhas if linha[7] == ledger_mod.RECADASTRADA)
    print(f"{args.saida}: {n} cadastro(s), {recadastros} recadastro(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
