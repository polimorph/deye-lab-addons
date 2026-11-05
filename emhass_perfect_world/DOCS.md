# EMHASS Perfect World (lab)

La pornire citește versiunea add-on-ului EMHASS de producție prin Supervisor și instalează
exact `emhass==<versiune>` într-un venv persistent (`/data/venv`). Nu face rebuild la update-uri EMHASS:
la repornire se aliniază singur. `emhass_version` poate fi fixat manual (altceva decât `auto`).

Nu atinge `/share/emhass` și nu scrie nimic în producție.

## Arhivă sloturi
Citește `sensor.deye_economic_auditor_scenario_capture` (o înregistrare la fiecare slot de 15 min) și o păstrează în
`/share/deye_lab/pw_lab/slots/<zi>.jsonl`. La pornire completează din istoricul HA (recorder ~10 zile).
Un slot recalculat devine o revizie nouă (`rev`); cititorul ia ultima. `status.json` arată acoperirea zilei.
Add-on-ul are acces rw la `/share`, dar scrie doar sub `data_dir`.
