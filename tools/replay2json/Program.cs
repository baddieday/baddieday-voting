// replay2json – liest ein Fortnite-Replay und gibt die für die Clip-Pipeline
// nötigen Daten als JSON auf stdout aus.
//
// Aufruf:  replay2json <datei.replay> [--ich <Epic-ID oder Teil des Spielernamens>]
// Exit-Codes: 0 = ok, 1 = falscher Aufruf, 2 = Replay konnte nicht gelesen werden
//
// Das JSON-Format ist die Schnittstelle zu Python (src/clip_pipeline/replay.py).
// Wer den Parser austauscht, muss nur dieses Format wieder erzeugen.
//
// Wichtig (geprüft mit Build 42.20): IsReplayOwner ist nicht gesetzt. Deshalb wird
// "ich" über --ich bestimmt, sonst per Heuristik (Kills == eigene Replay-Stats).

using System.Text.Json;
using FortniteReplayReader;
using FortniteReplayReader.Models;
using Unreal.Core.Models.Enums;

string? pfad = null, ichSuche = null;
for (var i = 0; i < args.Length; i++)
{
    if (args[i] == "--ich" && i + 1 < args.Length) ichSuche = args[++i];
    else if (pfad is null) pfad = args[i];
    else { pfad = null; break; }
}
if (pfad is null)
{
    Console.Error.WriteLine("Aufruf: replay2json <datei.replay> [--ich <Epic-ID oder Namensteil>]");
    return 1;
}

FortniteReplay replay;
try
{
    // Normal liest Ereignisse und Spielerdaten; Full (Bewegungen usw.) brauchen wir nicht.
    replay = new ReplayReader(parseMode: ParseMode.Normal).ReadReplay(pfad);
}
catch (Exception ex)
{
    Console.Error.WriteLine(JsonSerializer.Serialize(new { fehler = ex.GetType().Name, meldung = ex.Message }));
    return 2;
}

var menschen = (replay.PlayerData ?? []).Where(p => !p.IsBot).ToList();
PlayerData? ich = null;
string? ichQuelle = null;
if (ichSuche is not null)
{
    ich = menschen.FirstOrDefault(p => string.Equals(p.EpicId, ichSuche, StringComparison.OrdinalIgnoreCase))
       ?? menschen.FirstOrDefault(p => p.PlayerName?.Contains(ichSuche, StringComparison.OrdinalIgnoreCase) == true);
    ichQuelle = ich is null ? null : "argument";
}
if (ich is null && menschen.FirstOrDefault(p => p.IsReplayOwner) is { } besitzer)
{
    (ich, ichQuelle) = (besitzer, "replay_owner");
}
if (ich is null && replay.Stats is { } stats)
{
    // Heuristik: genau ein Mensch hat so viele Kills wie die eigenen Replay-Stats
    var passend = menschen.Where(p => p.Kills == stats.Eliminations && p.Placement is not null).ToList();
    if (passend.Count == 1) (ich, ichQuelle) = (passend[0], "heuristik");
}

var ausgabe = new
{
    parser = "FortniteReplayReader " + typeof(ReplayReader).Assembly.GetName().Version,
    datei = Path.GetFileName(pfad),
    build = new
    {
        branch = replay.Header?.Branch,
        changelist = replay.Header?.Changelist,
    },
    // Zeitpunkt, an dem Fortnite das Replay angelegt hat. Kind wird mit ausgegeben,
    // damit Python prüfen kann, ob es wirklich UTC ist.
    replay_start = Iso(replay.Info?.Timestamp),
    replay_start_kind = replay.Info?.Timestamp.Kind.ToString(),
    laenge_ms = replay.Info?.LengthInMs,
    live = replay.Info?.IsLive,
    match = new
    {
        start_utc = Iso(replay.GameData?.UtcTimeStartedMatch),
        ende_welt_s = replay.GameData?.MatchEndTime,
        bus_start_s = replay.GameData?.AircraftStartTime,
        playlist = replay.GameData?.CurrentPlaylist,
        teamgroesse = replay.GameData?.TeamSize,
        gewinner_team = replay.GameData?.WinningTeam,
        gewinner_ids = replay.GameData?.WinningPlayerIds,
    },
    ich_quelle = ichQuelle,
    ich = ich is null ? null : new
    {
        id = ich.Id,
        player_id = ich.PlayerId,
        epic_id = ich.EpicId,
        name = ich.PlayerName,
        team = ich.TeamIndex,
        platzierung = ich.Placement,
        kills = ich.Kills,
        team_kills = ich.TeamKills,
        tod_s = ich.DeathTimeDouble ?? ich.DeathTime,
    },
    team_platzierung = replay.TeamStats?.Position,
    spieler_gesamt = replay.TeamStats?.TotalPlayers,
    stats_eliminierungen = replay.Stats?.Eliminations,
    // t_ms = Zeitpunkt des Ereignisses in Millisekunden seit Beginn des Replays
    eliminierungen = (replay.Eliminations ?? []).Select(e => new
    {
        t_ms = e.Info?.StartTime,
        zeit = e.Time,
        eliminator = e.Eliminator,
        eliminiert = e.Eliminated,
        knock = e.Knocked,
        selbst = e.IsSelfElimination,
        eliminator_bot = e.EliminatorInfo?.IsBot,
        eliminiert_bot = e.EliminatedInfo?.IsBot,
        waffe = e.GunType,
    }),
    // Killfeed: Zeit in Spielwelt-Sekunden; dient zur Gegenprobe der Zeitbasis
    killfeed = (replay.KillFeed ?? []).Select(k => new
    {
        welt_s = k.ReplicatedWorldTimeSecondsDouble ?? k.ReplicatedWorldTimeSeconds,
        opfer = k.PlayerId,
        taeter = k.FinisherOrDowner,
        am_boden = k.IsDowned,
        wiederbelebt = k.IsRevived,
    }),
};

Console.WriteLine(JsonSerializer.Serialize(ausgabe, new JsonSerializerOptions { WriteIndented = true }));
return 0;

static string? Iso(DateTime? zeit) => zeit?.ToString("o");
