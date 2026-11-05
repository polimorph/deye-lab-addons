"""Fizica comună a tuturor lumilor din laborator (măsurată pe istoricul 26.10–05.11.2026).
O singură sursă de adevăr pentru: realizarea planului în Perfect World, replay-ul STOCK și inventarul SOC.
Planificatorul EMHASS NU folosește acest set: el păstrează configul de producție (credința planificatorului);
fizica descrie ce se întâmplă de fapt cu energia. Pierderea fixă (acumulator v0.2.7) e un strat separat și
NU se include aici, ca să nu fie numărată de două ori."""
import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from .schema import num

TZ = ZoneInfo("Europe/Bucharest")


@dataclass(frozen=True)
class Physics:
    label: str
    eta_ch: float        # bus DC -> energie în celule
    eta_dis: float       # celule -> bus DC
    eta_dc_ac: float     # bus DC -> AC
    eta_ac_dc: float     # AC -> bus DC
    capacity_kwh: float = 10.24
    soc_min: float = 0.10
    soc_max: float = 1.0
    max_batt_kw: float = 5.12

    @property
    def eta_discharge_to_ac(self):
        """Fracțiunea din energia stocată care ajunge în AC (folosită la inventarul SOC)."""
        return self.eta_dis * self.eta_dc_ac


# Măsurat pe 927 sloturi MEASURED (Huber + bootstrap): dus-întors baterie ≈0.95 -> 0.975 pe sens; DC->AC ≈0.99;
# AC->DC nemăsurabil (fără încărcare din rețea în perioadă) -> 0.95 presupus.
MEASURED = Physics("measured-2026-11-05", eta_ch=0.975, eta_dis=0.975, eta_dc_ac=0.99, eta_ac_dc=0.95)
# Valorile vechi din replay-ul STOCK al auditorului v0.2.x (doar pentru testul de fidelitate).
LEGACY_STOCK = Physics("legacy-auditor-v0.2", eta_ch=0.99, eta_dis=0.99, eta_dc_ac=0.97, eta_ac_dc=0.97)

STOCK_FLOOR_PCT = 10.0
STOCK_SUMMER = ((20, 0), (8, 0))
STOCK_WINTER = ((17, 0), (9, 0))


def realize_flows(phys, soc0, pv_used_kwh, load_kwh, batt_charge_kwh, batt_discharge_kwh):
    """Realizează fluxurile bateriei planificate (energii pe bus DC, kWh/slot) cu fizica `phys`.
    Întoarce (import_kwh, export_kwh, soc_end, charge_bus, discharge_bus) după limitele reale ale bateriei."""
    cap = phys.capacity_kwh
    c = min(max(batt_charge_kwh, 0.0), max(phys.soc_max - soc0, 0.0) * cap / phys.eta_ch)
    d = min(max(batt_discharge_kwh, 0.0), max(soc0 - phys.soc_min, 0.0) * cap * phys.eta_dis)
    dc_net = pv_used_kwh + d - c
    ac = dc_net * phys.eta_dc_ac if dc_net >= 0 else dc_net / phys.eta_ac_dc
    grid = load_kwh - ac
    soc_end = soc0 + (c * phys.eta_ch - d / phys.eta_dis) / cap
    return max(grid, 0.0), max(-grid, 0.0), soc_end, c, d


def _in_window(epoch, summer=STOCK_SUMMER, winter=STOCK_WINTER):
    loc = dt.datetime.fromtimestamp(epoch, TZ)
    (sh, sm), (eh, em) = summer if 4 <= loc.month <= 9 else winter
    lm, a, b = loc.hour * 60 + loc.minute, sh * 60 + sm, eh * 60 + em
    return (lm >= a or lm < b) if a > b else (a <= lm < b)


def stock_usable(r):
    # RESET_PENDING (stock necunoscut) nu intră în replay: creează un gol, deci resetare din SOC-ul real la următorul rând
    return (r.get("capture_state") == "VALID"
            and num(r.get("load_kwh")) is not None and num(r.get("pv_actual_kwh")) is not None)


def stock_replay(rows, phys=MEASURED):
    """Replay STOCK (aceeași logică ca DeyeStockReplay_v0.2 din auditor), cu fizica `phys`.
    rows: rânduri de arhivă (orice ordine, o revizie per slot). Întoarce {epoch: dict(soc_start, soc_end, import, export)}.
    Stare între sloturi; după un gol sau la schimbarea segmentului se resetează din SOC-ul real;
    primul rând al istoricului pornește din SOC-ul STOCK capturat."""
    cap, floor = phys.capacity_kwh, STOCK_FLOOR_PCT
    max_b = phys.max_batt_kw * 0.25
    out, prev, prev_seg, soc1 = {}, None, None, None
    for r in sorted((x for x in rows if stock_usable(x)), key=lambda x: x["slot_start_epoch"]):
        ep = r["slot_start_epoch"]
        load, pv = num(r["load_kwh"]), num(r["pv_actual_kwh"])
        if prev is None:
            soc0 = num(r.get("stock_soc_start_pct"))
        elif ep - prev != 900 or r.get("replay_segment") != prev_seg:
            soc0 = num(r.get("actual_soc_start_pct"))
        else:
            soc0 = soc1
        if soc0 is None:
            soc0 = num(r.get("actual_soc_start_pct"))
        if soc0 is None:
            prev = None
            continue
        e0, ef = cap * soc0 / 100, cap * floor / 100
        pv_dc = min(pv, load / phys.eta_dc_ac)
        surplus, deficit = max(pv - pv_dc, 0.0), max(load - pv_dc * phys.eta_dc_ac, 0.0)
        gimp = gexp = 0.0
        if surplus > 1e-6:
            bc = min(surplus, max_b, max(cap - e0, 0.0) / phys.eta_ch)
            e1 = min(e0 + bc * phys.eta_ch, cap)
            gexp = max(surplus - bc, 0.0) * phys.eta_dc_ac
        else:
            bd = 0.0
            if _in_window(ep):
                bd = min(deficit / phys.eta_dc_ac, max_b, max(e0 - ef, 0.0) * phys.eta_dis)
            gimp = max(deficit - bd * phys.eta_dc_ac, 0.0)
            e1 = max(e0 - bd / phys.eta_dis, 0.0)
        soc1 = e1 / cap * 100
        out[ep] = {"soc_start": soc0, "soc_end": soc1, "import": gimp, "export": gexp}
        prev, prev_seg = ep, r.get("replay_segment")
    return out
