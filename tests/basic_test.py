import pytest


def somar(a, b):
    """Função de exemplo só para validar que o pytest está funcionando."""
    return a + b


class TestExemplo:
    """Testes genéricos de sanity check."""

    def test_soma_simples(self):
        assert somar(2, 3) == 5

    def test_soma_negativos(self):
        assert somar(-1, -1) == -2

    def test_soma_zero(self):
        assert somar(0, 0) == 0

    @pytest.mark.parametrize("a, b, esperado", [
        (1, 1, 2),
        (10, 20, 30),
        (-5, 5, 0),
    ])
    def test_soma_parametrizada(self, a, b, esperado):
        assert somar(a, b) == esperado