# EMHASS Perfect World (lab)

La pornire citește versiunea add-on-ului EMHASS de producție prin Supervisor și instalează
exact `emhass==<versiune>` într-un venv persistent (`/data/venv`). Nu face rebuild la update-uri EMHASS:
la repornire se aliniază singur. `emhass_version` poate fi fixat manual (altceva decât `auto`).

Nu atinge `/share/emhass` și nu scrie nimic în producție.
