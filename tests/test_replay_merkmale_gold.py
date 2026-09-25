"""Goldtest Stufe 2, Leitplanke 1: Die Kill-Zählung bleibt, wie sie ist.

Für alle bestehenden Replay-Vorlagen der Tests (test_replay_vorbewertung, test_stimmung, test_nachschnitt) sind
Ereignisse, Titel, Gruppen, Clip-Fenster und die alten fünf Merkmale byte-gleich mit dem Stand vor Paket A2
(SOLL wurde mit Commit 0c7e583 erzeugt). Und: Sind die neuen Merkmale alle 0, liefert bewerte genau den alten Wert.
"""

import json
import unittest
from datetime import timedelta

from clip_pipeline import vorbewertung, zeitleiste
from clip_pipeline.konfig import lade
from clip_pipeline.merkmale import REPLAY_MERKMALE, MIC_MERKMALE
from clip_pipeline.replay import match_aus_json
from clip_pipeline.zeit import iso
# Modul statt Klasse importieren: sonst sammelt unittest die Testklasse Replay hier ein zweites Mal ein
from tests import test_replay_vorbewertung as alt

EINSTELLUNGEN, ICH, elim = alt.EINSTELLUNGEN, alt.ICH, alt.elim
GRUND = alt.Replay.DATEN

ALTE_FUENF = ("kill_punkte", "victory_royale", "laenge", "lautstaerke", "kommentar")


def _mit(eliminierungen, **mehr) -> dict:
    return dict(GRUND, eliminierungen=eliminierungen, **mehr)


def _nachschnitt_wipe() -> dict:
    """Wie tests/test_nachschnitt.py: Replay beginnt 300 s vor der Aufnahme, Wipe bei 10/14/25 s, Kills bei 25,2 s."""
    wipe = [(10.0, "ICH", "GEGNER-A", True), (14.0, "ICH", "GEGNER-B", True), (25.0, "ICH", "GEGNER-C", True),
            (25.2, "ICH", "GEGNER-A", False), (25.2, "ICH", "GEGNER-B", False), (25.2, "ICH", "GEGNER-C", False)]
    return {"replay_start": "2026-09-21T19:00:00.000Z", "replay_start_kind": "Utc", "laenge_ms": 1_200_000,
            "ich_quelle": "konfig", "ich": {"epic_id": "ICH", "platzierung": 4},
            "eliminierungen": [{"t_ms": round((300.0 + s) * 1000), "eliminator": t, "eliminiert": o, "knock": k,
                                "selbst": False} for s, t, o, k in wipe]}


def _stimmung(eliminierungen, platz) -> dict:
    return {"replay_start": "2026-09-21T19:00:00.000Z", "replay_start_kind": "Utc", "laenge_ms": 900000,
            "ich_quelle": "konfig", "ich": {"epic_id": "ICH", "platzierung": platz}, "eliminierungen": eliminierungen}


def _st(t, opfer, knock=False):
    return {"t_ms": round(t * 1000), "eliminator": "ICH", "eliminiert": opfer, "knock": knock}


# Alle Replay-Vorlagen der bestehenden Tests (die Grundvorlage direkt, die übrigen als Kopie der Inline-Vorlagen)
VORLAGEN = {
    "grund": GRUND,
    "umhaut": _mit([
        elim(100, ICH, "A", knock=True), elim(104, "TEAM", "A"),
        elim(200, "TEAM", "B", knock=True), elim(203, ICH, "B"),
        elim(300, ICH, "C", knock=True), elim(302, ICH, "C"),
        elim(400, ICH, "D"),
        elim(500, ICH, "E", knock=True), elim(510, "E", "E", selbst=True),
        elim(600, "X", "F", selbst=True),
        elim(700, ICH, "G", knock=True), elim(900, "Y", "G"),
    ], stats_eliminierungen=4),
    "wipe": _mit([
        elim(185.0, ICH, "OPFER-1", knock=True), elim(195.4, ICH, "OPFER-2", knock=True),
        elim(200.0, ICH, "OPFER-3", knock=True),
        elim(200.0, ICH, "OPFER-1"), elim(200.0, ICH, "OPFER-2"), elim(200.1, ICH, "OPFER-3"),
    ], stats_eliminierungen=3),
    "teammate": _mit([
        elim(300, ICH, "OPFER-1", knock=True), elim(304, "TEAM", "OPFER-1"),
        elim(400, ICH, "OPFER-2", knock=True), elim(495, ICH, "OPFER-2"),
        elim(600, ICH, "OPFER-3"),
    ], stats_eliminierungen=3),
    "sieg": _mit(GRUND["eliminierungen"][:4], ich={"epic_id": ICH, "player_id": ICH, "platzierung": 1}),
    "nachschnitt_wipe": _nachschnitt_wipe(),
    "stimmung_tod": _stimmung([{"t_ms": 304000, "eliminator": "GEGNER", "eliminiert": "ICH", "knock": False}], 12),
    "stimmung_wipe": _stimmung([_st(297, "OPFER-1", True), _st(302, "OPFER-2", True), _st(304, "OPFER-3", True),
                                _st(304, "OPFER-1"), _st(304, "OPFER-2"), _st(304, "OPFER-3")], 5),
}


def ergebnis(daten: dict) -> dict:
    """Alles, was sich durch Paket A2 nicht ändern darf – als JSON-fähiges Dict."""
    m = match_aus_json(daten, "m", zonen_name="Europe/Berlin")
    zl = zeitleiste.baue(m, [], m.start_utc, m.ende_utc)
    return {
        "ereignisse": [[iso(e.zeit_utc), e.art, iso(e.aktion_utc) if e.aktion_utc else None] for e in m.ereignisse],
        "warnungen": m.warnungen,
        "kandidaten": [{
            "titel": k.titel, "gruppen": [[iso(z) for z in g] for g in k.gruppen],
            "aktionen": [[iso(z) for z in g] for g in k.aktionen],
            "start": iso(k.start_utc), "ende": iso(k.ende_utc),
            "merkmale": {n: k.merkmale[n] for n in ALTE_FUENF},
        } for k in vorbewertung.kandidaten(zl, EINSTELLUNGEN)],
    }


# Erzeugt mit dem Stand vor Paket A2 (Commit 0c7e583): print(json.dumps({n: ergebnis(d) for n, d in VORLAGEN.items()}))
SOLL = json.loads(r"""{
 "grund": {
  "ereignisse": [
   [
    "2026-09-21T19:48:26.158Z",
    "knock",
    null
   ],
   [
    "2026-09-21T19:48:37.420Z",
    "kill",
    "2026-09-21T19:48:26.158Z"
   ],
   [
    "2026-09-21T19:53:24.974Z",
    "kill",
    null
   ],
   [
    "2026-09-21T19:56:21.213Z",
    "tod",
    null
   ]
  ],
  "warnungen": [],
  "kandidaten": [
   {
    "titel": "Einzelkill",
    "gruppen": [
     [
      "2026-09-21T19:48:37.420Z"
     ]
    ],
    "aktionen": [
     [
      "2026-09-21T19:48:26.158Z"
     ]
    ],
    "start": "2026-09-21T19:48:18.158Z",
    "ende": "2026-09-21T19:48:42.420Z",
    "merkmale": {
     "kill_punkte": 1.0,
     "victory_royale": 0.0,
     "laenge": 0.0,
     "lautstaerke": 0.0,
     "kommentar": 0.0
    }
   },
   {
    "titel": "Einzelkill",
    "gruppen": [
     [
      "2026-09-21T19:53:24.974Z"
     ]
    ],
    "aktionen": [
     [
      "2026-09-21T19:53:24.974Z"
     ]
    ],
    "start": "2026-09-21T19:53:16.974Z",
    "ende": "2026-09-21T19:53:29.974Z",
    "merkmale": {
     "kill_punkte": 1.0,
     "victory_royale": 0.0,
     "laenge": 0.0,
     "lautstaerke": 0.0,
     "kommentar": 0.0
    }
   }
  ]
 },
 "umhaut": {
  "ereignisse": [
   [
    "2026-09-21T19:44:02.617Z",
    "knock",
    null
   ],
   [
    "2026-09-21T19:44:02.617Z",
    "kill",
    "2026-09-21T19:44:02.617Z"
   ],
   [
    "2026-09-21T19:47:22.617Z",
    "knock",
    null
   ],
   [
    "2026-09-21T19:47:24.617Z",
    "kill",
    "2026-09-21T19:47:22.617Z"
   ],
   [
    "2026-09-21T19:49:02.617Z",
    "kill",
    null
   ],
   [
    "2026-09-21T19:50:42.617Z",
    "knock",
    null
   ],
   [
    "2026-09-21T19:50:42.617Z",
    "kill",
    "2026-09-21T19:50:42.617Z"
   ],
   [
    "2026-09-21T19:54:02.617Z",
    "knock",
    null
   ]
  ],
  "warnungen": [],
  "kandidaten": [
   {
    "titel": "Einzelkill",
    "gruppen": [
     [
      "2026-09-21T19:44:02.617Z"
     ]
    ],
    "aktionen": [
     [
      "2026-09-21T19:44:02.617Z"
     ]
    ],
    "start": "2026-09-21T19:43:54.617Z",
    "ende": "2026-09-21T19:44:07.617Z",
    "merkmale": {
     "kill_punkte": 1.0,
     "victory_royale": 0.0,
     "laenge": 0.0,
     "lautstaerke": 0.0,
     "kommentar": 0.0
    }
   },
   {
    "titel": "Einzelkill",
    "gruppen": [
     [
      "2026-09-21T19:47:24.617Z"
     ]
    ],
    "aktionen": [
     [
      "2026-09-21T19:47:22.617Z"
     ]
    ],
    "start": "2026-09-21T19:47:14.617Z",
    "ende": "2026-09-21T19:47:29.617Z",
    "merkmale": {
     "kill_punkte": 1.0,
     "victory_royale": 0.0,
     "laenge": 0.0,
     "lautstaerke": 0.0,
     "kommentar": 0.0
    }
   },
   {
    "titel": "Einzelkill",
    "gruppen": [
     [
      "2026-09-21T19:49:02.617Z"
     ]
    ],
    "aktionen": [
     [
      "2026-09-21T19:49:02.617Z"
     ]
    ],
    "start": "2026-09-21T19:48:54.617Z",
    "ende": "2026-09-21T19:49:07.617Z",
    "merkmale": {
     "kill_punkte": 1.0,
     "victory_royale": 0.0,
     "laenge": 0.0,
     "lautstaerke": 0.0,
     "kommentar": 0.0
    }
   },
   {
    "titel": "Einzelkill",
    "gruppen": [
     [
      "2026-09-21T19:50:42.617Z"
     ]
    ],
    "aktionen": [
     [
      "2026-09-21T19:50:42.617Z"
     ]
    ],
    "start": "2026-09-21T19:50:34.617Z",
    "ende": "2026-09-21T19:50:47.617Z",
    "merkmale": {
     "kill_punkte": 1.0,
     "victory_royale": 0.0,
     "laenge": 0.0,
     "lautstaerke": 0.0,
     "kommentar": 0.0
    }
   }
  ]
 },
 "wipe": {
  "ereignisse": [
   [
    "2026-09-21T19:45:27.617Z",
    "knock",
    null
   ],
   [
    "2026-09-21T19:45:38.017Z",
    "knock",
    null
   ],
   [
    "2026-09-21T19:45:42.617Z",
    "knock",
    null
   ],
   [
    "2026-09-21T19:45:42.617Z",
    "kill",
    "2026-09-21T19:45:27.617Z"
   ],
   [
    "2026-09-21T19:45:42.617Z",
    "kill",
    "2026-09-21T19:45:38.017Z"
   ],
   [
    "2026-09-21T19:45:42.717Z",
    "kill",
    "2026-09-21T19:45:42.617Z"
   ]
  ],
  "warnungen": [],
  "kandidaten": [
   {
    "titel": "Triple Kill",
    "gruppen": [
     [
      "2026-09-21T19:45:42.617Z",
      "2026-09-21T19:45:42.617Z",
      "2026-09-21T19:45:42.717Z"
     ]
    ],
    "aktionen": [
     [
      "2026-09-21T19:45:27.617Z",
      "2026-09-21T19:45:38.017Z",
      "2026-09-21T19:45:42.617Z"
     ]
    ],
    "start": "2026-09-21T19:45:19.617Z",
    "ende": "2026-09-21T19:45:47.717Z",
    "merkmale": {
     "kill_punkte": 6.0,
     "victory_royale": 0.0,
     "laenge": 0.0,
     "lautstaerke": 0.0,
     "kommentar": 0.0
    }
   }
  ]
 },
 "teammate": {
  "ereignisse": [
   [
    "2026-09-21T19:47:22.617Z",
    "knock",
    null
   ],
   [
    "2026-09-21T19:47:22.617Z",
    "kill",
    "2026-09-21T19:47:22.617Z"
   ],
   [
    "2026-09-21T19:49:02.617Z",
    "knock",
    null
   ],
   [
    "2026-09-21T19:50:37.617Z",
    "kill",
    null
   ],
   [
    "2026-09-21T19:52:22.617Z",
    "kill",
    null
   ]
  ],
  "warnungen": [],
  "kandidaten": [
   {
    "titel": "Einzelkill",
    "gruppen": [
     [
      "2026-09-21T19:47:22.617Z"
     ]
    ],
    "aktionen": [
     [
      "2026-09-21T19:47:22.617Z"
     ]
    ],
    "start": "2026-09-21T19:47:14.617Z",
    "ende": "2026-09-21T19:47:27.617Z",
    "merkmale": {
     "kill_punkte": 1.0,
     "victory_royale": 0.0,
     "laenge": 0.0,
     "lautstaerke": 0.0,
     "kommentar": 0.0
    }
   },
   {
    "titel": "Einzelkill",
    "gruppen": [
     [
      "2026-09-21T19:50:37.617Z"
     ]
    ],
    "aktionen": [
     [
      "2026-09-21T19:50:37.617Z"
     ]
    ],
    "start": "2026-09-21T19:50:29.617Z",
    "ende": "2026-09-21T19:50:42.617Z",
    "merkmale": {
     "kill_punkte": 1.0,
     "victory_royale": 0.0,
     "laenge": 0.0,
     "lautstaerke": 0.0,
     "kommentar": 0.0
    }
   },
   {
    "titel": "Einzelkill",
    "gruppen": [
     [
      "2026-09-21T19:52:22.617Z"
     ]
    ],
    "aktionen": [
     [
      "2026-09-21T19:52:22.617Z"
     ]
    ],
    "start": "2026-09-21T19:52:14.617Z",
    "ende": "2026-09-21T19:52:27.617Z",
    "merkmale": {
     "kill_punkte": 1.0,
     "victory_royale": 0.0,
     "laenge": 0.0,
     "lautstaerke": 0.0,
     "kommentar": 0.0
    }
   }
  ]
 },
 "sieg": {
  "ereignisse": [
   [
    "2026-09-21T19:48:26.158Z",
    "knock",
    null
   ],
   [
    "2026-09-21T19:48:37.420Z",
    "kill",
    "2026-09-21T19:48:26.158Z"
   ],
   [
    "2026-09-21T19:53:24.974Z",
    "kill",
    null
   ]
  ],
  "warnungen": [],
  "kandidaten": [
   {
    "titel": "Einzelkill",
    "gruppen": [
     [
      "2026-09-21T19:48:37.420Z"
     ]
    ],
    "aktionen": [
     [
      "2026-09-21T19:48:26.158Z"
     ]
    ],
    "start": "2026-09-21T19:48:18.158Z",
    "ende": "2026-09-21T19:48:42.420Z",
    "merkmale": {
     "kill_punkte": 1.0,
     "victory_royale": 0.0,
     "laenge": 0.0,
     "lautstaerke": 0.0,
     "kommentar": 0.0
    }
   },
   {
    "titel": "Einzelkill + Victory Royale",
    "gruppen": [
     [
      "2026-09-21T19:53:24.974Z"
     ]
    ],
    "aktionen": [
     [
      "2026-09-21T19:53:24.974Z"
     ]
    ],
    "start": "2026-09-21T19:53:16.974Z",
    "ende": "2026-09-21T19:53:29.974Z",
    "merkmale": {
     "kill_punkte": 1.0,
     "victory_royale": 1.0,
     "laenge": 0.0,
     "lautstaerke": 0.0,
     "kommentar": 0.0
    }
   }
  ]
 },
 "nachschnitt_wipe": {
  "ereignisse": [
   [
    "2026-09-21T19:05:10.000Z",
    "knock",
    null
   ],
   [
    "2026-09-21T19:05:14.000Z",
    "knock",
    null
   ],
   [
    "2026-09-21T19:05:25.000Z",
    "knock",
    null
   ],
   [
    "2026-09-21T19:05:25.200Z",
    "kill",
    "2026-09-21T19:05:10.000Z"
   ],
   [
    "2026-09-21T19:05:25.200Z",
    "kill",
    "2026-09-21T19:05:14.000Z"
   ],
   [
    "2026-09-21T19:05:25.200Z",
    "kill",
    "2026-09-21T19:05:25.000Z"
   ]
  ],
  "warnungen": [],
  "kandidaten": [
   {
    "titel": "Triple Kill",
    "gruppen": [
     [
      "2026-09-21T19:05:25.200Z",
      "2026-09-21T19:05:25.200Z",
      "2026-09-21T19:05:25.200Z"
     ]
    ],
    "aktionen": [
     [
      "2026-09-21T19:05:10.000Z",
      "2026-09-21T19:05:14.000Z",
      "2026-09-21T19:05:25.000Z"
     ]
    ],
    "start": "2026-09-21T19:05:02.000Z",
    "ende": "2026-09-21T19:05:30.200Z",
    "merkmale": {
     "kill_punkte": 6.0,
     "victory_royale": 0.0,
     "laenge": 0.0,
     "lautstaerke": 0.0,
     "kommentar": 0.0
    }
   }
  ]
 },
 "stimmung_tod": {
  "ereignisse": [
   [
    "2026-09-21T19:05:04.000Z",
    "tod",
    null
   ]
  ],
  "warnungen": [],
  "kandidaten": []
 },
 "stimmung_wipe": {
  "ereignisse": [
   [
    "2026-09-21T19:04:57.000Z",
    "knock",
    null
   ],
   [
    "2026-09-21T19:05:02.000Z",
    "knock",
    null
   ],
   [
    "2026-09-21T19:05:04.000Z",
    "knock",
    null
   ],
   [
    "2026-09-21T19:05:04.000Z",
    "kill",
    "2026-09-21T19:04:57.000Z"
   ],
   [
    "2026-09-21T19:05:04.000Z",
    "kill",
    "2026-09-21T19:05:02.000Z"
   ],
   [
    "2026-09-21T19:05:04.000Z",
    "kill",
    "2026-09-21T19:05:04.000Z"
   ]
  ],
  "warnungen": [],
  "kandidaten": [
   {
    "titel": "Triple Kill",
    "gruppen": [
     [
      "2026-09-21T19:05:04.000Z",
      "2026-09-21T19:05:04.000Z",
      "2026-09-21T19:05:04.000Z"
     ]
    ],
    "aktionen": [
     [
      "2026-09-21T19:04:57.000Z",
      "2026-09-21T19:05:02.000Z",
      "2026-09-21T19:05:04.000Z"
     ]
    ],
    "start": "2026-09-21T19:04:49.000Z",
    "ende": "2026-09-21T19:05:09.000Z",
    "merkmale": {
     "kill_punkte": 6.0,
     "victory_royale": 0.0,
     "laenge": 0.0,
     "lautstaerke": 0.0,
     "kommentar": 0.0
    }
   }
  ]
 }
}""")


class Goldtest(unittest.TestCase):
    def test_zaehlung_byte_gleich(self):
        for name, daten in VORLAGEN.items():
            with self.subTest(vorlage=name):
                # über JSON verglichen: „byte-gleich“ heißt auch gleiche Typen (6.0 bleibt 6.0)
                self.assertEqual(json.dumps(ergebnis(daten), sort_keys=True), json.dumps(SOLL[name], sort_keys=True))

    def test_neue_merkmale_null_aendern_bewerte_nicht(self):
        gewichte = {m: float(v) for m, v in lade().abschnitt("vorbewertung")["startgewichte"].items()}
        self.assertTrue(all(gewichte[m] != 0 for m in ("platzierung", "bot_opfer", "clutch")))  # sonst prüft das nichts
        for name, soll in SOLL.items():
            for k in soll["kandidaten"]:
                with self.subTest(vorlage=name, titel=k["titel"]):
                    alt = vorbewertung.bewerte(k["merkmale"], gewichte, k["titel"])
                    neu = dict(k["merkmale"], **dict.fromkeys(REPLAY_MERKMALE + MIC_MERKMALE, 0.0))
                    self.assertEqual(vorbewertung.bewerte(neu, gewichte, k["titel"]), alt)

    def test_neue_ereignis_felder_aendern_zeiten_nicht(self):
        # Mit waffe/eliminiert_bot/spieler_gesamt im JSON bleiben Ereignisse und Kandidaten gleich
        for name, daten in VORLAGEN.items():
            with self.subTest(vorlage=name):
                reich = dict(daten, spieler_gesamt=100, eliminierungen=[
                    dict(e, waffe=7, eliminiert_bot=True) for e in daten["eliminierungen"]])
                self.assertEqual(json.dumps(ergebnis(reich), sort_keys=True), json.dumps(SOLL[name], sort_keys=True))


if __name__ == "__main__":
    unittest.main()
