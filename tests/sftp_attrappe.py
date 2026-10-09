"""sftp-Attrappe für die Abhol-Tests (Mehrbenutzer, Stufe 2): spielt `sftp -b -` gegen einen Ordner wie das
Abholprofil des Briefkastens (internal-sftp -d /fach -R, chroot je Benutzer).

Aufruf wie das echte sftp: sftp -F /dev/null -b - -P <port> [-l kbit] -o … <benutzer>@<host>, Befehle auf stdin. Die
Ausgabe folgt dem echten OpenSSH 9.6 (geprüft mit echtem sshd): vor jedem Befehl „sftp> <befehl>“ auf stdout; `ls -1`
schreibt „<pfad>/<name>“ je Zeile; `df` zwei Zeilen in KiB; Fehler auf stderr; ein Fehler ohne „-“ vor dem Befehl
beendet den Lauf mit Exit 1, eine abgebrochene Verbindung mit Exit 255. `reget` setzt fort, auf eine vollständige
Datei ist es kein Fehler. Schreiben, Löschen, Umbenennen, Ordner anlegen: „Permission denied“ (nur lesen).

Umgebung:
  SFTP_ATTRAPPE_WURZEL     je Benutzer ein Ordner <wurzel>/<benutzer>/ (= chroot), darin fach/ und die Datei
                           .schluessel mit dem einzigen IdentityFile, das hereindarf (wie abholen/<benutzer>)
  SFTP_ATTRAPPE_ABBRUCH    so viele Bytes (über alle reget eines Aufrufs), dann bricht die Verbindung ab (Exit 255)
  SFTP_ATTRAPPE_AUS        "1" = vServer nicht erreichbar (Exit 255 wie „ssh: connect …“)
  SFTP_ATTRAPPE_DF         "groesse belegt frei" in KiB statt der Werte des Ordners
Ein Protokoll schreibt die Attrappe nicht: Der Abholer begrenzt die Größe jeder Datei, die sftp schreibt
(RLIMIT_FSIZE) – die Tests zeichnen die Aufrufe am Modul auf.
"""

from __future__ import annotations

import errno
import os
import posixpath
import shlex
import sys
from pathlib import Path

START = "/fach"         # internal-sftp -d /fach
BLOCK = 32 * 1024


class Abbruch(Exception):
    """Die Verbindung ist weg (Exit 255)."""


class Fehler(Exception):
    """Ein Befehl ist gescheitert (Exit 1, außer mit „-“ davor)."""


def installiere(ordner: Path) -> Path:
    """Ein ausführbares `sftp` in ordner, das diese Attrappe mit dem Python der Tests startet – für briefkasten.SFTP."""
    programm = Path(ordner) / "sftp"
    programm.write_text(f"#!{sys.executable}\nimport runpy\nrunpy.run_path({str(Path(__file__).resolve())!r}, "
                        "run_name='__main__')\n", encoding="utf-8")
    programm.chmod(0o755)
    return programm


def _argumente(argv: list[str]) -> tuple[dict[str, str], str, str]:
    """(Optionen -o, Benutzer, Host). Schalter mit Wert: -b -F -P -l -o -i."""
    optionen: dict[str, str] = {}
    rest: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-b", "-F", "-P", "-l", "-i") and i + 1 < len(argv):
            optionen[a] = argv[i + 1]
            i += 2
        elif a == "-o" and i + 1 < len(argv):
            schluessel, _, wert = argv[i + 1].partition("=")
            optionen[schluessel] = wert
            i += 2
        else:
            rest.append(a)
            i += 1
    ziel = rest[-1] if rest else ""
    benutzer, _, host = ziel.rpartition("@")
    return optionen, benutzer, host


class Sitzung:
    def __init__(self, chroot: Path, abbruch: int | None):
        self.chroot = chroot.resolve()
        self.abbruch = abbruch
        self.bytes = 0

    def pfad(self, entfernt: str) -> Path:
        """Pfad im chroot: absolut ab /, sonst ab /fach. „..“ endet an der Wurzel des chroot (wie beim echten)."""
        voll = posixpath.normpath(posixpath.join(START, entfernt))
        while voll.startswith("/.."):
            voll = voll[3:] or "/"
        return self.chroot / voll.lstrip("/")

    def anzeige(self, entfernt: str) -> str:
        return posixpath.normpath(posixpath.join(START, entfernt))

    def ls(self, args: list[str]) -> None:
        alle = "-a" in args
        ziele = [a for a in args if not a.startswith("-")] or ["."]
        pfad = self.pfad(ziele[0])
        if not pfad.is_dir():
            raise Fehler(f'Can\'t ls: "{self.anzeige(ziele[0])}" not found')
        namen = sorted(os.listdir(pfad), key=os.fsencode)
        if alle:
            namen = [".", "..", *namen]
        else:
            namen = [n for n in namen if not n.startswith(".")]
        vorne = ziele[0].rstrip("/")
        for n in namen:
            print(f"{vorne}/{n}" if ziele[0] != "." else n)

    def df(self) -> None:
        if wert := os.environ.get("SFTP_ATTRAPPE_DF"):
            groesse, belegt, frei = (int(x) for x in wert.split())
        else:
            st = os.statvfs(self.pfad("."))
            groesse, frei = st.f_blocks * st.f_frsize // 1024, st.f_bavail * st.f_frsize // 1024
            belegt = groesse - st.f_bfree * st.f_frsize // 1024
        prozent = f"{100 * belegt // groesse:3d}%" if groesse else "ERR"
        print("        Size         Used        Avail       (root)    %Capacity")
        print(f"{groesse:12d} {belegt:12d} {frei:12d} {frei:12d}         {prozent}")

    def hole(self, args: list[str], fortsetzen: bool) -> None:
        if len(args) != 2:
            raise Fehler("Aufruf: get entfernt lokal")
        quelle = self.pfad(args[0])
        if not quelle.is_file():
            raise Fehler(f'File "{self.anzeige(args[0])}" not found.')
        lokal = Path(args[1])
        groesse = quelle.stat().st_size
        start = 0
        if fortsetzen and lokal.exists():
            start = lokal.stat().st_size
            if start > groesse:
                raise Fehler(f'Unable to resume download of "{lokal}": local file is larger than remote')
            if start == groesse:
                print(f'File "{lokal}" was not modified', file=sys.stderr)
                return
        fd = os.open(lokal, os.O_WRONLY | os.O_CREAT | (0 if fortsetzen else os.O_TRUNC), 0o600)
        try:
            with open(quelle, "rb") as ein:
                ein.seek(start)
                stelle = start
                while block := ein.read(BLOCK):
                    if fortsetzen and self.abbruch is not None and self.bytes + len(block) > self.abbruch:
                        block = block[: max(0, self.abbruch - self.bytes)]
                        os.pwrite(fd, block, stelle)
                        raise Abbruch()
                    try:
                        os.pwrite(fd, block, stelle)
                    except OSError as e:   # RLIMIT_FSIZE des Aufrufers: wie das echte sftp scheitert nur dieser Befehl
                        if e.errno != errno.EFBIG:
                            raise
                        os.ftruncate(fd, start)
                        raise Fehler(f'write local "{lokal}": File too large') from None
                    stelle += len(block)
                    self.bytes += len(block)
        finally:
            os.close(fd)


def main() -> int:
    optionen, benutzer, host = _argumente(sys.argv[1:])
    befehle = [z.rstrip("\n") for z in sys.stdin.read().splitlines()] if optionen.get("-b") == "-" else []
    port = optionen.get("-P", "22")
    if os.environ.get("SFTP_ATTRAPPE_AUS") == "1":
        print(f"ssh: connect to host {host} port {port}: Connection timed out\r\nConnection closed", file=sys.stderr)
        return 255
    chroot = Path(os.environ["SFTP_ATTRAPPE_WURZEL"]) / benutzer
    erlaubt = chroot / ".schluessel"
    schluessel = optionen.get("IdentityFile", "")
    if (not benutzer or not erlaubt.is_file() or not schluessel
            or os.path.realpath(erlaubt.read_text(encoding="utf-8").strip()) != os.path.realpath(schluessel)
            or optionen.get("IdentitiesOnly") != "yes" or optionen.get("StrictHostKeyChecking") != "yes"):
        print(f"{benutzer}@{host}: Permission denied (publickey).\r\nConnection closed", file=sys.stderr)
        return 255
    abbruch = os.environ.get("SFTP_ATTRAPPE_ABBRUCH")
    sitzung = Sitzung(chroot, int(abbruch) if abbruch else None)
    for zeile in befehle:
        if not zeile.strip() or zeile.lstrip().startswith("#"):
            continue
        print(f"sftp> {zeile}", flush=True)
        weiter = zeile.startswith("-")
        teile = shlex.split(zeile[1:] if weiter else zeile)
        befehl, args = teile[0], teile[1:]
        try:
            if befehl == "ls":
                sitzung.ls(args)
            elif befehl == "df":
                sitzung.df()
            elif befehl in ("get", "reget"):
                sitzung.hole([a for a in args if not a.startswith("-")], fortsetzen=befehl == "reget")
            else:   # put, rm, rename, mkdir, chmod, ln … – das Abholprofil darf nur lesen
                raise Fehler(f"{befehl}: Permission denied")
        except Fehler as e:
            print(str(e), file=sys.stderr)
            if not weiter:
                return 1
        except Abbruch:
            sys.stdout.flush()
            print("client_loop: send disconnect: Broken pipe\r\nConnection closed", file=sys.stderr)
            return 255
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
