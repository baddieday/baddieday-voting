"""Künstliches Material für Regisseur-Tests: Momente mit Stimmung, Videos, Klick-Musik."""

import json
import subprocess
from pathlib import Path

from clip_pipeline import musik
from clip_pipeline.zeit import iso, jetzt

from tests.hilfen import MitSpeicher, testvideo

# (Stimmung, Kills in der Serie, Kill-Sekunden im Moment, Match)
MOMENTE = [
    ("episch", 4, [6.0, 8.0, 10.0, 12.5], "m1"), ("episch", 3, [5.0, 7.0, 9.0], "m2"),
    ("spannend", 2, [7.0, 9.5], "m1"), ("spannend", 1, [8.0], "m3"), ("spannend", 2, [6.0, 10.0], "m2"),
    ("lustig", 0, [], "m3"), ("lustig", 1, [9.0], "m4"), ("chill", 0, [], "m4"),
    ("frustriert", 0, [], "m5"), ("frustriert", 1, [5.0], "m5"), ("spannend", 1, [11.0], "m6"),
    ("episch", 2, [6.0, 8.0], "m6"), ("lustig", 0, [], "m7"), ("spannend", 1, [7.0], "m7"),
    ("chill", 0, [], "m8"), ("spannend", 2, [8.0, 12.0], "m8"),
]


def klick_musik(ziel: Path, bpm: float, dauer: float) -> Path:
    ziel.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"sine=f=880:d={dauer},volume='if(lt(mod(t\\,{60 / bpm})\\,0.03)\\,1\\,0)':eval=frame",
                    "-f", "lavfi", "-i", f"anoisesrc=a=0.01:d={dauer}", "-filter_complex", "amix=inputs=2",
                    "-c:a", "libmp3lame", "-q:a", "6", str(ziel)], check=True)
    return ziel


class MitRegieMaterial(MitSpeicher):
    DAUER = 20.0

    def setUp(self):
        super().setUp()
        self.konfig.daten["musik"]["ordner"] = str(self.tmp / "musik")
        self.konfig.daten.setdefault("regie", {})["ordner"] = str(self.tmp / "regie")

    def momente_anlegen(self, momente=MOMENTE, *, video_mit_ton: int = 2) -> list[int]:
        ids = []
        for i, (stimmung, serie, kills, match) in enumerate(momente, 1):
            datei = testvideo(self.tmp / "momente" / f"{i:02d}.mp4", dauer=self.DAUER, tonspuren=video_mit_ton)
            mk = {"kills": len(kills), "max_gruppe": serie, "kill_sekunden": kills, "spitzen": len(kills),
                  "jubel_laut": 0, "tod_sekunde": 4.0 if stimmung == "frustriert" else None}
            cur = self.con.execute(
                """INSERT INTO momente (schluessel, match_id, datei, start_s, ende_s, kills, stimmung, sicherheit,
                                        quelle, merkmale, erstellt, geaendert)
                   VALUES (?, ?, ?, 0, ?, ?, ?, 0.8, 'regel', ?, ?, ?)""",
                (f"datei:{i}", match, str(datei), self.DAUER, len(kills), stimmung, json.dumps(mk), iso(jetzt()), iso(jetzt())))
            ids.append(cur.lastrowid)
        return ids

    def musik_anlegen(self, bpm: float, stimmung: str, dauer: float = 330, name: str | None = None):
        datei = klick_musik(self.tmp / "roh" / f"{name or stimmung}.mp3", bpm, dauer)
        return musik.hinzufuegen(self.con, self.konfig, datei, titel=name or f"Klick {bpm}", kuenstler="Test",
                                 quelle="CC0 Testton", stimmung=stimmung)
