"""Os perfis de tarefa do tarefas.toml.

O arquivo e o que define o que as rodadas cadastram. Erro aqui nao pode passar
em silencio: vira erro de uso antes de qualquer rodada.
"""
import pytest

import config
from config import ErroPerfis, carregar_perfis

PERFIL_OK = """
[conferir-custas]
descricao = "CONFERIR CUSTAS"
tipo = "Diversos / Contato Telefônico"
status = "Pendente"
responsavel = "Pedro Henrique Braz Moreira"
"""


def _arquivo(tmp_path, texto):
    caminho = tmp_path / "tarefas.toml"
    caminho.write_text(texto, encoding="utf-8")
    return caminho


def test_o_arquivo_do_repositorio_mantem_os_perfis_de_producao():
    # O que faturamento e defesa cadastravam quando eram codigo. Mudar isto e
    # mudar o que as rodadas de producao gravam no Legal One.
    perfis = carregar_perfis(config.ARQUIVO_PERFIS)

    assert {n: (p.descricao, p.tipo, p.status, p.responsavel, p.dica_arquivo)
            for n, p in perfis.items()} == {
        "faturamento-final": ("FATURAMENTO FINAL", "Diversos", "Cumprido",
                              "Heloiza Helena de Araujo", "Faturamento"),
        "defesa-faturada": ("DEFESA FATURADA", "Diversos", "Cumprido",
                            "Heloiza Helena de Araujo", "Defesa"),
    }
    assert config.PERFIL_PADRAO == "faturamento-final"
    assert not config.ERRO_PERFIS


def test_perfil_valido(tmp_path):
    perfil = carregar_perfis(_arquivo(tmp_path, PERFIL_OK))["conferir-custas"]

    assert (perfil.nome, perfil.descricao, perfil.tipo, perfil.status,
            perfil.responsavel, perfil.dica_arquivo) == (
        "conferir-custas", "CONFERIR CUSTAS", "Diversos / Contato Telefônico",
        "Pendente", "Pedro Henrique Braz Moreira", "")


@pytest.mark.parametrize("escrito, esperado", [
    ("nao cumprido", "Não cumprido"), ("CUMPRIDO", "Cumprido"),
    ("  pendente ", "Pendente"),
])
def test_status_e_escrito_como_o_legal_one_espera(tmp_path, escrito, esperado):
    texto = PERFIL_OK.replace('"Pendente"', f'"{escrito}"')

    assert carregar_perfis(_arquivo(tmp_path, texto))["conferir-custas"].status \
        == esperado


@pytest.mark.parametrize("campo", ["descricao", "tipo", "status", "responsavel"])
def test_campo_obrigatorio_faltando(tmp_path, campo):
    # Sem padrao escondido: um perfil novo sem responsavel nao pode cair no
    # nome de alguem que ninguem escolheu.
    texto = "\n".join(linha for linha in PERFIL_OK.splitlines()
                      if not linha.startswith(campo))

    with pytest.raises(ErroPerfis, match=f"falta {campo}"):
        carregar_perfis(_arquivo(tmp_path, texto))


def test_campo_desconhecido_e_erro(tmp_path):
    # "responsável" com acento, digitado a mao, nao pode ser ignorado.
    texto = PERFIL_OK.replace("responsavel =", "responsável =")

    with pytest.raises(ErroPerfis, match="desconhecido"):
        carregar_perfis(_arquivo(tmp_path, texto))


def test_status_inexistente(tmp_path):
    with pytest.raises(ErroPerfis, match="nao existe"):
        carregar_perfis(_arquivo(tmp_path, PERFIL_OK.replace("Pendente", "Feito")))


@pytest.mark.parametrize("nome", sorted(config.NOMES_RESERVADOS))
def test_nome_reservado(tmp_path, nome):
    with pytest.raises(ErroPerfis, match="reservado"):
        carregar_perfis(_arquivo(tmp_path, PERFIL_OK.replace("conferir-custas", nome)))


def test_descricao_repetida_entre_perfis(tmp_path):
    # Dividiriam as mesmas linhas do ledger: a rodada de um pularia a do outro.
    texto = PERFIL_OK + PERFIL_OK.replace("[conferir-custas]", "[outro]") \
        .replace('"CONFERIR CUSTAS"', '"conferir custas"')

    with pytest.raises(ErroPerfis, match="repetida"):
        carregar_perfis(_arquivo(tmp_path, texto))


def test_toml_quebrado(tmp_path):
    with pytest.raises(ErroPerfis, match="TOML"):
        carregar_perfis(_arquivo(tmp_path, '[perfil\ndescricao = "X"'))


def test_arquivo_inexistente(tmp_path):
    with pytest.raises(ErroPerfis, match="nao encontrado"):
        carregar_perfis(tmp_path / "nao-existe.toml")


def test_todos_os_problemas_saem_juntos(tmp_path):
    texto = PERFIL_OK.replace("Pendente", "Feito") + '\n[vazio]\ndescricao = "Y"\n'

    with pytest.raises(ErroPerfis) as erro:
        carregar_perfis(_arquivo(tmp_path, texto))
    assert "[conferir-custas]" in str(erro.value) and "[vazio]" in str(erro.value)
