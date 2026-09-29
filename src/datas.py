"""Datas e horas da tarefa: leitura, combinacao e as regras do Legal One.

Separado do Selenium e da rodada para ser testavel com as datas na mao. E aqui
que mora o que o Legal One recusa (inicio depois da conclusao, Pendente com
conclusao no passado) e o que ele so aceita com confirmacao (data anterior a
hoje) — tudo levantado na pasta de teste em 29/09/2026.

Formatos: data "DD/MM/AAAA" e hora "HH:MM:SS", que e como o formulario as
mostra e como o ledger as guarda.
"""
import dataclasses
import datetime
import re

FORMATO_DATA = "%d/%m/%Y"
FORMATO_HORA = "%H:%M:%S"

# Duracao quando so a hora de inicio e dada. E a do proprio Legal One (o
# formulario sugere 14:00-14:30). Deixar a hora de fim que ele sugere nao serve:
# ela e calculada a partir da hora atual, e com um inicio escolhido as 16h, a
# conclusao sugerida (14:30) ficaria antes do inicio e o Salvar seria recusado.
DURACAO_PADRAO = datetime.timedelta(minutes=30)

_RE_HORA = re.compile(r"^(\d{1,2})(?::|h)(\d{2})?(?::(\d{2}))?h?$", re.IGNORECASE)


def _ler_hora(texto: str) -> str:
    m = _RE_HORA.match(texto.strip())
    if not m:
        raise ValueError(f"hora {texto!r} nao esta no formato HH:MM")
    h, mi, s = int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0)
    return datetime.time(h, mi, s).strftime(FORMATO_HORA)


def ler(valor) -> tuple[str | None, str | None]:
    """(data, hora) de uma celula ou de um texto; o que faltar vem None.

    Aceita "29/09/2026", "29/09/2026 09:00", "09:00" (so a hora: o dia fica
    sendo o do cadastro), e os tipos que o openpyxl devolve para celula de
    data e de hora. Meia-noite numa celula de data do Excel e lida como "sem
    hora": e assim que o Excel guarda uma data sem hora, e nao da para
    distinguir de uma tarefa marcada de proposito para 00:00.
    """
    if valor is None or valor == "":
        return None, None
    if isinstance(valor, datetime.datetime):
        hora = None if valor.time() == datetime.time(0) else \
            valor.time().strftime(FORMATO_HORA)
        return valor.strftime(FORMATO_DATA), hora
    if isinstance(valor, datetime.date):
        return valor.strftime(FORMATO_DATA), None
    if isinstance(valor, datetime.time):
        return None, valor.strftime(FORMATO_HORA)

    texto = " ".join(str(valor).split())
    partes = texto.split(" ")
    if (len(partes) == 1 and ":" in texto) or re.fullmatch(r"\d{1,2}h\d*", texto):
        return None, _ler_hora(texto)
    if len(partes) > 2:
        raise ValueError(f"{texto!r} nao e data (DD/MM/AAAA) nem data e hora")
    try:
        data = datetime.datetime.strptime(partes[0], FORMATO_DATA).strftime(FORMATO_DATA)
    except ValueError:
        raise ValueError(f"data {partes[0]!r} invalida (esperado DD/MM/AAAA)")
    return data, _ler_hora(partes[1]) if len(partes) == 2 else None


def ler_so_data(valor) -> str | None:
    """Data sem hora — publicacao e disponibilizacao nao tem hora no formulario."""
    data, hora = ler(valor)
    if hora is not None and data is None:
        raise ValueError(f"{valor!r} e hora; aqui vai uma data (DD/MM/AAAA)")
    if hora is not None:
        raise ValueError(f"{valor!r}: esta data nao leva hora")
    return data


@dataclasses.dataclass(frozen=True)
class Agenda:
    """As datas pedidas para a tarefa. None = nao pedido (vale o padrao).

    E o que se escreve: na linha de comando (--data, --fim...) ou nas colunas
    da planilha. O que vai ao formulario sai de resolver(), no instante do
    cadastro, porque "hoje" muda numa rodada que atravessa a meia-noite.
    """

    inicio: str | None = None
    hora_inicio: str | None = None
    fim: str | None = None
    hora_fim: str | None = None
    publicacao: str | None = None
    disponibilizacao: str | None = None

    @classmethod
    def de_textos(cls, inicio=None, fim=None, publicacao=None,
                  disponibilizacao=None) -> "Agenda":
        """Monta a partir do que foi escrito. ValueError com o campo no texto."""
        try:
            d_ini, h_ini = ler(inicio)
        except ValueError as e:
            raise ValueError(f"inicio: {e}")
        try:
            d_fim, h_fim = ler(fim)
        except ValueError as e:
            raise ValueError(f"conclusao: {e}")
        try:
            pub = ler_so_data(publicacao)
        except ValueError as e:
            raise ValueError(f"publicacao: {e}")
        try:
            disp = ler_so_data(disponibilizacao)
        except ValueError as e:
            raise ValueError(f"disponibilizacao: {e}")
        return cls(d_ini, h_ini, d_fim, h_fim, pub, disp)

    def sobre(self, outra: "Agenda") -> "Agenda":
        """Esta agenda por cima de outra: campo pedido aqui ganha, vazio herda.

        E a precedencia linha > flag: a linha da planilha e `self`, as flags da
        rodada sao `outra`.
        """
        return Agenda(*(a if a is not None else b for a, b in zip(
            dataclasses.astuple(self), dataclasses.astuple(outra), strict=True)))

    @property
    def escolhida(self) -> bool:
        """Inicio ou conclusao foram pedidos (e nao deixados para "hoje").

        So entao se confirma o aviso de data passada do Legal One: sem data
        escolhida, o aviso so aparece num processo pego pela meia-noite, e
        gravar a tarefa com a data de ontem seria erro.
        """
        return self.inicio is not None or self.fim is not None

    def resolver(self, hoje: datetime.date) -> "Datas":
        """As datas concretas de um cadastro feito em `hoje`.

        Sem inicio, e hoje. Sem conclusao, e no dia do inicio (a tarefa de um
        dia so, como sempre foi). Num dia so, a hora que faltar e completada
        com DURACAO_PADRAO a partir da outra: a que o formulario sugeriria vem
        da hora atual e pode cair do lado errado da pedida. Uma tarefa as 23h50
        sem conclusao termina no dia seguinte.
        """
        inicio = self.inicio or hoje.strftime(FORMATO_DATA)
        fim = self.fim or inicio
        hora_inicio, hora_fim = self.hora_inicio, self.hora_fim
        if fim == inicio and hora_inicio and not hora_fim:
            termino = _momento(inicio, hora_inicio) + DURACAO_PADRAO
            hora_fim = termino.strftime(FORMATO_HORA)
            if self.fim is None:
                fim = termino.strftime(FORMATO_DATA)
            elif termino.strftime(FORMATO_DATA) != fim:
                hora_fim = "23:59:00"  # conclusao pedida neste dia: fica nele
        elif fim == inicio and hora_fim and not hora_inicio:
            comeco = _momento(fim, hora_fim) - DURACAO_PADRAO
            hora_inicio = ("00:00:00" if comeco.strftime(FORMATO_DATA) != inicio
                           else comeco.strftime(FORMATO_HORA))
        return Datas(inicio, hora_inicio, fim, hora_fim,
                     self.publicacao, self.disponibilizacao)


@dataclasses.dataclass(frozen=True)
class Datas:
    """As datas de um cadastro, ja concretas. Hora None = a do formulario."""

    inicio: str
    hora_inicio: str | None
    fim: str
    hora_fim: str | None
    publicacao: str | None = None
    disponibilizacao: str | None = None

    def problemas(self, hoje: datetime.date, status: str,
                  status_recusados_no_passado: set[str]) -> list[str]:
        """O que o Legal One recusaria nestas datas, antes de tentar.

        - Inicio depois da conclusao: recusado sempre. So da para comparar a
          hora quando as duas foram pedidas; sem hora, compara so o dia.
        - Conclusao no passado com um status que o Legal One barra nesse caso
          (Pendente): "O status selecionado nao pode ser 'Pendente' quando a
          data de conclusao for anterior a data atual".
        """
        erros = []
        com_hora = bool(self.hora_inicio and self.hora_fim)
        ini = _momento(self.inicio, self.hora_inicio if com_hora else None)
        fim = _momento(self.fim, self.hora_fim if com_hora else None)
        if ini > fim:
            erros.append(f"inicio ({_texto(self.inicio, self.hora_inicio)}) depois "
                         f"da conclusao ({_texto(self.fim, self.hora_fim)})")
        if (status in status_recusados_no_passado
                and _momento(self.fim, None).date() < hoje):
            erros.append(f"status {status} com conclusao no passado ({self.fim}), "
                         f"que o Legal One nao aceita")
        return erros


def _texto(data: str, hora: str | None) -> str:
    return f"{data} {hora}" if hora else data


def _momento(data: str, hora: str | None) -> datetime.datetime:
    return datetime.datetime.strptime(f"{data} {hora or '00:00:00'}",
                                      f"{FORMATO_DATA} {FORMATO_HORA}")
