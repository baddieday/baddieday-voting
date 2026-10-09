"""sftp-Attrappe für die Briefkasten-Tests (Mehrbenutzer, Stufe 2): spielt `sftp -b` gegen einen Ordner wie der
Briefkasten auf dem vServer (chroot je Benutzer, internal-sftp -d /fach) – in seinen zwei Profilen:
  - Abholen (der Mini, Schlüssel aus <chroot>/.schluessel): nur lesen (ls, df, get, reget).
  - Hochladen (der PC des Freundes, Schlüssel aus <chroot>/.schluessel_pc): ls, df, put, reput, rename [-l] – nicht
    lesen, nicht löschen, keine Ordner, kein fertiges Ziel überschreiben, nicht an die Marke, nur in die vier Unterordner.

Aufruf wie das echte sftp: sftp -F <datei|none> -b <-|datei> -P <port> [-l kbit] -o … <benutzer>@<host>. Werte von -o
dürfen in Anführungszeichen stehen (IdentityFile="C:/mit Leerzeichen/pc"), wie bei ssh. Lokale Pfade gelten ab dem
Arbeitsordner des Aufrufs. Die Ausgabe folgt dem echten OpenSSH 9.6 (geprüft mit echtem sshd und echtem sftp): vor
jedem Befehl „sftp> <befehl>“ auf stdout; `ls -1` schreibt „<pfad>/<name>“ je Zeile; `df` zwei Zeilen in KiB; Fehler
auf stderr mit den Texten des echten (z. B. „resume "x": destination file same size or larger“, „remote rename … :
Failure“, „read remote … : Permission denied“); ein Fehler ohne „-“ vor dem Befehl beendet den Lauf mit Exit 1, eine
abgebrochene Verbindung mit Exit 255. `reget` setzt fort, auf eine vollständige Datei ist es kein Fehler.

Umgebung:
  SFTP_ATTRAPPE_WURZEL     je Benutzer ein Ordner <wurzel>/<benutzer>/ (= chroot), darin fach/ und die Dateien
                           .schluessel (Abholen, wie abholen/<benutzer>) bzw. .schluessel_pc (Hochladen, wie
                           hochladen/<benutzer>) mit dem einzigen IdentityFile, das im jeweiligen Profil hereindarf
  SFTP_ATTRAPPE_ABBRUCH    so viele Bytes (über alle reget, put und reput eines Aufrufs), dann bricht die Verbindung ab
                           (Exit 255); was bis dahin kam, bleibt liegen
  SFTP_ATTRAPPE_AUS        "1" = vServer nicht erreichbar (Exit 255 wie „ssh: connect …“)
  SFTP_ATTRAPPE_DF         "groesse belegt frei" in KiB statt der Werte des Ordners
  SFTP_ATTRAPPE_ALT        "1" = Briefkasten mit OpenSSH vor 8.6 (Ubuntu 20.04, Debian 11): `rename` ohne -l scheitert
                           mit „Permission denied“, `rename -l` geht (wie echt mit 8.2p1 nachgestellt, M135)
  SFTP_ATTRAPPE_PROTOKOLL  Datei: je Aufruf eine JSON-Zeile (argv, Arbeitsordner, Profil, Befehle, Exit) – für Aufrufer,
                           die die Tests nicht am Modul aufzeichnen können (das PC-Programm in PowerShell). Der Abholer
                           setzt sie nicht: Er begrenzt die Größe jeder Datei, die sftp schreibt (RLIMIT_FSIZE).
"""

from __future__ import annotations

import errno
import json
import os
import posixpath
import shlex
import sys
from pathlib import Path

START = "/fach"         # internal-sftp -d /fach
BLOCK = 32 * 1024
UNTERORDNER = ("videos", "replays", "sitzungen", "status")   # gehören dem Freund – alles andere im Fach gehört root


class Abbruch(Exception):
    """Die Verbindung ist weg (Exit 255)."""


class Fehler(Exception):
    """Ein Befehl ist gescheitert (Exit 1, außer mit „-“ davor)."""


def installiere(ordner: Path) -> Path:
    """Ein ausführbares `sftp` in ordner, das diese Attrappe mit dem Python der Tests startet – für briefkasten.SFTP
    bzw. Sftp in der freund.psd1 des PC-Programms."""
    programm = Path(ordner) / "sftp"
    programm.write_text(f"#!{sys.executable}\nimport runpy\nrunpy.run_path({str(Path(__file__).resolve())!r}, "
                        "run_name='__main__')\n", encoding="utf-8")
    programm.chmod(0o755)
    return programm


def _ohne_anfuehrung(wert: str) -> str:
    """Wert einer -o-Option wie ssh ihn liest: Anführungszeichen außen weg (IdentityFile="/mit Leerzeichen/pc")."""
    if len(wert) >= 2 and wert[0] == wert[-1] and wert[0] in "\"'":
        return wert[1:-1]
    return wert


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
            optionen[schluessel] = _ohne_anfuehrung(wert)
            i += 2
        else:
            rest.append(a)
            i += 1
    ziel = rest[-1] if rest else ""
    benutzer, _, host = ziel.rpartition("@")
    return optionen, benutzer, host


class Sitzung:
    def __init__(self, chroot: Path, abbruch: int | None, profil: str):
        self.chroot = chroot.resolve()
        self.abbruch = abbruch
        self.profil = profil
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

    def _kopiere(self, ein, fd: int, start: int, *, fehler_text: str, abbrechen: bool) -> None:
        """Blockweise kopieren ab start; mit abbrechen reißt nach SFTP_ATTRAPPE_ABBRUCH Bytes (über den ganzen Aufruf)
        die Verbindung – das Stück bis dahin bleibt liegen."""
        stelle = start
        while block := ein.read(BLOCK):
            if abbrechen and self.abbruch is not None and self.bytes + len(block) > self.abbruch:
                block = block[: max(0, self.abbruch - self.bytes)]
                os.pwrite(fd, block, stelle)
                raise Abbruch()
            try:
                os.pwrite(fd, block, stelle)
            except OSError as e:   # RLIMIT_FSIZE des Aufrufers: wie das echte sftp scheitert nur dieser Befehl
                if e.errno != errno.EFBIG:
                    raise
                os.ftruncate(fd, start)
                raise Fehler(fehler_text) from None
            stelle += len(block)
            self.bytes += len(block)

    def hole(self, args: list[str], fortsetzen: bool) -> None:
        if len(args) != 2:
            raise Fehler("Aufruf: get entfernt lokal")
        if self.profil == "hochladen":   # das Upload-Profil hat kein read
            raise Fehler(f'read remote "{self.anzeige(args[0])}" : Permission denied')
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
                self._kopiere(ein, fd, start, fehler_text=f'write local "{lokal}": File too large',
                              abbrechen=fortsetzen)   # beim Holen bricht nur reget ab (get: Lieferscheine, Status)
        finally:
            os.close(fd)

    def _darf_schreiben(self, entfernt: str) -> Path:
        """Ziel im Upload-Profil: nur eine Datei direkt in einem der vier Unterordner (die Wurzel und die Marke gehören
        root, Ordner anlegen geht nicht) – sonst „Permission denied“ wie beim echten."""
        voll = self.anzeige(entfernt)
        teile = voll.split("/")   # ["", "fach", "<ordner>", "<name>"]
        if self.profil != "hochladen" or len(teile) != 4 or teile[1] != "fach" or teile[2] not in UNTERORDNER \
                or not teile[3]:
            raise Fehler(f'dest open "{voll}": Permission denied')
        return self.pfad(entfernt)

    def lade_hoch(self, args: list[str], fortsetzen: bool) -> None:
        """put (anlegen oder kürzen und neu schreiben) bzw. reput (an eine kürzere vorhandene Datei anhängen)."""
        args = [a for a in args if not a.startswith("-")]
        if len(args) != 2:
            raise Fehler("Aufruf: put lokal entfernt")
        lokal = Path(args[0])
        if not lokal.is_file():
            raise Fehler(f'stat local "{lokal}": No such file or directory')
        ziel = self._darf_schreiben(args[1])
        groesse = lokal.stat().st_size
        start = 0
        if fortsetzen:
            if not ziel.is_file():
                raise Fehler("stat remote: No such file or directory")
            start = ziel.stat().st_size
            if start >= groesse:
                raise Fehler(f'resume "{args[0]}": destination file same size or larger')
        fd = os.open(ziel, os.O_WRONLY | os.O_CREAT | (0 if fortsetzen else os.O_TRUNC), 0o600)
        try:
            with open(lokal, "rb") as ein:
                ein.seek(start)
                self._kopiere(ein, fd, start, fehler_text=f'write remote "{self.anzeige(args[1])}": Failure',
                              abbrechen=True)
        finally:
            os.close(fd)

    def benenne_um(self, args: list[str]) -> None:
        """rename auf die alte Art (SSH2_FXP_RENAME): ein vorhandenes Ziel bleibt („Failure“), nichts außerhalb der
        Unterordner. `rename -l` erzwingt sie; ohne -l nimmt sftp posix-rename, wenn der Server es anbietet – ab OpenSSH
        8.6 bietet der Briefkasten es nicht an (dann ist es dasselbe), davor (SFTP_ATTRAPPE_ALT=1) bietet er es trotz
        Erlaubnisliste an und verweigert es dann mit „Permission denied“ (Befund B1, M135)."""
        klassisch = args[:1] == ["-l"]
        if klassisch:
            args = args[1:]
        if len(args) != 2:
            raise Fehler("Aufruf: rename [-l] alt neu")
        alt, neu = self.anzeige(args[0]), self.anzeige(args[1])
        if not klassisch and os.environ.get("SFTP_ATTRAPPE_ALT") == "1":
            raise Fehler(f'remote rename "{alt}" to "{neu}": Permission denied')
        try:
            quelle, ziel = self._darf_schreiben(args[0]), self._darf_schreiben(args[1])
        except Fehler:
            raise Fehler(f'remote rename "{alt}" to "{neu}": Permission denied') from None
        if not quelle.exists():
            raise Fehler(f'remote rename "{alt}" to "{neu}": No such file or directory')
        if os.path.lexists(ziel):
            raise Fehler(f'remote rename "{alt}" to "{neu}": Failure')
        os.rename(quelle, ziel)


def _verweigert(befehl: str, args: list[str], sitzung: Sitzung) -> Fehler:
    """Was keines der Profile darf: löschen, Ordner anlegen, Rechte, Links (Texte wie beim echten)."""
    ziel = sitzung.anzeige(args[0]) if args else START
    if befehl == "rm":
        return Fehler(f"remote delete {ziel}: Permission denied")
    if befehl == "mkdir":
        return Fehler(f'remote mkdir "{ziel}": Permission denied')
    return Fehler(f"{befehl}: Permission denied")


def _protokoll(eintrag: dict) -> None:
    if datei := os.environ.get("SFTP_ATTRAPPE_PROTOKOLL"):
        with open(datei, "a", encoding="utf-8") as f:
            f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")


def main() -> int:
    optionen, benutzer, host = _argumente(sys.argv[1:])
    stapel = optionen.get("-b")
    if stapel == "-":
        befehle = [z.rstrip("\n") for z in sys.stdin.read().splitlines()]
    elif stapel:
        befehle = [z.rstrip("\r") for z in Path(stapel).read_text(encoding="utf-8").splitlines()]
    else:
        befehle = []
    eintrag = {"argv": sys.argv[1:], "ordner": os.getcwd(), "profil": None, "befehle": befehle, "exit": None}
    code = _lauf(optionen, benutzer, host, befehle, eintrag)
    eintrag["exit"] = code
    _protokoll(eintrag)
    return code


def _lauf(optionen: dict[str, str], benutzer: str, host: str, befehle: list[str], eintrag: dict) -> int:
    port = optionen.get("-P", "22")
    if os.environ.get("SFTP_ATTRAPPE_AUS") == "1":
        print(f"ssh: connect to host {host} port {port}: Connection timed out\r\nConnection closed", file=sys.stderr)
        return 255
    chroot = Path(os.environ["SFTP_ATTRAPPE_WURZEL"]) / benutzer
    schluessel = optionen.get("IdentityFile", "")
    profil = None
    for name, art in ((".schluessel", "abholen"), (".schluessel_pc", "hochladen")):
        erlaubt = chroot / name
        if benutzer and schluessel and erlaubt.is_file() and \
                os.path.realpath(erlaubt.read_text(encoding="utf-8").strip()) == os.path.realpath(schluessel):
            profil = art
    if profil is None or optionen.get("IdentitiesOnly") != "yes" or optionen.get("StrictHostKeyChecking") != "yes":
        print(f"{benutzer}@{host}: Permission denied (publickey).\r\nConnection closed", file=sys.stderr)
        return 255
    eintrag["profil"] = profil
    abbruch = os.environ.get("SFTP_ATTRAPPE_ABBRUCH")
    sitzung = Sitzung(chroot, int(abbruch) if abbruch else None, profil)
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
            elif befehl in ("put", "reput") and profil == "hochladen":
                sitzung.lade_hoch(args, fortsetzen=befehl == "reput")
            elif befehl == "rename" and profil == "hochladen":
                sitzung.benenne_um(args)
            else:   # rm, mkdir, chmod, ln … – und im Abholprofil auch put und rename: es darf nur lesen
                raise _verweigert(befehl, args, sitzung)
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
