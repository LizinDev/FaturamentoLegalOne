"""Casamento do tipo e do responsavel pedidos com o que existe no Legal One.

A regra e nunca chutar: o que nao casa com exatamente uma opcao para a rodada
antes do primeiro cadastro, com a lista do que existe.
"""
import pytest

import catalogo
from catalogo import NaoResolvido, resolver_tipo, resolver_usuario
from test_rodada import ARVORE, USUARIOS


def _id(pedido):
    return resolver_tipo(pedido, ARVORE).id


# --- tipo --------------------------------------------------------------------

@pytest.mark.parametrize("pedido, esperado", [
    ("Diversos", "tipo_4"),
    ("Diversos / Contato Telefônico", "subtipo_9"),
    # Sem acento, caixa e espacos diferentes: o que se digita a mao.
    ("diversos  /  contato telefonico", "subtipo_9"),
    # ">" e aceito como separador.
    ("Diversos > Contato Telefônico", "subtipo_9"),
    # O nome tem barra: o caminho e comparado inteiro, nunca quebrado.
    ("[Cível] Prazos / Agravo em REsp / Rext", "subtipo_1287"),
])
def test_tipo_pelo_caminho(pedido, esperado):
    assert _id(pedido) == esperado


def test_nome_sozinho_vale_quando_e_unico():
    tipo = resolver_tipo("Contato Telefônico", ARVORE)
    # O caminho volta como o Legal One o escreve: e ele que vai para o ledger.
    assert (tipo.id, tipo.caminho, tipo.pai) == (
        "subtipo_9", "Diversos / Contato Telefônico", "tipo_4")


def test_caminho_exato_ganha_do_nome_repetido():
    # "Audiência" e um tipo e tambem um subtipo de outro pai. Escrito sozinho,
    # e o caminho do tipo — ele e exatamente "Audiência".
    assert _id("Audiência") == "tipo_7"


def test_nome_repetido_sem_caminho_proprio_pede_o_caminho():
    arvore = [t for t in ARVORE if t.id != "tipo_7"] + [
        catalogo.Tipo("subtipo_60", "Diversos / Audiência", "Audiência", "tipo_4")]

    with pytest.raises(NaoResolvido, match="mais de um lugar") as erro:
        resolver_tipo("Audiência", arvore)
    assert "[Cível] Prazos / Audiência" in str(erro.value)
    assert "Diversos / Audiência" in str(erro.value)


def test_tipo_inexistente_sugere_os_parecidos():
    with pytest.raises(NaoResolvido, match="nao existe") as erro:
        resolver_tipo("Prazos / Apelacao", ARVORE)
    assert "[Cível] Prazos" in str(erro.value)


def test_tipo_vazio_e_recusado():
    with pytest.raises(NaoResolvido):
        resolver_tipo("  ", ARVORE)


def test_lista_longa_de_sugestoes_e_cortada():
    arvore = [catalogo.Tipo(f"subtipo_{i}", f"Pai{i} / Audiência", "Audiência",
                            f"tipo_{i}") for i in range(20)]

    with pytest.raises(NaoResolvido, match=r"e mais 12"):
        resolver_tipo("Audiência", arvore)


# --- responsavel -------------------------------------------------------------

@pytest.mark.parametrize("pedido", [
    "Heloiza Helena de Araujo",
    "heloiza helena de araújo",   # acento a mais: a busca do Legal One ignora
    "Heloiza",                    # unico que casa
])
def test_responsavel_resolve_para_o_nome_do_legal_one(pedido):
    nomes = [n for n in USUARIOS
             if catalogo.normalizar(pedido.split()[0]) in catalogo.normalizar(n)]
    assert resolver_usuario(pedido, nomes) == "Heloiza Helena de Araujo"


def test_nome_igual_ganha_mesmo_com_outros_resultados():
    nomes = ["Ana Clara", "Ana Clara Stroparo"]
    assert resolver_usuario("ana clara", nomes) == "Ana Clara"


def test_responsavel_ambiguo_para_com_a_lista():
    with pytest.raises(NaoResolvido, match="mais de um usuario") as erro:
        resolver_usuario("Ana", ["Ana Clara Stroparo", "Ana Luiza Saitone Costa"])
    assert "Ana Luiza Saitone Costa" in str(erro.value)


def test_responsavel_que_nao_e_usuario_ativo():
    with pytest.raises(NaoResolvido, match="nao e usuario ativo"):
        resolver_usuario("Fulano", [])


def test_resultado_repetido_nao_vira_ambiguidade():
    assert resolver_usuario("Heloiza", ["Heloiza Helena de Araujo"] * 2) == \
        "Heloiza Helena de Araujo"
