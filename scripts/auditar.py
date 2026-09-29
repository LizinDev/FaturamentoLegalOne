"""Confere no Legal One se as tarefas que o ledger da como cadastradas existem.

Ate a 1.7 o Salvar recusado podia virar 'ok' no ledger sem tarefa nenhuma
(208 de 3.003 em 09-10/09/2026). So a grade de compromissos do processo diz a
verdade, e e ela que este script le, processo a processo.

Nao muda nada no ledger nem no Legal One: grava um CSV com o que achou. O que
fazer com os ausentes (tirar do ledger para voltarem a fila) esta no MANUAL,
em "O ledger diz cadastrado mas a tarefa nao existe".

    python scripts/auditar.py --desde 2026-09-10T14:00
    python scripts/auditar.py --desde 2026-09-09 --ate 2026-09-10 \
        --saida "Cadastros/Auditoria 09-09.csv"

Precisa do Chrome de debug logado, e so dele: usar o navegador durante a
auditoria atrapalha a leitura, como numa rodada.
"""
import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config
import ledger as ledger_mod
import legalone

# Uma leitura que diz "nao existe" e repetida antes de valer: a grade as vezes
# vem sem a tarefa na primeira carga, e e esse resultado que decide o que sai
# do ledger.
TENTATIVAS = 2
PAUSA_ENTRE_TENTATIVAS = 3


def argumentos(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--desde", required=True,
                   help="inicio do recorte no ledger (AAAA-MM-DD ou AAAA-MM-DDTHH:MM)")
    p.add_argument("--ate", default="9999",
                   help="fim do recorte, exclusivo (padrao: ate o ultimo cadastro)")
    p.add_argument("--saida", help="CSV de saida (padrao: data/auditoria_<desde>.csv)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = argumentos(argv)
    saida = Path(args.saida or config.DATA_DIR /
                 f"auditoria_{args.desde.replace(':', '')}.csv")

    with ledger_mod.Ledger(config.LEDGER_FILE) as registro:
        alvos = registro.cadastrados_entre(args.desde, args.ate)
    print(f"A conferir: {len(alvos)} cadastro(s) entre {args.desde} e {args.ate}",
          flush=True)
    if not alvos:
        return 0

    automador = legalone.AutomadorLegalOne(legalone.conectar())
    automador.usar_aba_propria()

    ausentes = falhas = 0
    with open(saida, "w", newline="", encoding="utf-8-sig") as f:
        escritor = csv.writer(f, delimiter=";")
        escritor.writerow(["PROCESSO", "TAREFA", "ID_LEGALONE", "CADASTRADO_EM",
                           "SITUACAO", "EXISTE"])
        for i, (cnj, tarefa, id_lo, *_, quando, situacao) in enumerate(
                (linha[:8] for linha in alvos), 1):
            existe = None
            try:
                for tentativa in range(TENTATIVAS):
                    existe = automador.tarefa_ja_existe(id_lo, tarefa)
                    if existe:
                        break
                    if tentativa + 1 < TENTATIVAS:
                        time.sleep(PAUSA_ENTRE_TENTATIVAS)
            except legalone.SessaoExpirada as e:
                print(f"SESSAO EXPIRADA: {e}", flush=True)
                return 3
            except Exception as e:
                falhas += 1
                print(f"[{i}] {cnj}: falha na conferencia ({type(e).__name__})",
                      flush=True)
            resultado = ("ERRO_CONFERENCIA" if existe is None
                         else "sim" if existe else "NAO")
            if existe is False:
                ausentes += 1
                print(f"[{i}] AUSENTE {cnj} (id {id_lo}) {tarefa!r}", flush=True)
            escritor.writerow([cnj, tarefa, id_lo, quando, situacao, resultado])
            f.flush()
            if i % 100 == 0:
                print(f"  ... {i}/{len(alvos)} | ausentes ate aqui: {ausentes}",
                      flush=True)

    print(f"Fim: {len(alvos)} conferido(s), {ausentes} ausente(s), "
          f"{falhas} sem conferencia. Resultado em {saida}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
