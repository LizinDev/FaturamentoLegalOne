"""Casa o tipo e o responsavel pedidos com o que existe no Legal One.

Separado do Selenium, como interpretar_busca: e aqui que se decide em qual tipo
e para qual pessoa a tarefa vai ser cadastrada, e isso precisa ser testavel com
as listas na mao. O legalone.py so busca as listas; a decisao e daqui.

A regra geral e nunca chutar. Um tipo ou responsavel que nao casa com
exatamente uma opcao para a rodada antes do primeiro cadastro, com a lista do
que existe — errar aqui criaria centenas de tarefas no tipo ou na pessoa
errada, e desfazer isso e trabalho manual.
"""
import dataclasses
import re
import unicodedata

# Como o proprio Legal One escreve o caminho de um subtipo ("Diversos / Contato
# Telefonico"): e o hierarchySeparator do lookup de tipo.
SEPARADOR = " / "

# Quantas opcoes mostrar quando o pedido e ambiguo ou nao existe.
MAX_SUGESTOES = 8


class NaoResolvido(ValueError):
    """O pedido nao casa com exatamente uma opcao do Legal One."""


@dataclasses.dataclass(frozen=True)
class Tipo:
    id: str        # "tipo_4" (tipo) ou "subtipo_9" (subtipo)
    caminho: str   # "Diversos" ou "Diversos / Contato Telefônico"
    nome: str      # so a ultima parte: "Contato Telefônico"
    pai: str | None = None  # id do tipo pai, para expandir a arvore


def normalizar(texto: str) -> str:
    """Forma de comparacao: sem acento, sem caixa, com espacos colapsados.

    A arvore tem nomes com espaco duplo ("[BOT CIVEL]  CONTESTACAO") e a busca
    de usuarios do Legal One ja ignora acento ("Araujo" acha "Araújo"); comparar
    o texto cru faria a checagem recusar o que o proprio sistema aceita.
    """
    sem_acento = unicodedata.normalize("NFKD", str(texto))
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return " ".join(sem_acento.split()).casefold()


def _normalizar_caminho(pedido: str) -> str:
    # ">" e aceito como separador por ser o que se costuma escrever a mao. A
    # barra nao da para quebrar: ha nomes com barra ("Agravo em REsp / Rext"),
    # por isso o caminho e sempre comparado inteiro.
    return normalizar(re.sub(r"\s*>\s*", SEPARADOR, str(pedido)))


def tipos_da_arvore(linhas: list[dict]) -> list[Tipo]:
    """Converte as linhas do LookupTreeTiposTarefa em Tipos."""
    return [
        Tipo(id=str(linha["Id"]), caminho=str(linha["Path"]),
             nome=str(linha["Value"]), pai=linha.get("ParentId") or None)
        for linha in linhas
    ]


def _lista(opcoes: list[str]) -> str:
    mostradas = "; ".join(repr(o) for o in opcoes[:MAX_SUGESTOES])
    resto = len(opcoes) - MAX_SUGESTOES
    return mostradas + (f" (e mais {resto})" if resto > 0 else "")


def resolver_tipo(pedido: str, tipos: list[Tipo]) -> Tipo:
    """O tipo pedido, pelo caminho inteiro ou pelo nome, se ele for unico.

    "Diversos" e "Diversos / Contato Telefônico" sao caminhos. "Contato
    Telefônico" sozinho tambem vale, porque so existe um; ja "Audiencia"
    aparece sob mais de dez pais, e ai o pedido volta com os caminhos.
    """
    alvo = _normalizar_caminho(pedido)
    if not alvo:
        raise NaoResolvido("tipo vazio")

    por_caminho = [t for t in tipos if normalizar(t.caminho) == alvo]
    if len(por_caminho) == 1:
        return por_caminho[0]

    por_nome = [t for t in tipos if normalizar(t.nome) == alvo]
    if len(por_nome) == 1:
        return por_nome[0]
    if len(por_nome) > 1:
        raise NaoResolvido(
            f"tipo {pedido!r} existe em mais de um lugar; escreva o caminho: "
            + _lista([t.caminho for t in por_nome])
        )

    parecidos = _parecidos(alvo, tipos)
    raise NaoResolvido(
        f"tipo {pedido!r} nao existe no Legal One"
        + (f". Parecidos: {_lista(parecidos)}" if parecidos else "")
    )


def _parecidos(alvo: str, tipos: list[Tipo]) -> list[str]:
    """Caminhos que tem palavras do pedido, os que casam mais primeiro.

    Por palavra, e nao pelo pedido inteiro: quem erra um acento ou um pedaco do
    caminho ("Prazos / Apelacao") ainda ve onde o tipo mora.
    """
    palavras = {p for p in re.findall(r"\w+", alvo) if len(p) >= 4}
    pontos = [(sum(p in normalizar(t.caminho) for p in palavras), t.caminho)
              for t in tipos]
    return [c for n, c in sorted(pontos, key=lambda x: -x[0]) if n > 0]


def resolver_usuario(pedido: str, nomes: list[str]) -> str:
    """O nome exato do usuario pedido, entre os que a busca devolveu.

    Vale o nome igual (sem ligar para acento e caixa) ou, na falta dele, o
    unico resultado da busca — "Heloiza" acha "Heloiza Helena de Araujo". Se
    um dia entrar outra Heloiza, o pedido passa a ser ambiguo e a rodada para
    com a lista, em vez de escolher uma das duas.
    """
    alvo = normalizar(pedido)
    if not alvo:
        raise NaoResolvido("responsavel vazio")
    unicos = list(dict.fromkeys(nomes))

    iguais = [n for n in unicos if normalizar(n) == alvo]
    if len(iguais) == 1:
        return iguais[0]
    if len(unicos) == 1 and not iguais:
        return unicos[0]
    if not unicos:
        raise NaoResolvido(
            f"responsavel {pedido!r} nao e usuario ativo do Legal One"
        )
    raise NaoResolvido(
        f"responsavel {pedido!r} casa com mais de um usuario; escreva o nome "
        f"completo: {_lista(unicos)}"
    )
