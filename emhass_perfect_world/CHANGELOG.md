## 0.10.0
- Scorare sub decontarea lunară PPC MaxControl (`lab/settlement.py`, funcții pure; reguli confirmate în scris de PPC 09–11.11.2026, aceleași ca Auditorul v0.3.0): compensare kWh contra kWh pe luna calendaristică locală, preț consum cu plafon 0,999 pe interval, regim deficit/surplus, tarife reglementate pe consumul brut, TVA 21%. Parametrii neconfirmați (componenta fixă 0,069 vs 0,095, TG la consum) sunt în `Rules`.
- Scoreboard: pentru PERFECT, REAL și STOCK_PPC apar `<lume>_settle_*` (`settle_cost_ron`, `settle_value_ron`, `settle_value_inv_at_import_ron`, regim, π̄_c, π̄_inj, import/export de decontare, `settle_exanom_cost_ron` = fără sloturile cu salt de contor) și `settle_delta_vs_real_ron` / `settle_delta_vs_stock_ppc_ron`. Sloturile cu salt de contor SE INCLUD în decontare (se lucrează pe totaluri), spre deosebire de scorarea cash. STOCK_HYDRO_V2 rămâne cash. Energia rămasă în baterie e evaluată la valoarea marginală a regimului (export = prudent, import evitat = limită superioară). Pierderea fixă intră ca import suplimentar, ca în Auditor.
- Experimente: `<id>_settle_delta_vs_baseline_ron` (sloturi scorate) și `<id>_settle_delta_all_slots_ron` / `..._inv_at_import_ron` (toate sloturile procesate, preț implicit din rândul lanțului la sloturile cu preț completat).
- Valorile cash existente (`value_ron`, `delta_*`) rămân neschimbate; dashboardul trece pe decontare într-un pas următor, după validarea pe date live (REAL și STOCK_PPC trebuie să reproducă panoul „Contract”).
- Teste: cifrele din panoul Contract (70,34 / 62,37 RON), exemplele PPC (562,65 / 116,16), împărțire pe luni, regimuri, valori marginale, ciclare.

## 0.9.6
- Variantă experimentală nouă `sfinit_r0` (`soc_final` = `soc_init`, recompensă 0): al doilea tipar documentat de EMHASS pentru MPC rulant („capăt neutru”). La fiecare solve ținta finală e SOC-ul de la începutul acelui solve (`perfect.resolve_soc_final`). Lanț Perfect World separat, doar în laborator; se calculează de la început, iar tabelul comun se scurtează temporar până ajunge din urmă.

## 0.9.5
- Variantă experimentală nouă `sf0_r0` (`soc_final` 0, recompensă 0): tiparul din documentația EMHASS pentru MPC rulant (fără țintă efectivă la capătul orizontului). Lanț Perfect World separat, același start și aceeași fizică; producția și scoreboard-ul principal nu se ating. Se calculează de la început (≈745 sloturi) la primele cicluri.

## 0.9.4
- După repornirea HA, senzorii publicați prin REST (`..._scoreboard`, `..._experiments`) dispăreau până la următorul ciclu de 300 s (cardurile arătau „în așteptare”). Acum bucla de poll verifică la fiecare 30 s că senzorii există (404 → lipsă) și republică imediat; cel mult o dată pe minut. Erorile 502/timeout cât HA pornește nu declanșează nimic.

## 0.9.3
- Δ pe toate sloturile: fără „-0,000” în dashboard.

## 0.9.2
- Corecție 0.9.1: Δ pe toate sloturile includea doar sloturile cu preț în arhivă, deci tocmai sloturile cu preț completat (11:30 pe 05.11) rămâneau excluse. Acum prețul lipsă se deduce din rândul lanțului (cost / energie).

## 0.9.1
- Experimente: Δ față de referință pe TOATE sloturile procesate de ambele lanțuri (`<id>_delta_all_slots_ron`, `<id>_delta_all_slots_inv_at_import_ron`, `<id>_slots_all`), inclusiv sloturile cu preț completat. Motiv: la egalitate de preț între sloturi vecine, solver-ul poate muta un export într-un slot exclus din scorare (11:30 pe 05.11), ceea ce apărea ca pierdere falsă de −0,354 RON la `sf50_r0` și `sf30_r100`. `delta_vs_baseline_*` (pe sloturile scorate, comparabile cu REAL/STOCK) rămân neschimbate.

## 0.9.0
- Diagnostic nou pe fiecare rând de slot: `plan_soc_horizon_end` (SOC-ul planului la ultimul pas al orizontului) și `plan_soc_horizon_min` (cel mai jos SOC din plan). Arată direct dacă ținta `soc_final` / recompensa sunt binding. Rândurile vechi nu le au (None).
- Variante experimentale noi: `sf30_r0` (replică a referinței — verifică reproductibilitatea, Δ trebuie să fie 0), `sf30_r100` (recompensă 1,0, testează explicit riscul de încărcare din rețea pentru recompensă) și `sf50_r0` (țintă 50%). Plafonul de recompensă din teste: 1,0.
- Lanțurile experimentale se reiau de la start o singură dată (marcaj `diag` = 2), ca să aibă diagnosticul pe toată perioada; lanțul principal și scoreboard-ul nu se ating.

## 0.8.0
- Variante experimentale ale țintei de SOC la sfârșitul orizontului (`experiments.py`): `soc_final` 0,2/recompensă 0, 0,3/0,5 și 0,3/0,7 (`battery_soc_final_reward_factor`), fiecare ca lanț Perfect World separat (aceeași arhivă, aceeași fizică măsurată, același start). Config și `soc_final` suprascrise doar în copia laboratorului; producția și scoreboard-ul principal nu se ating. Referința este Perfect World principal (0,3 / recompensă 0).
- Senzor nou `sensor.emhass_perfect_world_experiments` (starea = sloturi comparate; atribute `<id>_value_ron`, `<id>_delta_vs_baseline_ron`, import/export, SOC final) și `experiments/summary.json`. Perioadă comună pentru toate lumile.
- Fiecare lume are acum și `value_inv_at_import_ron`: energia rămasă în baterie evaluată la prețul de import (limită superioară), pe lângă evaluarea la preț de export (conservatoare).
- O eroare într-o variantă nu oprește celelalte variante, lanțul principal sau arhivarea.

## 0.7.0
- `physics.py`: UN singur set de fizică măsurată (26.10–05.11, 927 sloturi): baterie 0.975 pe sens (dus-întors ≈0.95), DC→AC 0.99, AC→DC 0.95 (presupus, nemăsurabil). Folosit de Perfect World, replay-ul STOCK și inventarul SOC. Planificatorul EMHASS păstrează configul de producție; pierderea fixă rămâne strat separat.
- Perfect World: `realize` refăcut. Din plan se păstrează fluxurile bateriei (bus DC) și curtailment-ul; rețeaua și SOC-ul rezultă din fizica măsurată, cu limite reale de SOC. Rândul păstrează `plan_import_kwh`, `plan_export_kwh`, `plan_soc_end`, `physics`, `battery_limited`. Lanțul propagă SOC-ul realizat.
- STOCK_PPC / STOCK_HYDRO_V2: replay STOCK portat în laborator (verificat pe 966 sloturi din arhivă: cu eficiențele vechi 0.99/0.99/0.97 reproduce valorile auditorului cu 0 diferențe), rulat cu fizica măsurată. Valorile `stock_*` din arhivă nu mai intră în scoreboard (au eficiențele vechi); senzorii STOCK din HA rămân neschimbați.
- Inventar SOC: eta = eta_dis × eta_dc_ac din fizica comună, cu corecția diferenței de SOC de la începutul perioadei (un replay resetat nu pornește mereu din SOC-ul real).
- Fix: valorile foarte mici sunt serializate de HA ca șir (`"1e-05"`); `schema.num` le acceptă (înainte truncheau orizontul Perfect World și goleau replay-ul STOCK).
- Scoreboard expune `physics`, `eta_batt_ch`, `eta_batt_dis`, `eta_dc_ac`, `eta_ac_dc`.

## 0.6.2
- Sloturile cu salt de contor în arhivă (export imposibil fizic: peste ce pot produce descărcarea + PV − încărcare − consum + import) sunt excluse din scoreboard pentru TOATE lumile (aceeași perioadă). Senzorul expune `excluded_counter_anomaly_slots` și `excluded_counter_anomaly_epochs`.

## 0.6.1
- Senzorul scoreboard există și fără rezultate (stare unknown, status WAITING_FULL_HORIZON).

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
