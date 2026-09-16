"""Infra dos testes.

Nada aqui abre o Chrome nem toca no Legal One: o Selenium so aparece como um
automador falso. Os testes que envolvem arquivos usam sempre `tmp_path` — o
ledger de producao guarda o historico real de cadastros e nao pode ser tocado.
"""
import os
import sys
from pathlib import Path

import pytest

# Antes do import do config, que configura o logging: teste nao escreve no log
# de producao.
os.environ["FATURAMENTO_LOG_FILE"] = ""

RAIZ = Path(__file__).resolve().parent.parent
SRC = RAIZ / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import config  # noqa: E402  (precisa do sys.path acima)
import ledger as ledger_mod  # noqa: E402


def situacao_de(registro, cnj: str, tarefa: str) -> str | None:
    """Situacao gravada para um par (processo, tarefa), ou None.

    Fica nos testes, e nao no Ledger: o programa nunca precisa consultar um
    processo isolado — ele trabalha com os conjuntos de `todos`/`concluidos`.
    """
    linha = registro.con.execute(
        "SELECT situacao FROM processos WHERE cnj = ? AND tarefa = ?",
        (cnj, tarefa),
    ).fetchone()
    return linha[0] if linha else None


@pytest.fixture
def dados_tmp(tmp_path, monkeypatch):
    """Aponta os caminhos do config para um diretorio descartavel."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "LEDGER_FILE", str(tmp_path / "ledger.sqlite3"))
    monkeypatch.setattr(config, "RELATORIO_CSV", str(tmp_path / "relatorio.csv"))
    monkeypatch.setattr(config, "NAO_ENCONTRADOS_CSV",
                        str(tmp_path / "nao_encontrados.csv"))
    monkeypatch.setattr(config, "NAO_ENCONTRADOS_SIMULACAO_CSV",
                        str(tmp_path / "nao_encontrados_simulacao.csv"))
    # A pausa entre processos so existe para nao martelar o Legal One.
    monkeypatch.setattr(config, "PAUSA_ENTRE_PROCESSOS", 0)
    return tmp_path


@pytest.fixture
def registro(dados_tmp):
    """Ledger vazio, em arquivo temporario."""
    with ledger_mod.Ledger(config.LEDGER_FILE) as led:
        yield led


@pytest.fixture
def perfil():
    return config.PERFIS[config.PERFIL_PADRAO]
