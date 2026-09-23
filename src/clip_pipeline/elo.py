"""Elo für Clip-Battles.

Port von "Variante A" aus clips_voter (src/community_clip_os.py, apply_pairwise_elo):
  Erwartung  E_A = 1 / (1 + 10^((R_B − R_A) / 400))
  Neu        R_A' = R_A + K · (S_A − E_A)      S_A = 1 Sieg, 0 Niederlage
  K-Faktor   48 (unter 10 Battles), 32 (unter 25), danach 24
  Unsicherheit schrumpft je Battle um 6 % (min. 75). "Überspringen" ändert nur die Unsicherheit.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stand:
    elo: float = 1500.0
    rd: float = 350.0  # Unsicherheit ("rating deviation")
    battles: int = 0


def erwartung(elo_a: float, elo_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((elo_b - elo_a) / 400.0))


def k_faktor(battles_vorher: int) -> float:
    if battles_vorher < 10:
        return 48.0
    if battles_vorher < 25:
        return 32.0
    return 24.0


def battle(a: Stand, b: Stand, ergebnis: str, *, rd_faktor: float = 0.94, rd_min: float = 75.0) -> tuple[Stand, Stand]:
    """ergebnis: 'a' (A gewinnt), 'b' (B gewinnt) oder 's' (übersprungen)."""
    if ergebnis not in ("a", "b", "s"):
        raise ValueError(f"Unbekanntes Ergebnis {ergebnis!r}")
    rd_a = max(rd_min, round(a.rd * rd_faktor, 2))
    rd_b = max(rd_min, round(b.rd * rd_faktor, 2))
    if ergebnis == "s":
        return Stand(a.elo, rd_a, a.battles + 1), Stand(b.elo, rd_b, b.battles + 1)
    s_a = 1.0 if ergebnis == "a" else 0.0
    neu_a = round(a.elo + k_faktor(a.battles) * (s_a - erwartung(a.elo, b.elo)), 2)
    neu_b = round(b.elo + k_faktor(b.battles) * ((1.0 - s_a) - erwartung(b.elo, a.elo)), 2)
    return Stand(neu_a, rd_a, a.battles + 1), Stand(neu_b, rd_b, b.battles + 1)


def ranglisten_wert(elo: float, rd: float) -> float:
    """Rating minus halbe Unsicherheit: Ein einziger Glückssieg reicht nicht für Platz 1."""
    return round(elo - 0.5 * rd, 2)
