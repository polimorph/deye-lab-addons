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
