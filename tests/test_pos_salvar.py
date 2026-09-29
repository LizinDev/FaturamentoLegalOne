"""O que a pagina diz depois do Salvar — e quando um cadastro pode virar 'ok'.

Ate a 1.8, sair de CreateFromProcesso bastava. Mas o formulario devolvido em
/processos/tarefas/Edit tambem sai de la, e era assim que a recusa e o aviso de
data passada viravam cadastro fantasma.
"""
import pytest

import legalone
from legalone import PEDIU_CONFIRMACAO, RECUSOU, SALVOU, situacao_pos_salvar

BASE = "https://hasson.novajus.com.br"
FORMULARIO = BASE + "/processos/tarefas/CreateFromProcesso/118827"
DEVOLVIDO = BASE + "/processos/tarefas/Edit"
AVISO = ("A data de \"Início\" do compromisso ou de \"Conclusão\" da tarefa é "
         "anterior à data atual.Deseja salvar mesmo assim?")
ERRO = ("O status selecionado não pode ser 'Pendente' quando a data de "
        "conclusão for anterior à data atual")


@pytest.mark.parametrize("destino", [
    # Onde o "Salvar e fechar" cai quando grava, mesmo mostrando erro de servidor.
    BASE + "/processos/compromissotarefa",
    BASE + "/processos/Processos/DetailsCompromissosTarefas/118827",
])
def test_saiu_do_formulario_para_outra_pagina_e_salvou(destino):
    assert situacao_pos_salvar(destino, "", "") == SALVOU


def test_ainda_no_formulario_de_criacao_nao_e_resposta():
    assert situacao_pos_salvar(FORMULARIO, "", "") is None


def test_formulario_devolvido_nunca_e_sucesso():
    # O buraco que gerava o fantasma: fora de CreateFromProcesso, mas nao salvo.
    assert situacao_pos_salvar(DEVOLVIDO, "", "") is None


def test_formulario_devolvido_com_erro_e_recusa():
    assert situacao_pos_salvar(DEVOLVIDO, "", ERRO) == RECUSOU


def test_formulario_devolvido_com_aviso_pede_confirmacao():
    assert situacao_pos_salvar(DEVOLVIDO, AVISO, "") == PEDIU_CONFIRMACAO


def test_aviso_tem_prioridade_sobre_erro():
    # Com o aviso na tela o cadastro ainda pode ser gravado.
    assert situacao_pos_salvar(DEVOLVIDO, AVISO, ERRO) == PEDIU_CONFIRMACAO


@pytest.mark.parametrize("url", [
    DEVOLVIDO + "/",
    DEVOLVIDO + "?returnUrl=%2Fprocessos",
    BASE + "/processos/Tarefas/EDIT",
])
def test_endereco_do_formulario_devolvido_e_reconhecido_em_variacoes(url):
    assert situacao_pos_salvar(url, "", "") is None


def test_aviso_real_contem_o_trecho_de_data_passada():
    assert legalone.TRECHO_AVISO_DATA_PASSADA in AVISO
