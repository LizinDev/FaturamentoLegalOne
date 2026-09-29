"""Leitura da planilha de cobrancas -> lista de processos unicos."""
import collections
import dataclasses
import datetime
import logging
import re
from collections.abc import Callable
from pathlib import Path

from openpyxl import load_workbook

import config
import datas

logger = logging.getLogger(__name__)

# NNNNNNN-DD.AAAA.J.TR.OOOO
RE_CNJ = re.compile(r"^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$")
# Mesma coisa, mas com ponto no lugar do hifen — erro de digitacao comum na
# planilha (ex.: 0064904.50.2019.8.05.0001). E recuperavel sem ambiguidade.
RE_CNJ_PONTO = re.compile(r"^(\d{7})\.(\d{2}\.\d{4}\.\d\.\d{2}\.\d{4})$")


@dataclasses.dataclass
class Processo:
    """Um processo unico da planilha, com a origem agregada."""

    cnj: str                 # numero normalizado, usado na busca
    cnj_original: str        # como estava na planilha
    formato_ok: bool
    # Descricao da tarefa a cadastrar neste processo. Vazia quando a tarefa vale
    # para a rodada toda; preenchida quando ela sai da propria linha.
    tarefa: str = ""
    tipos_cobranca: list[str] = dataclasses.field(default_factory=list)
    status_planilha: list[str] = dataclasses.field(default_factory=list)
    # Onde o processo aparece, no formato "aba!Lnn" — um processo repetido em
    # varias linhas/abas vira uma entrada so, com todas as origens.
    linhas: list[str] = dataclasses.field(default_factory=list)
    # Com --tarefa planilha: tipo, status, responsavel e datas como vieram das
    # colunas "... DA TAREFA" da linha ("" onde a celula estava vazia).
    campos_tarefa: dict[str, str] = dataclasses.field(default_factory=dict)
    # O perfil completo da tarefa deste processo. Quem le a planilha nao o
    # preenche: e a preparacao da rodada que junta linha, flags e tarefas.toml.
    perfil: "config.PerfilTarefa | None" = None
    # As datas pedidas na propria linha (modo planilha); por cima das da rodada.
    agenda: "datas.Agenda | None" = None

    @property
    def origem(self) -> str:
        """Resumo de onde o processo aparece, para o relatorio."""
        return "; ".join(self.linhas)


def normalizar(numero: str) -> tuple[str, bool]:
    """Devolve (numero_normalizado, formato_ok).

    So corrige o caso do ponto no lugar do hifen. Numeros realmente quebrados
    passam adiante como estao: a busca vai falhar e eles aparecem no relatorio
    para conferencia manual, em vez de sumirem silenciosamente.
    """
    limpo = " ".join(str(numero).split())
    if RE_CNJ.match(limpo):
        return limpo, True
    m = RE_CNJ_PONTO.match(limpo)
    if m:
        return f"{m.group(1)}-{m.group(2)}", True
    return limpo, False


def _indices_cabecalho(linha) -> dict[str, int]:
    """Mapeia nome da coluna -> indice, a partir da linha de cabecalho."""
    indices = {}
    for i, valor in enumerate(linha):
        if valor is None:
            continue
        indices[" ".join(str(valor).split()).upper()] = i
    return indices


def _celula(linha, indices: dict[str, int], *colunas: str) -> str:
    """Valor de uma coluna nomeada, ja normalizado ("" se ausente ou vazia).

    Com mais de um nome, vale o primeiro que tiver valor naquela linha: sao
    nomes alternativos do mesmo campo (ver COLUNAS_TIPO_COBRANCA), e a planilha
    pode trazer a coluna velha em branco ao lado da nova.
    """
    for coluna in colunas:
        i = indices.get(coluna)
        if i is None or i >= len(linha) or linha[i] is None:
            continue
        valor = " ".join(str(linha[i]).split())
        if valor:
            return valor
    return ""


def ler(
    caminho: str | Path,
    abas: list[str] | None = None,
    tipo_contem: str | None = None,
    status_planilha: str | None = None,
    tarefa_da_linha: Callable[[str], str | None] | None = None,
    colunas_da_tarefa: bool = False,
) -> list[Processo]:
    """Le a planilha e devolve os processos unicos, na ordem de aparicao.

    abas             — nomes de abas a considerar (None = todas)
    tipo_contem      — filtra por substring em TIPO DE COBRANCA (case-insensitive)
    status_planilha  — filtra por STATUS LEGAL ONE exato (ex.: "Ativo")
    tarefa_da_linha  — recebe o TIPO DE COBRANCA e devolve a descricao da tarefa
                       daquela linha, ou None para "nao reconheco isto" (a linha
                       e pulada). Com esta funcao a deduplicacao passa a ser por
                       (processo, tarefa): o mesmo numero que aparece como defesa
                       e como faturamento precisa das duas tarefas, e deduplicar
                       so pelo numero perderia uma delas.
    colunas_da_tarefa — a tarefa vem das colunas "... DA TAREFA" (modo
                       --tarefa planilha). A descricao e obrigatoria por linha:
                       linha sem ela e pulada, com aviso. A deduplicacao e por
                       (processo, descricao), e o mesmo par com tipo, status ou
                       responsavel diferentes em duas linhas e erro — o ledger
                       guarda um cadastro por par, e escolher um dos dois seria
                       chute.
    """
    caminho = Path(caminho)
    if not caminho.is_file():
        raise FileNotFoundError(f"Planilha nao encontrada: {caminho}")

    wb = load_workbook(caminho, read_only=True, data_only=True)
    try:
        alvo = abas or wb.sheetnames
        desconhecidas = [a for a in alvo if a not in wb.sheetnames]
        if desconhecidas:
            raise ValueError(
                f"Aba(s) inexistente(s): {desconhecidas}. "
                f"Disponiveis: {wb.sheetnames}"
            )

        # A chave e o par (numero, tarefa). Sem tarefa_da_linha a tarefa e "" em
        # todas as linhas e o par se comporta como a chave so pelo numero.
        por_chave: dict[tuple[str, str], Processo] = {}
        total_linhas = 0
        nao_reconhecidos: collections.Counter = collections.Counter()
        sem_descricao = 0
        abas_com_descricao = 0
        conflitos: list[str] = []

        for nome in alvo:
            ws = wb[nome]
            indices: dict[str, int] = {}

            for n_linha, linha in enumerate(ws.iter_rows(values_only=True), 1):
                if n_linha == 1:
                    indices = _indices_cabecalho(linha)
                    if config.COLUNA_PROCESSO not in indices:
                        logger.warning(
                            "Aba %r sem coluna %r — ignorada",
                            nome, config.COLUNA_PROCESSO,
                        )
                        break
                    if colunas_da_tarefa:
                        if config.COLUNA_DESCRICAO_TAREFA in indices:
                            abas_com_descricao += 1
                        else:
                            logger.warning("Aba %r sem coluna %r — ignorada",
                                           nome, config.COLUNA_DESCRICAO_TAREFA)
                            break
                    continue

                bruto = _celula(linha, indices, config.COLUNA_PROCESSO)
                if not bruto:
                    continue

                tipo = _celula(linha, indices, *config.COLUNAS_TIPO_COBRANCA)
                status = _celula(linha, indices, config.COLUNA_STATUS_LEGALONE)

                if tipo_contem and tipo_contem.upper() not in tipo.upper():
                    continue
                if status_planilha and status != status_planilha:
                    continue

                tarefa = ""
                campos: dict[str, str] = {}
                if colunas_da_tarefa:
                    tarefa = _celula(linha, indices, config.COLUNA_DESCRICAO_TAREFA)
                    if not tarefa:
                        sem_descricao += 1
                        continue
                    campos = {
                        "tipo": _celula(linha, indices, config.COLUNA_TIPO_TAREFA),
                        "status": _celula(linha, indices, config.COLUNA_STATUS_TAREFA),
                        "responsavel": _celula(linha, indices,
                                               config.COLUNA_RESPONSAVEL_TAREFA),
                        "inicio": _celula_data(linha, indices,
                                               config.COLUNA_INICIO_TAREFA),
                        "fim": _celula_data(linha, indices,
                                            config.COLUNA_CONCLUSAO_TAREFA),
                        "publicacao": _celula_data(linha, indices,
                                                   config.COLUNA_PUBLICACAO_TAREFA),
                        "disponibilizacao": _celula_data(
                            linha, indices, config.COLUNA_DISPONIBILIZACAO_TAREFA),
                    }
                elif tarefa_da_linha is not None:
                    tarefa = tarefa_da_linha(tipo) or ""
                    if not tarefa:
                        # Pular e mais seguro do que chutar uma tarefa: a coluna
                        # e texto livre e as abas mais novas tem dezenas de
                        # variantes. Os valores saem no aviso do fim.
                        nao_reconhecidos[tipo or "(vazio)"] += 1
                        continue

                total_linhas += 1
                cnj, ok = normalizar(bruto)

                proc = por_chave.get((cnj, tarefa))
                if proc is None:
                    proc = Processo(cnj=cnj, cnj_original=bruto, formato_ok=ok,
                                    tarefa=tarefa, campos_tarefa=campos)
                    por_chave[(cnj, tarefa)] = proc
                elif campos != proc.campos_tarefa:
                    conflitos.append(
                        f"{bruto} / {tarefa!r}: {proc.linhas[0]} "
                        f"{_resumo(proc.campos_tarefa)} x {nome}!L{n_linha} "
                        f"{_resumo(campos)}"
                    )

                if tipo and tipo not in proc.tipos_cobranca:
                    proc.tipos_cobranca.append(tipo)
                if status and status not in proc.status_planilha:
                    proc.status_planilha.append(status)
                proc.linhas.append(f"{nome}!L{n_linha}")
    finally:
        wb.close()

    if colunas_da_tarefa and not abas_com_descricao:
        raise ValueError(
            f"Nenhuma aba tem a coluna {config.COLUNA_DESCRICAO_TAREFA!r}, que o "
            f"modo --tarefa planilha exige."
        )
    if conflitos:
        raise ValueError(
            "O mesmo processo pede a mesma tarefa com valores diferentes:\n  "
            + "\n  ".join(conflitos[:20])
            + (f"\n  (e mais {len(conflitos) - 20})" if len(conflitos) > 20 else "")
        )

    processos = list(por_chave.values())
    invalidos = sum(1 for p in processos if not p.formato_ok)
    logger.info(
        "Planilha lida: %d linha(s) -> %d processo(s) unico(s) (%d fora do padrao CNJ)",
        total_linhas, len(processos), invalidos,
    )
    if sem_descricao:
        logger.warning("%d linha(s) puladas por %s vazia",
                       sem_descricao, config.COLUNA_DESCRICAO_TAREFA)
    if nao_reconhecidos:
        logger.warning(
            "%d linha(s) puladas por TIPO DE COBRANCA sem tarefa correspondente: %s",
            sum(nao_reconhecidos.values()),
            "; ".join(f"{valor!r} ({n})"
                      for valor, n in nao_reconhecidos.most_common()),
        )
    return processos


def _celula_data(linha, indices: dict[str, int], coluna: str) -> str:
    """Celula de data/hora como texto DD/MM/AAAA [HH:MM:SS] ("" se vazia).

    O openpyxl devolve celula de data como datetime, e str() dela daria
    "2026-09-29 00:00:00". Texto digitado passa como esta: quem o le e
    interpreta, com as mensagens de erro, e o datas.py. Meia-noite e "sem hora"
    — e como o Excel guarda uma data sem hora.
    """
    i = indices.get(coluna)
    valor = linha[i] if i is not None and i < len(linha) else None
    if isinstance(valor, datetime.datetime):
        if valor.time() == datetime.time(0):
            return valor.strftime("%d/%m/%Y")
        return valor.strftime("%d/%m/%Y %H:%M:%S")
    if isinstance(valor, datetime.date):
        return valor.strftime("%d/%m/%Y")
    if isinstance(valor, datetime.time):
        return valor.strftime("%H:%M:%S")
    return _celula(linha, indices, coluna)


def _resumo(campos: dict[str, str]) -> str:
    """Os campos preenchidos de uma linha, para a mensagem de conflito."""
    return "(" + ", ".join(f"{k}={v}" for k, v in campos.items() if v) + ")"
