# Diagnose: Shorts zeigen immer dieselben Clips / werden kürzer (27.09.2026)

Read-only-Checkliste für den Mini (LXC `clips`). Nichts wird geschrieben, pve-big wird nie geweckt (kein Zugriff auf
`/srv/big`). Hintergrund und Korrektur: PR „Regisseur: Abwechslung, Nachlegen nach dem Kürzen, ⏱️ zu kurz“ (27.09.);
die Hypothesen H1–H6 stammen aus der Ursachenprüfung dazu (docs/REGIE.md, Abschnitt „Abwechslung“).

| # | Hypothese | Stand der Prüfung am Code (27.09.) |
|---|---|---|
| H1 | Momente ohne Datei fallen still weg | Defekt echt (jetzt: Rückfall auf den Bot-Clip + Zählung), als Ursache in Produktion unbelegt – der Bot zeigte ~120–128 Kandidaten |
| H2 | Abwechslung zu schwach – Rotation nur in der Spitze | **bestätigt** (3/3 Gegenprüfer): 13–18 verschiedene Momente in 20 Shorts bei 120 vorhandenen → Cooldown + Frische-Quote |
| H3 | Serien > 20 s fallen aus Shorts | widerlegt als Ursache (~1 % der Serien); Nebenwirkung: eine Serie belegt bis 20 s |
| H4 | dauer_faktor kann nur fallen (kein „zu kurz“) | Mechanik bestätigt, als Hauptursache der Kürze widerlegt → trotzdem korrigiert (⏱️ zu kurz, [regie.formate]) |
| H5 | Kürzen ohne Nachlegen im Serien-Regime | **bestätigt** als Hauptursache der Kürze: 30–38 s mit 2–3 Momenten statt 45 s → Nachlegen nach dem Kürzen |
| H6 | Stimmung nachziehen scheitert still | als Ursache widerlegt (Pool ist da); Sichtbarkeit korrigiert (Fehler → Hinweis im Entwurf) |

Reihenfolge: Kommando 0 zuerst (welcher Checkout läuft wirklich), dann 1–5. Jedes Kommando ist copy-paste-fähig (bash).

## 0. Umgebung festnageln: Aus welchem Checkout/Commit läuft der Lern-Bot wirklich, welche DB und welcher Puffer stehen in den lokal.toml beider Checkouts? (deploy/systemd/clip-lernbot.service:13 hat `ExecStart=/opt/clip-pipeline/.venv/bin/pipeline lernbot`; deploy/pve-mini/regie-starten.sh:121 ersetzt das per sed durch /opt/clip-regie – nur wenn dieses Skript lief, läuft der Bot aus /opt/clip-regie.)

```bash
systemctl cat clip-lernbot 2>/dev/null | grep -E 'ExecStart|WorkingDirectory|ReadWritePaths'; systemctl is-active clip-lernbot; for d in /opt/clip-pipeline /opt/clip-regie; do echo "== $d: $(sudo -u pipeline git -C $d log -1 --format='%h %ad %s' --date=short 2>/dev/null)"; grep -nE '^\s*(pfad|wurzel|ordner|stimmung_je_entwurf|dauer_faktor|seg_min_faktor|abwechslung|max_je_match|lernen_ab)\s*=' $d/config/lokal.toml 2>/dev/null | sed "s#^#   lokal.toml:#"; done; echo '== Mounts:'; findmnt -no SOURCE,TARGET,FSTYPE /srv/clips /srv/puffer 2>/dev/null; ls -ld /srv/clips; echo '== Regie-Ordner:'; ls -la /var/lib/clip-pipeline/regie 2>/dev/null | tail -5; echo '== Material-Kopien:'; ls /var/lib/clip-pipeline/material 2>/dev/null | head -5; find /var/lib/clip-pipeline/material -type f 2>/dev/null | wc -l
```

**Lesen:** ExecStart muss auf /opt/clip-regie zeigen, sonst läuft der alte main-Stand (H6: falscher Code/falsche DB; alle weiteren Zahlen dann gegen /opt/clip-pipeline lesen). datenbank.pfad in beiden lokal.toml identisch = /var/lib/clip-pipeline/pipeline.db (Annahme laut config/pipeline.toml:226; docs/PUFFER.md:240 sagt, regie.sql legt nur neue Tabellen in derselben DB an). Steht in lokal.toml ein [regie.vorgaben] dauer_faktor/max_je_match, ist das eine harte Untergrenze/Vorgabe (regie_lernen.py:79-106) – H3 direkt belegt. Anzahl Material-Kopien (letzte Zeile) ≈ Anzahl Momente erwartet; 0 heißt: momente.datei zeigt in den Puffer /srv/clips (relevant für H1).

## 1. Momente zählen: gesamt, mit existierender Datei, Pfad-Präfixe, je clip_status, Clips ohne momente-Zeile. Belegt, wie groß die Kandidatenmenge wirklich ist, denn regie.kandidaten() lässt Zeilen mit `clip_status == 'verworfen' or not Path(datei).is_file()` still fallen (regie.py:279-280) – die Hinweis-Zeile 'nur N Momente zur Auswahl' (regie.py:683-684) zählt nur die überlebenden.

```bash
sudo -u pipeline env PYTHONPATH=/opt/clip-regie/src /opt/clip-regie/.venv/bin/python - <<'EOF'
import os, sqlite3, sys, json
from collections import Counter
from clip_pipeline import konfig as K
k = K.lade()
pfad = str(k.datenbank)
print("DB:", pfad, "| Puffer:", k.wurzel, "| material.ordner:", k.wert("material.ordner", "?"), "| regie.ordner:", k.wert("regie.ordner", "?"))
con = sqlite3.connect(f"file:{pfad}?mode=ro", uri=True); con.row_factory = sqlite3.Row
def q(sql, *a):
    try: return con.execute(sql, a).fetchall()
    except sqlite3.OperationalError as e: print("  (Abfrage fehlgeschlagen:", e, ")"); return []
mom = q("SELECT m.id, m.schluessel, m.clip_id, m.datei, m.ende_s, m.merkmale, c.status AS clip_status, c.clip_pfad FROM momente m LEFT JOIN clips c ON c.id=m.clip_id")
print("momente gesamt:", len(mom))
ex = [z for z in mom if os.path.isfile(z["datei"])]
print("  davon datei existiert:", len(ex), "| fehlt:", len(mom)-len(ex))
praef = Counter()
for z in mom:
    d = z["datei"]
    if "/momente/" in d: praef["…/sessions/<id>/momente/ (Nachschnitt)"] += 1
    elif d.startswith(str(k.wert("material.ordner", "/var/lib/clip-pipeline/material"))): praef["material-Kopie"] += 1
    elif d.startswith(str(k.wurzel)): praef["Puffer " + str(k.wurzel)] += 1
    else: praef["sonst: " + d.rsplit("/", 2)[0]] += 1
print("  datei-Praefixe:", dict(praef))
fehlt = Counter()
for z in mom:
    if not os.path.isfile(z["datei"]): fehlt[z["datei"].rsplit("/", 1)[0]] += 1
print("  fehlende Dateien nach Ordner (Top 10):", fehlt.most_common(10))
print("  je clip_status (None = Datei-Moment ohne Clip):", dict(Counter(z["clip_status"] for z in mom)))
print("  je clip_status, nur existierende Datei:", dict(Counter(z["clip_status"] for z in ex)))
ns = sum(1 for z in mom if '"nachschnitt"' in (z["merkmale"] or ""))
print("  mit merkmale.nachschnitt:", ns)
print("  kill_sekunden>=2 (Serie):", sum(1 for z in mom if len((json.loads(z["merkmale"] or "{}").get("kill_sekunden") or [])) >= 2))
ohne = q("SELECT COUNT(*) n FROM clips c WHERE c.clip_pfad IS NOT NULL AND c.status != 'verworfen' AND NOT EXISTS (SELECT 1 FROM momente m WHERE m.clip_id=c.id)")
print("clips (nicht verworfen, mit clip_pfad) OHNE momente-Zeile:", ohne[0]["n"] if ohne else "?")
print("clips je status:", {z["status"]: z["n"] for z in q("SELECT status, COUNT(*) n FROM clips GROUP BY status")})
print("clips mit clip_pfad gesamt:", q("SELECT COUNT(*) n FROM clips WHERE clip_pfad IS NOT NULL")[0]["n"])
EOF
```

**Lesen:** H1 (Kandidatenschwund durch fehlende Dateien) ist belegt, wenn 'davon datei existiert' deutlich unter 'momente gesamt' (~120-128) liegt, z. B. 20 von 125. 'fehlende Dateien nach Ordner' zeigt dann, wo: Puffer-Pfade /srv/clips/sessions/… (Rohdaten/Bot-Clips nach dem Puffer-Umbau E19 nur noch im Lager auf pve-big, das schläft → is_file() False) oder Nachschnitt-Pfade …/sessions/<id>/momente/. Liegen viele Momente als 'Puffer'-Präfix vor, ohne Material-Kopie (Befehl 0: 0 Kopien), gehen sie im Betrieb verloren, sobald der Puffer die Datei nicht hat. 'verworfen' im Status fällt ebenfalls weg (regie.py:279). 'clips OHNE momente-Zeile' > 0 bedeutet, dass stimmung_nachziehen nicht hinterherkommt oder scheitert (→ H5, Befehl 5). Ist 'existiert' ≈ gesamt (≥ 100), ist H1 widerlegt und die Engführung liegt in der Auswahl (H2/H4).

## 2. Die letzten 12 Entwürfe aus der DB plus Schnittlisten-JSON (auswahl.kandidaten, auswahl.neu, hinweise, Momente je Entwurf) und die Menge der verschiedenen Momente über alle Short-Entwürfe – misst 'immer dieselben' und die tatsächliche dauer_s.

```bash
sudo -u pipeline env PYTHONPATH=/opt/clip-regie/src /opt/clip-regie/.venv/bin/python - <<'EOF'
import sqlite3, json, os
from clip_pipeline import konfig as K
k = K.lade(); con = sqlite3.connect(f"file:{k.datenbank}?mode=ro", uri=True); con.row_factory = sqlite3.Row
try: rows = con.execute("SELECT id, name, format, dauer_s, status, erstellt, schnittliste, parameter FROM entwuerfe ORDER BY id DESC LIMIT 12").fetchall()
except sqlite3.OperationalError as e: print("keine entwuerfe:", e); rows = []
alle_short = set(); shorts = 0
for z in rows:
    try: l = json.load(open(z["schnittliste"], encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e: l = {"_fehler": str(e)}
    seg = l.get("segmente", [])
    mom = [s for s in seg if s.get("teil", 1) == 1 and s.get("rolle") != "hook"]
    ms = {s.get("moment") for s in mom}
    if z["format"] == "short": shorts += 1; alle_short |= ms
    p = json.loads(z["parameter"] or "{}")
    print(f"#{z['id']} {z['format']:16} dauer={z['dauer_s']} status={z['status']} {z['erstellt']}")
    print(f"    auswahl={l.get('auswahl')} momente={len(ms)} segmente={len(seg)} dauer_faktor={p.get('dauer_faktor')} seg_min_faktor={p.get('seg_min_faktor')} abwechslung={p.get('abwechslung')} max_je_match={p.get('max_je_match')}")
    print(f"    hinweise={l.get('hinweise', l.get('_fehler'))}")
    print(f"    momente={sorted(ms)}")
print(f"\n{shorts} Short-Entwuerfe unter den letzten {len(rows)}: {len(alle_short)} verschiedene Momente insgesamt -> {sorted(alle_short)}")
EOF
```

**Lesen:** auswahl.kandidaten ist die Zahl, die der Regisseur wirklich sah (regie.py:712 `"kandidaten": len(alle)` – nach Datei-Filter UND Serien-Ausschluss). Liegt sie bei 10-30 statt ~120, ist die Engführung vor der Auswahl (H1/H4); liegt sie ≥ 100 und die Vereinigungsmenge über 12 Shorts trotzdem nur 15-25 Momente, ist es die Punktedominanz (H2). Hinweise lesen: 'N Serie(n) zu lang für Short' (regie.py:620) → H4 mit Zahl; 'nur N Momente zur Auswahl' → H1; 'Dauer x s unter 30 s' → Material fehlt. dauer_s-Reihe: fällt sie von ~40-45 auf ~30-33, passt das zu H3 (Ziel wird auf min 30 geklemmt, regie.py:333-334); dauer_faktor je Entwurf zeigt den Abfall über die Zeit. Lokale Referenz (Fixture, 16 Momente, 3 Shorts): Vereinigung 11 von 16, dauer 41 → 40.5 → 36.5 bei dauer_faktor 1.0 → 0.9 → 0.81.

## 3. Gelernte Regie-Parameter und Bewertungsgründe: Häufigkeit von 'lang' (regie_lernen.py:37 – es gibt kein Gegenstück 'zu kurz'), dauer_faktor-Verlauf in entwuerfe.parameter, moment_bonus-Verteilung, und was regie_lernen.aktuelle() heute liefert.

```bash
sudo -u pipeline env PYTHONPATH=/opt/clip-regie/src /opt/clip-regie/.venv/bin/python - <<'EOF'
import sqlite3, json
from collections import Counter
from clip_pipeline import konfig as K
k = K.lade(); con = sqlite3.connect(f"file:{k.datenbank}?mode=ro", uri=True); con.row_factory = sqlite3.Row
print("regie.vorgaben aus Konfig (lokal.toml):", k.wert("regie.vorgaben", {}), "| regie.lernen_ab:", k.wert("regie.lernen_ab", 3))
try:
    b = con.execute("SELECT b.entwurf_id, b.daumen, b.gruende, b.erstellt, e.format FROM entwurf_bewertungen b JOIN entwuerfe e ON e.id=b.entwurf_id ORDER BY b.erstellt").fetchall()
except sqlite3.OperationalError as e: print("keine Bewertungen:", e); b = []
print("Bewertungen gesamt:", len(b), "| Daumen:", dict(Counter(z["daumen"] for z in b)))
g = Counter()
for z in b: g.update(json.loads(z["gruende"] or "[]"))
print("Gruende (Haeufigkeit):", dict(g), "-> 'lang':", g.get("lang", 0), "(je 'lang' dauer_faktor x0.9, Untergrenze 0.6)")
print("erwarteter dauer_faktor allein aus 'lang':", round(max(0.6, 0.9 ** g.get("lang", 0)), 3))
for z in b[-15:]: print(f"   E#{z['entwurf_id']} {z['format']:16} daumen={z['daumen']:+d} gruende={z['gruende']} {z['erstellt']}")
print("\nParameter der letzten 8 Entwuerfe (entwuerfe.parameter):")
for z in con.execute("SELECT id, format, parameter, erstellt FROM entwuerfe ORDER BY id DESC LIMIT 8"):
    p = json.loads(z["parameter"] or "{}")
    print(f"   E#{z['id']} {z['format']:16} dauer_faktor={p.get('dauer_faktor')} seg_min_faktor={p.get('seg_min_faktor')} beats_pro_schnitt={p.get('beats_pro_schnitt')} abwechslung={p.get('abwechslung')} max_je_match={p.get('max_je_match')} puffer_vor={p.get('puffer_vor_s')} puffer_nach={p.get('puffer_nach_s')} moment_bonus_n={len(p.get('moment_bonus') or {})} min_bonus={min((p.get('moment_bonus') or {0:0}).values())} max_bonus={max((p.get('moment_bonus') or {0:0}).values())}")
try:
    from clip_pipeline import regie_lernen
    p, ziel = regie_lernen.aktuelle(con, k)
    print("\nregie_lernen.aktuelle JETZT:", {x: p[x] for x in ("dauer_faktor", "seg_min_faktor", "beats_pro_schnitt", "abwechslung", "max_je_match", "puffer_vor_s", "puffer_nach_s")})
    mb = p.get("moment_bonus") or {}
    print("   moment_bonus:", len(mb), "Momente; <=-1:", sum(1 for v in mb.values() if v <= -1), "| >=+1:", sum(1 for v in mb.values() if v >= 1), "| Top:", sorted(mb.items(), key=lambda x: -x[1])[:8], "| Boden:", sorted(mb.items(), key=lambda x: x[1])[:8])
except Exception as e: print("aktuelle() fehlgeschlagen:", e)
EOF
```

**Lesen:** H3 (Shorts kürzer durch Einweg-Lernen): 'lang' ≥ 5 → dauer_faktor ≈ 0.59 → geklemmt 0.6 (regie_lernen.py:154 `_grenze(p["dauer_faktor"] * 0.9, 0.6, 1.0)`); dann ist ziel = 45·0.6 = 27 → durch regie.py:334 auf min_s 30 gehoben: jeder Short zielt auf 30 s statt bis 45. Es gibt keinen Grund, der dauer_faktor wieder anhebt (GRUENDE regie_lernen.py:34-43) – der Faktor kann nur fallen oder per [regie.vorgaben] gesetzt werden; ein 👍 ändert ihn nicht. Zeigt 'dauer_faktor' in den letzten Entwürfen 0.6 und 'aktuelle JETZT' 0.6 → H3 bestätigt. H2 (Rückkopplung): moment_bonus mit vielen Werten ≥ +1 (jedes 👍 gibt allen gezeigten Momenten +0.5, regie_lernen.py:136-140) bedeutet, dass die schon gezeigten Momente in der Punktewertung noch weiter nach oben rücken – die Abwechslung (max. voller Punktabzug, abwechslung() regie.py:245-253) hebt das bei starken Momenten nicht auf. seg_min_faktor ≥ 1.3 (aus 'hektisch') macht Segmente länger, füllt die 30 s also mit weniger Momenten → auch weniger Breite je Short.

## 4. Serien-Ausschluss und Auswahl mit dem echten Regie-Code des Lern-Bot-Checkouts: Kandidat.min_laenge je Moment, wie viele serie_zu_lang(k, FORMATE['short']) sind (> 20 s am Stück), plus Vorrat, Ziel-Dauer, Punkte-Top-15 mit Abzug/gezeigt und Verteilung je match_id. Zeilen ohne Datei/verworfen werden vorher getrennt gezählt, weil kandidaten() sie still filtert.

```bash
sudo -u pipeline env PYTHONPATH=/opt/clip-regie/src /opt/clip-regie/.venv/bin/python - <<'EOF'
import sqlite3, os
from collections import Counter
from clip_pipeline import konfig as K, regie, lernen, regie_lernen
k = K.lade(); con = sqlite3.connect(f"file:{k.datenbank}?mode=ro", uri=True); con.row_factory = sqlite3.Row
zeilen = con.execute("SELECT m.datei, c.status s FROM momente m LEFT JOIN clips c ON c.id=m.clip_id").fetchall()
print("momente-Zeilen:", len(zeilen), "| verworfen:", sum(1 for z in zeilen if z["s"] == "verworfen"), "| Datei fehlt:", sum(1 for z in zeilen if not os.path.isfile(z["datei"])))
p, _ = regie_lernen.aktuelle(con, k)
_, gew = lernen.aktuelle(con, k)
kt = [float(x) for x in k.wert("vorbewertung.kill_punkte")]
frueher = regie.gezeigte_momente(con)
print("gezeigte_momente: Entwuerfe im Fenster:", len(frueher), "| verschiedene Momente darin:", len({m for f in frueher for m in f}))
ks = regie.kandidaten(con, p, frueher, gewichte=gew, kill_tabelle=kt)
fmt = regie.FORMATE["short"]
print("kandidaten():", len(ks), "| serie:", sum(k_.serie for k_ in ks), "| serie_zu_lang (Short, >%.0f s):" % fmt["serie_max_s"], sum(regie.serie_zu_lang(k_, fmt) for k_ in ks), "| mehrteilig:", sum(len(k_.teile) > 1 for k_ in ks))
seg_min = fmt["seg_min_s"] * p["seg_min_faktor"]
pl = [regie.plan_laenge(k_, fmt, seg_min) for k_ in ks]
brauch = [k_ for k_ in ks if not regie.serie_zu_lang(k_, fmt)]
vorrat = sum(regie.plan_laenge(k_, fmt, seg_min) for k_ in brauch)
ziel = min(fmt["max_s"], max(fmt["min_s"], 0.8 * vorrat)) * p["dauer_faktor"]; ziel = min(fmt["max_s"], max(fmt["min_s"], ziel))
print(f"brauchbar fuer Short: {len(brauch)} | vorrat={vorrat:.0f} s | dauer_faktor={p['dauer_faktor']} -> ziel={ziel:.1f} s (min {fmt['min_s']}, max {fmt['max_s']})")
print("plan_laenge je Kandidat: min=%.1f mittel=%.1f max=%.1f" % (min(pl), sum(pl)/len(pl), max(pl)) if pl else "keine Kandidaten")
print("Punkte-Verteilung (Top 15 nach punkte, mit abzug/gezeigt/serie/min_laenge):")
for k_ in sorted(ks, key=lambda x: -x.punkte)[:15]:
    print(f"   {k_.schluessel:12} match={k_.match_id} punkte={k_.punkte:6.2f} abzug={k_.abzug:5.2f} gezeigt={k_.gezeigt} int={k_.intensitaet:5.2f} serie={k_.serie} teile={len(k_.teile)} min_l={k_.min_laenge:5.1f} kern={k_.kern_laenge:5.1f} zu_lang={regie.serie_zu_lang(k_, fmt)}")
if ks: print("Punkte-Spanne aller:", min(x.punkte for x in ks), "..", max(x.punkte for x in ks), "| Median:", sorted(x.punkte for x in ks)[len(ks)//2])
print("Kandidaten je match_id (Top 10):", Counter(k_.match_id for k_ in ks).most_common(10), "| Matches gesamt:", len({k_.match_id for k_ in ks}))
print("Serien zu lang (Liste):", [(k_.schluessel, round(k_.min_laenge, 1)) for k_ in ks if regie.serie_zu_lang(k_, fmt)])
EOF
```

**Lesen:** Erste Zeile gegen 'kandidaten()' halten: die Differenz ist genau der stille Filter aus regie.py:279-280 (H1). 'serie_zu_lang' > 0 belegt H4: seit 'Multikills am Stück' (Kandidat.serie=True bei ≥ 2 kill_sekunden mit Aktionszeiten, regie.py:230; serie_zu_lang regie.py:322-324) fallen Multikills mit min_laenge > 20 s für Shorts komplett weg (regie.py:611-618) – das sind ausgerechnet die stärksten Momente, und 'brauchbar fuer Short' schrumpft. Ziel-Zeile: steht dort ziel=30.0 bei dauer_faktor 0.6 → H3 (Klemme auf min_s). Top-15: bleiben Momente mit gezeigt ≥ 2 trotz abzug oben (lokale Referenz: datei:1 mit int 11.0, abzug 5.78, gezeigt 2, immer noch Platz 1 mit 5.22), ist H2 belegt – der Abzug ist relativ zu den eigenen Punkten und kann einen Moment nie unter einen schwächeren Moment mit 0 Punkten drücken, wenn dessen Punkte ≤ 1 sind; hohe Elo ((elo−1500)/100, regie.py:293) und Freigabe-Bonus +1 (regie.py:290) verstärken das. 'Matches gesamt' klein (z. B. ≤ 8) mit max_je_match=3 heißt: höchstens 3·Matches Momente wählbar (regie.py:342), auch das engt ein. Fallback, falls `mode=ro` an WAL scheitert: `sqlite3.connect(pfad)` ohne uri – das ändert nichts, solange nur SELECT läuft.

## 5. Stimmung-Nachziehen und Whisper: Log-Zeilen des Lern-Bot-Dienstes der letzten 7 Tage (stimmung_nachziehen schluckt JEDE Exception, lernbot.py:164-167 `except Exception: log.exception("Stimmung nachziehen fehlgeschlagen")`), Importierbarkeit von faster_whisper im Lern-Bot-venv, und ob pve-big-Prüfungen den Entwurf blockierten.

```bash
echo '== Nachziehen / Fehler im Log:'; journalctl -u clip-lernbot --since '7 days ago' --no-pager 2>/dev/null | grep -iE 'Stimmung|fehlgeschlagen|Whisper|Traceback|Error|SpeicherOffline|nachholen|Entwurf' | tail -60; echo; echo '== Zähler:'; for m in 'Stimmung für' 'Stimmung nachziehen fehlgeschlagen' 'Traceback' 'SpeicherOffline' 'nicht installiert'; do printf '%-45s %s\n' "$m" "$(journalctl -u clip-lernbot --since '7 days ago' --no-pager 2>/dev/null | grep -c "$m")"; done; echo '== faster_whisper im Lern-Bot-venv:'; sudo -u pipeline /opt/clip-regie/.venv/bin/python -c 'import importlib.util,sys; print("faster_whisper:", "JA" if importlib.util.find_spec("faster_whisper") else "NEIN", "|", sys.executable)'; echo '== faster_whisper im Produktions-venv:'; sudo -u pipeline /opt/clip-pipeline/.venv/bin/python -c 'import importlib.util; print("JA" if importlib.util.find_spec("faster_whisper") else "NEIN")'; echo '== Konfig lernbot/stimmung:'; grep -nE 'stimmung_je_entwurf|^\[lernbot\]|^\[stimmung\]|^claude\s*=|^whisper' /opt/clip-regie/config/pipeline.toml /opt/clip-regie/config/lokal.toml 2>/dev/null
```

**Lesen:** H5 (Auswahl wächst nicht): 'Stimmung nachziehen fehlgeschlagen' > 0 oder Tracebacks → analysiere() bricht ab, es kommen keine neuen momente-Zeilen hinzu; die Kandidatenmenge bleibt bei den zuerst analysierten (~10-20, sortiert 'Beste zuerst', stimmung.py:133-136) – das erklärt 'immer dieselben'. 'Stimmung für N weitere Clips' = 0 Treffer über 7 Tage bei gleichzeitig 'clips OHNE momente-Zeile' > 0 (Befehl 1) stützt H5 ebenfalls. faster_whisper NEIN ist für die Auswahl unkritisch (nur Hinweis 'faster-whisper nicht installiert', stimmung.py:483), erklärt aber fehlende Stimm-Merkmale. SpeicherOffline-Zeilen: baue_entwurf ruft `konfig.pruefe_speicher(wecken=True)` (lernbot.py:179) – schlägt das an, entstehen gar keine Entwürfe; Datei-Momente, die im Lager liegen, sind dann für is_file() unsichtbar (→ H1). Viele Treffer 'Entwurf' ohne Fehler und Zähler 0 → H5 widerlegt, Ursache liegt in Befehl 2-4.
