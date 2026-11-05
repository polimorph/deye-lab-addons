## 0.6.0
- `publish.py`: scoreboard pe aceeași perioadă (doar sloturile evaluate în Perfect World) pentru Perfect World, real, STOCK PPC și STOCK Hydro V2.
- Valoare = -(cost + pierdere fixă) - inventar SOC; L citit din acumulatorul v0.2.7; aceeași regulă de preț (import dacă lumea importă în slot, altfel export). Hydro V2 la tarif fix 1,23597 / 0,515.
- Senzor `sensor.emhass_perfect_world_scoreboard` (republicat la fiecare ciclu) + `results/scoreboard.json`.
- Fără `-0.0` în rândurile de lanț.

## 0.5.1
- Autotest unic al lanțului (04.11 00:00–13:45, orizont acoperit) în `selftest/`, comparat cu lumea reală.

## 0.5.0
- `perfect.py`: lanț Perfect World slot cu slot (orizont ca producția, PV/load reale, SOC propriu), rezultate în `results/` (rânduri pe slot + `day_<zi>.json` cu ScenarioResult). Fără rezultate provizorii: un slot se rezolvă doar când arhiva acoperă tot orizontul.
- Fiecare rând poartă `emhass_version`, `era`, `config_hash`.

## 0.4.1
- Prețurile 'unknown' din capturi se completează (cel mult 4 sloturi) doar ca intrare pentru solver; slotul e marcat `price_filled` (costul nu se evaluează, ca în auditor).

## 0.4.0
- `adapter.py`: context EMHASS din configul LIVE de producție, payload ca MPC v4.5 (fără scalare PV/taxă risc în Perfect World), solve naive-mpc-optim.
- `solve_probe.py`: un solve pe o zi reală din arhivă → `solve_probe.json`.

## 0.3.1
- Probe rev 2: config producție prin GET /get-config (nu din fișier), rute web_server, construcția emhass_conf.
- Fix: versiunea țintă fără prefixul `v` (nu mai reinstalează EMHASS la fiecare pornire).

## 0.3.0
- Probe/Sentinel: la fiecare versiune EMHASS scrie `probe.json` (semnături, construcția emhass_conf, config producție fără secrete, citit doar-citire din /share/emhass/config.json).

## 0.2.2
- Fix backfill: API-ul REST de istoric întoarce doar 24 h dacă lipsește `end_time`; acum se interoghează pe felii de 24 h până la acum.

## 0.2.1
- Fix backfill: istoricul se cere cu `significant_changes_only=0` (altfel returna un singur rând, fiindcă starea senzorului nu se schimbă).

## 0.2.0
- Arhivă pe sloturi PT15M: backfill din istoricul HA (până la 10 zile) + poll la 30 s al `sensor.deye_economic_auditor_scenario_capture`.
- Stocare JSONL pe zi, append-only, cu revizii, în `/share/deye_lab/pw_lab/slots/` (supraviețuiește dezinstalării).
- `status.json` cu acoperirea ultimei zile (sloturi vs așteptate, tratează zilele cu 92/100 sloturi).
- Schemă normalizată `ScenarioResult` (zi, scenariu, versiune EMHASS, eră, certificat, cost net).
- Log fără linii duble. `boot: auto`.

## 0.1.0
- Schelet: venv aliniat la versiunea EMHASS de producție, selftest import.
