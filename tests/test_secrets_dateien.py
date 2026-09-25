"""Stolperdraht Nr. 2: Geheime *Dateien* kommen nicht ins Repo, die Vorlage `.env.example` bleibt leer.

Warum es diesen Test gibt
-------------------------
Das Repo ist seit dem 25.09.2026 öffentlich. `tests/test_keine_secrets.py` liest den **Inhalt** jeder getrackten
Datei und sucht nach Token-Mustern. Drei Lücken bleiben dabei:

- Eine geheime Datei kann ein Format haben, das kein Muster kennt: ein TikTok-Token-JSON, ein Zertifikat im
  Binärformat `.p12`, ein Schlüssel, dessen Kopfzeile fehlt. Ihr **Name** verrät sie trotzdem.
- `.gitignore` schützt nur, was darin steht. `tiktok.json` steht nicht darin (im Betrieb liegt die Datei unter
  /var/lib/clip-pipeline, Spec §7.2). Eine Kopie im Repo-Ordner wäre mit `git add -A` sofort committet – deshalb
  schaut Regel 1 auch auf neue, noch nicht committete Dateien.
- `.env.example` ist die Vorlage für `.env` und wird committet. Trägt dort jemand „nur kurz zum Testen“ einen
  echten Wert ein, ist er öffentlich.

Die Idee in Alltagssprache: test_keine_secrets prüft, *was in* den Briefen steht. Dieser Test prüft, *welche
Umschläge* überhaupt in den Briefkasten dürfen – und ob das Schild „nicht einwerfen“ (.gitignore) am richtigen
Kasten hängt und auch wirkt.

Vier Regeln, jede als eigene, benannte Prüfung:

1. `verbotene_dateien`: Kein Dateiname aus VERBOTEN unter den Dateien, die `git add -A` committen würde.
2. `env_vorlage_befunde`: In `.env.example` ist jede aktive Zeile eine leere Zuweisung `NAME=`.
3. `fehlende_gitignore_eintraege`: `.gitignore` nennt die vier Pfade aus PFLICHT_IGNORIERT.
4. `ist_ignoriert`: git bestätigt, dass diese Einträge auch **wirken** (eine spätere `!`-Zeile könnte sie
   aufheben) und dass die Beispiel-Dateien *nicht* ignoriert sind – die müssen ja ins Repo.

Jede Regel hat einen Negativfall mit künstlich zusammengesetzten Werten, damit man sieht, dass sie anschlägt. Die
Befunde nennen nur Pfad, Zeilennummer, Variablenname und Regel – nie einen Wert. So landet ein echter Token nie in
einer Testausgabe.

Plan Stufe 1, Paket (f); Startauftrag §4 („Keine Secrets“) und §6 Regel 4.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path, PurePosixPath

WURZEL = Path(__file__).resolve().parents[1]


# --- Regel 1: Dateien, die nie committet werden dürfen ---------------------------------------------------------
#
# Jede Regel bekommt nur den Dateinamen (letzter Pfadteil) und sagt „verboten“ oder nicht. Bewusst über den Namen
# und nicht über den ganzen Pfad: Eine Kopie von lokal.toml im Repo-Wurzelordner ist genauso privat wie die in
# config/ – .gitignore deckt aber nur config/lokal.toml ab.

def _ist_env_datei(name: str) -> bool:
    # .env hält die echten Tokens (konfig.lade_env liest sie). Wie in .gitignore („.env“, „*.env“) plus
    # „.env.<irgendwas>“ (z. B. .env.local, .env.alt): eine umbenannte Kopie ist genauso geheim. Ausnahme ist nur
    # die leere Vorlage, die Regel 2 prüft.
    return name != ".env.example" and (name.endswith(".env") or name.startswith(".env."))


# Private SSH-Schlüssel haben keine Endung, am Namen erkennt man sie nur an bekannten Namen:
# - Standardnamen von ssh-keygen; „_sk“ sind die Varianten für Hardware-Schlüssel (FIDO). Nicht einfach „id_*“ –
#   das träfe auch Code wie id_zuordnung.py.
# - die zwei Schlüssel, die unsere Anleitungen erzeugen lassen: n8n_pipeline (docs/SERVER.md, B5) und pve-big
#   (docs/ABSCHLUSSBERICHT.md, „Nötige Host-Änderungen“, Schritt 3). Wer ssh-keygen versehentlich im Repo-Ordner
#   aufruft, hat sie dort liegen.
# Der öffentliche Teil (.pub) darf veröffentlicht werden – fullmatch lässt ihn deshalb durch.
SSH_SCHLUESSEL = re.compile(r"id_(?:rsa|dsa|ecdsa|ed25519)(?:_sk)?|n8n_pipeline|pve-big")

# Endungen von Schlüssel- und Zertifikatsdateien (PEM-Text, roher Schlüssel, PKCS#12 unter zwei Namen). Die
# Binärformate .p12/.pfx sieht test_keine_secrets gar nicht – es überspringt Binärdateien.
SCHLUESSEL_ENDUNGEN = {".pem", ".key", ".p12", ".pfx"}

# Regelname -> Prüfung auf den Dateinamen. Die Reihenfolge ist die, in der geprüft wird; der erste Treffer zählt.
VERBOTEN = {
    "Umgebungsdatei (.env)": _ist_env_datei,
    # Einstellungen dieses Rechners, z. B. Heimnetz-Adresse und MAC von pve-big; die Vorlage heißt
    # lokal.beispiel.toml und darf ins Repo
    "Lokale Konfig (lokal.toml)": lambda name: name == "lokal.toml",
    # Konfig des Gaming-PCs mit WebhookToken (Header X-Pipeline-Token für n8n) und MAC von pve-big; die Vorlage
    # heißt uebertragung.beispiel.psd1 und darf ins Repo
    "Windows-Konfig (uebertragung.psd1)": lambda name: name == "uebertragung.psd1",
    # Zugangs- und Refresh-Token der TikTok-API (Spec §7.2); steht (noch) nicht in .gitignore
    "TikTok-Tokens (tiktok.json)": lambda name: name == "tiktok.json",
    # Ohne Groß-/Kleinschreibung: Windows unterscheidet sie nicht, eine server.KEY ist genauso ein Schlüssel
    "Schlüssel oder Zertifikat": lambda name: PurePosixPath(name).suffix.lower() in SCHLUESSEL_ENDUNGEN,
    "Privater SSH-Schlüssel": lambda name: SSH_SCHLUESSEL.fullmatch(name) is not None,
}


def verbotene_dateien(pfade: list[str]) -> list[tuple[str, str]]:
    """Welche dieser Pfade dürfen nie ins Repo – und nach welcher Regel?

    `pfade` sind relativ zur Repo-Wurzel mit „/“ (so, wie `git ls-files` sie liefert). Rückgabe: je auffälliger
    Datei ein Paar (Pfad, Regelname) aus VERBOTEN, in der Reihenfolge der Eingabe; leer heißt „alles in Ordnung“.
    Es wird nur der Name angeschaut, keine Datei geöffnet.

    Beispiel: ["README.md", "daten/tiktok.json"] -> [("daten/tiktok.json", "TikTok-Tokens (tiktok.json)")]
    """
    befunde = []
    for pfad in pfade:
        name = PurePosixPath(pfad).name
        for regel, trifft in VERBOTEN.items():
            if trifft(name):
                befunde.append((pfad, regel))
                break  # eine Regel je Datei reicht, um sie anzuhalten
    return befunde


def commit_kandidaten(wurzel: Path = WURZEL) -> list[str]:
    """Alle Dateien, die `git add -A` jetzt committen würde: schon getrackte plus neue, die .gitignore nicht
    ausschließt. Relativ zur Wurzel, mit „/“.

    Warum auch die neuen? .gitignore schützt nur, was darin steht – liegt z. B. eine tiktok.json im Repo-Ordner,
    ist sie einen `git add -A` vom öffentlichen Repo entfernt. Der Test soll anschlagen, *bevor* das passiert.
    Ignorierte Dateien (.env, config/lokal.toml, .venv/ …) tauchen hier nicht auf.

    Fehler: ohne git oder außerhalb eines Checkouts OSError bzw. CalledProcessError (der Test überspringt dann).
    """
    # --cached: getrackt · --others: neu · --exclude-standard: dabei .gitignore beachten · -z: Namen mit
    # Leerzeichen oder Umlauten kommen unverändert (ohne Anführungszeichen) zurück, getrennt durch \0
    ergebnis = subprocess.run(
        ["git", "-C", str(wurzel), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True, check=True)
    return [name for name in ergebnis.stdout.decode("utf-8", "replace").split("\0") if name]


# --- Regel 2: .env.example bleibt eine leere Vorlage -----------------------------------------------------------

# Eine Zuweisung, wie konfig.lade_env sie liest: NAME=Wert, Leerzeichen um das „=“ erlaubt. Der Name wie bei
# Umgebungsvariablen (Buchstabe oder _ vorne), damit „export NAME=“ nicht als Zuweisung durchgeht – lade_env
# würde daraus die Variable „export NAME“ machen, also eine nutzlose Zeile.
ZUWEISUNG = re.compile(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=(?P<wert>.*)")


def env_vorlage_befunde(text: str) -> list[tuple[int, str]]:
    """Prüft den Text einer `.env.example`: Jede aktive Zeile muss eine **leere** Zuweisung `NAME=` sein.

    Übersprungen werden Leerzeilen und Kommentare (auch eingerückte und auskommentierte Beispiele wie
    „# CLIP_KONFIG=config/pipeline.toml“) – genau wie konfig.lade_env. Als leer gilt ein Wert, der nach dem
    Entfernen von Leerzeichen und Anführungszeichen nichts mehr enthält (`NAME=""` ist also leer).

    Rückgabe: Liste (Zeilennummer, Befund), leer heißt „Vorlage ist sauber“. Der Befund nennt nur den Namen,
    nie den Wert – damit ein versehentlich eingetragener Token nicht auch noch in der Testausgabe steht.

    Beispiel: "A=\\nB=geheim\\n# C=x" -> [(2, "B hat einen Wert")]
    """
    befunde = []
    for nr, zeile in enumerate(text.splitlines(), 1):
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#"):
            continue
        zuweisung = ZUWEISUNG.fullmatch(zeile)
        if zuweisung is None:
            # Regel 2a: Eine Vorlage enthält nur Zuweisungen. Alles andere ist ein Tippfehler oder ein
            # eingefügter nackter Wert – beides gehört nicht in eine öffentliche Datei.
            befunde.append((nr, "keine Zeile der Form NAME="))
        elif zuweisung["wert"].strip().strip('"').strip("'"):
            # Regel 2b: Der Wert bleibt leer. Echte Werte stehen nur in .env (steht in .gitignore).
            befunde.append((nr, f"{zuweisung['name']} hat einen Wert"))
    return befunde


# --- Regel 3 und 4: .gitignore nennt die geheimen Pfade – und git hält sich daran ------------------------------

# Die vier Einträge, die .gitignore wörtlich enthalten muss (jeweils als eigene Zeile):
PFLICHT_IGNORIERT = (
    ".env",                        # Tokens für beide Bots (später auch TikTok, Spec §7.2)
    "config/lokal.toml",           # Einstellungen dieses Rechners, z. B. Heimnetz-Adresse und MAC von pve-big
    "windows/uebertragung.psd1",   # Konfig des Gaming-PCs mit Webhook-Token
    ".claude/",                    # Worktrees und lokale Einstellungen von Claude Code/Agenten
)


def fehlende_gitignore_eintraege(text: str) -> list[str]:
    """Welche Einträge aus PFLICHT_IGNORIERT fehlen im Text einer `.gitignore`?

    Ein Eintrag zählt nur als ganze Zeile (Leerzeichen am Rand egal); eine auskommentierte Zeile
    „# config/lokal.toml“ oder ein engerer Eintrag „.claude/settings.json“ zählt nicht. Rückgabe in der
    Reihenfolge von PFLICHT_IGNORIERT, leer heißt „alles da“.

    Ob die Einträge auch wirken, prüft diese Funktion nicht – eine spätere Zeile „!.env“ höbe sie auf. Das
    beantwortet git selbst, siehe `ist_ignoriert`.
    """
    zeilen = {zeile.strip() for zeile in text.splitlines()}
    return [eintrag for eintrag in PFLICHT_IGNORIERT if eintrag not in zeilen]


def ist_ignoriert(pfad: str, wurzel: Path = WURZEL) -> bool:
    """Würde git diesen Pfad (relativ zu `wurzel`) wegen .gitignore ignorieren? Die Datei muss nicht existieren.

    Fragt git selbst (`git check-ignore`) statt die Regeln nachzubauen – git kennt alle Feinheiten (Sterne,
    „!“-Ausnahmen, Ordner-Regeln). Zwei Schalter machen die Antwort unabhängig vom Rechner:
    - `--no-index`: auch dann die Regeln anwenden, wenn die Datei schon getrackt ist (sonst sagt git dort
      immer „nicht ignoriert“);
    - `core.excludesFile=` (leer): die persönliche, globale Ignore-Liste des Benutzers nicht mitzählen – sonst
      könnte der Test bei Florian grün sein, obwohl die .gitignore im Repo lückenhaft ist.
    Die Liste `.git/info/exclude` dieses Checkouts zählt weiter mit; dass die Pflicht-Einträge in der
    .gitignore selbst stehen (und damit mit jedem Klon mitkommen), prüft deshalb zusätzlich Regel 3.

    Fehler: außerhalb eines git-Repos oder ohne git OSError bzw. CalledProcessError.
    """
    ergebnis = subprocess.run(
        ["git", "-C", str(wurzel), "-c", "core.excludesFile=", "check-ignore", "--no-index", "-q", pfad],
        capture_output=True)
    # Exit-Codes von git check-ignore: 0 = ignoriert, 1 = nicht ignoriert, alles andere (128) = Fehler
    if ergebnis.returncode not in (0, 1):
        raise subprocess.CalledProcessError(ergebnis.returncode, ergebnis.args, ergebnis.stdout, ergebnis.stderr)
    return ergebnis.returncode == 0


# --- Tests -----------------------------------------------------------------------------------------------------

def _kuenstlicher_telegram_token() -> str:
    """Hat die Form eines Bot-Tokens, ist aber erfunden und wird erst zur Laufzeit zusammengesetzt – so enthält
    diese Datei selbst keinen „echten“ Wert, und test_keine_secrets schlägt hier nicht an."""
    return "123456789:" + "AA" + "Qx7vB2kLm9Np4Rs8Tu1Vw3Yz5Ab6Cd0Ef"


class Regel1VerboteneDateien(unittest.TestCase):
    def test_jede_regel_schlaegt_an(self):
        pfade = [
            ".env",
            "deploy/produktion.env",
            ".env.local",
            "config/lokal.toml",
            "lokal.toml",
            "windows/uebertragung.psd1",
            "tiktok.json",
            "daten/tiktok.json",
            "zertifikate/server.pem",
            "zertifikate/server.KEY",
            "zertifikate/client.p12",
            "zertifikate/client.pfx",
            "id_ed25519",
            "id_ecdsa_sk",
            "n8n_pipeline",
            "deploy/pve-big",
        ]
        regeln = dict(verbotene_dateien(pfade))
        self.assertEqual(sorted(regeln), sorted(pfade), "jeder dieser Pfade muss auffallen")
        self.assertEqual(regeln["deploy/produktion.env"], "Umgebungsdatei (.env)")
        self.assertEqual(regeln["lokal.toml"], "Lokale Konfig (lokal.toml)")
        self.assertEqual(regeln["windows/uebertragung.psd1"], "Windows-Konfig (uebertragung.psd1)")
        self.assertEqual(regeln["daten/tiktok.json"], "TikTok-Tokens (tiktok.json)")
        self.assertEqual(regeln["zertifikate/server.KEY"], "Schlüssel oder Zertifikat")
        self.assertEqual(regeln["n8n_pipeline"], "Privater SSH-Schlüssel")

    def test_vorlagen_und_harmlose_namen_bleiben_erlaubt(self):
        erlaubt = [
            ".env.example",
            "config/lokal.beispiel.toml",
            "windows/uebertragung.beispiel.psd1",
            "id_ed25519.pub",                    # öffentlicher Teil darf veröffentlicht werden
            "n8n_pipeline.pub",
            "src/clip_pipeline/id_zuordnung.py",  # beginnt mit „id_“, ist aber Code (darum kein Muster „id_*“)
            "deploy/pve-mini/rette-clips-start.sh",
            "docs/tiktok.md",
            "src/clip_pipeline/schemas/publikum.schema.json",
        ]
        self.assertEqual(verbotene_dateien(erlaubt), [])

    def test_keine_verbotenen_dateien_im_repo(self):
        try:
            kandidaten = commit_kandidaten()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("kein git-Checkout")
        self.assertIn(".env.example", kandidaten, "git ls-files liefert nicht die erwartete Dateiliste")
        self.assertEqual(verbotene_dateien(kandidaten), [],
                         "Geheime Datei im Repo oder kurz davor – aus git entfernen (git rm --cached) bzw. "
                         "außerhalb des Repo-Ordners ablegen")


class Regel2EnvVorlage(unittest.TestCase):
    def test_leere_vorlage_ist_in_ordnung(self):
        text = (
            "# Kopiere diese Datei nach .env\n"
            "\n"
            "TELEGRAM_BOT_TOKEN=\n"
            "LEARN_BOT_TOKEN=\"\"\n"                 # leere Anführungszeichen sind auch leer (wie konfig.lade_env)
            "  TELEGRAM_ALLOWED_USER_ID =  \n"
            "# CLIP_KONFIG=config/pipeline.toml\n"  # auskommentiertes Beispiel mit Wert: erlaubt
            "   # eingerückter Kommentar mit = und Wert\n"
        )
        self.assertEqual(env_vorlage_befunde(text), [])

    def test_eingetragener_wert_faellt_auf_ohne_ihn_zu_zeigen(self):
        erfunden = _kuenstlicher_telegram_token()
        text = "# Vorlage\nTELEGRAM_BOT_TOKEN=" + erfunden + "\nTELEGRAM_ALLOWED_USER_ID='4711'\n"
        befunde = env_vorlage_befunde(text)
        self.assertEqual(befunde, [(2, "TELEGRAM_BOT_TOKEN hat einen Wert"),
                                   (3, "TELEGRAM_ALLOWED_USER_ID hat einen Wert")])
        self.assertNotIn(erfunden, repr(befunde))
        self.assertNotIn("4711", repr(befunde))

    def test_zeile_ohne_zuweisung_faellt_auf(self):
        erfunden = _kuenstlicher_telegram_token()
        # Ein eingefügter nackter Wert oder „export“ (liest konfig.lade_env nicht richtig) hat in der Vorlage
        # nichts verloren.
        befunde = env_vorlage_befunde(erfunden + "\nexport LEARN_BOT_TOKEN=\n")
        self.assertEqual(befunde, [(1, "keine Zeile der Form NAME="), (2, "keine Zeile der Form NAME=")])
        self.assertNotIn(erfunden, repr(befunde))

    def test_env_example_im_repo_ist_leer(self):
        vorlage = WURZEL / ".env.example"
        self.assertTrue(vorlage.is_file(), ".env.example fehlt (CLAUDE.md: wir liefern eine Vorlage)")
        self.assertEqual(env_vorlage_befunde(vorlage.read_text(encoding="utf-8")), [],
                         "In .env.example steht ein Wert – echte Werte gehören nur in .env")


class Regel3GitignoreNenntDiePfade(unittest.TestCase):
    def test_vollstaendige_liste(self):
        text = "# Geheimnisse\n.env\n*.env\n!.env.example\nconfig/lokal.toml\nwindows/uebertragung.psd1\n.claude/\n"
        self.assertEqual(fehlende_gitignore_eintraege(text), [])

    def test_fehlender_und_auskommentierter_eintrag_fallen_auf(self):
        text = ".env\n# config/lokal.toml\nwindows/uebertragung.psd1\n.claude/settings.json\n"
        self.assertEqual(fehlende_gitignore_eintraege(text), ["config/lokal.toml", ".claude/"])

    def test_gitignore_im_repo(self):
        text = (WURZEL / ".gitignore").read_text(encoding="utf-8")
        self.assertEqual(fehlende_gitignore_eintraege(text), [])


class Regel4GitignoreWirkt(unittest.TestCase):
    # Je Pflicht-Eintrag ein Beispielpfad, den git ignorieren muss …
    IGNORIERT = (".env", "deploy/produktion.env", "config/lokal.toml", "windows/uebertragung.psd1",
                 ".claude/settings.local.json")
    # … und die Vorlagen, die git nicht ignorieren darf (sonst fehlen sie nach einem git clone)
    NICHT_IGNORIERT = (".env.example", "config/lokal.beispiel.toml", "windows/uebertragung.beispiel.psd1")

    def test_git_haelt_sich_an_die_eintraege(self):
        for pfad in self.IGNORIERT + self.NICHT_IGNORIERT:
            with self.subTest(pfad=pfad):
                try:
                    ergebnis = ist_ignoriert(pfad)
                except (OSError, subprocess.CalledProcessError):
                    self.skipTest("kein git-Checkout")
                self.assertEqual(ergebnis, pfad in self.IGNORIERT)

    def test_spaetere_ausnahme_hebt_den_schutz_auf(self):
        """Negativfall in einem Wegwerf-Repo: `.env` steht drin, eine spätere `!.env`-Zeile hebt es wieder auf.
        Regel 3 (steht drin?) wäre zufrieden – erst Regel 4 (wirkt es?) merkt es."""
        with tempfile.TemporaryDirectory() as ordner:
            wurzel = Path(ordner)
            try:
                subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q", str(wurzel)],
                               check=True, capture_output=True)
            except (OSError, subprocess.CalledProcessError):
                self.skipTest("git fehlt")
            gitignore = ".env\nconfig/lokal.toml\nwindows/uebertragung.psd1\n.claude/\n!.env\n"
            (wurzel / ".gitignore").write_text(gitignore, encoding="utf-8")
            self.assertEqual(fehlende_gitignore_eintraege(gitignore), [])
            self.assertFalse(ist_ignoriert(".env", wurzel))
            self.assertTrue(ist_ignoriert("config/lokal.toml", wurzel))


if __name__ == "__main__":
    unittest.main()
