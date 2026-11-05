## 0.2.0
- Arhivă pe sloturi PT15M: backfill din istoricul HA (până la 10 zile) + poll la 30 s al `sensor.deye_economic_auditor_scenario_capture`.
- Stocare JSONL pe zi, append-only, cu revizii, în `/share/deye_lab/pw_lab/slots/` (supraviețuiește dezinstalării).
- `status.json` cu acoperirea ultimei zile (sloturi vs așteptate, tratează zilele cu 92/100 sloturi).
- Schemă normalizată `ScenarioResult` (zi, scenariu, versiune EMHASS, eră, certificat, cost net).
- Log fără linii duble. `boot: auto`.

## 0.1.0
- Schelet: venv aliniat la versiunea EMHASS de producție, selftest import.
