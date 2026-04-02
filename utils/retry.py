"""
============================================================
Módulo de Retry com Backoff Exponencial — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-04-02
Descrição: Utilitário para calcular tempo de espera crescente
           entre tentativas de retry. Evita sobrecarga nas
           APIs externas em momentos de instabilidade.
============================================================
"""


def calcular_backoff(tentativa: int, base: int = 30, multiplicador: int = 2, maximo: int = 300) -> int:
    """
    Calcula o tempo de espera com backoff exponencial.

    Progressão com valores padrão:
      Tentativa 1 → 30s
      Tentativa 2 → 60s
      Tentativa 3 → 120s
      Tentativa 4 → 240s
      Tentativa 5+ → 300s (cap)

    Args:
        tentativa:    Número da tentativa atual (começa em 1)
        base:         Tempo base em segundos (padrão: 30)
        multiplicador: Fator de crescimento (padrão: 2)
        maximo:       Tempo máximo de espera em segundos (padrão: 300)

    Returns:
        Tempo de espera em segundos
    """
    return min(base * (multiplicador ** (tentativa - 1)), maximo)
