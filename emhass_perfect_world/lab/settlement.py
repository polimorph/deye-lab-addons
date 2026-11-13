"""Decontare lunară PPC MaxControl pentru sloturi PT15M (funcții pure, fără I/O).

Reguli confirmate în scris de PPC (răspunsul din 09–11.11.2026), aceleași ca în Auditorul v0.3.0:
  * compensare cantitativă lunară (kWh contra kWh), pe luna calendaristică;
  * energia consumată: preț pe interval π_c(t) = min(max(PZU−TG, 0), plafon 0,999) + componenta fixă;
    π̄_c = media ponderată pe consum; partea compensată = min(C, P) (la π̄_c, deci nu apare în cost);
  * deficit (C > P): (C−P) se plătește la π̄_c, cu TVA;
  * surplus (P > C): (P−C) se încasează la π̄_inj = media ponderată pe livrare a max(PZU−TG, 0), fără TVA;
  * tarifele reglementate, contribuțiile și accizele se aplică pe consumul BRUT, cu TVA, indiferent de compensare.
Ipoteze neconfirmate (parametri în `Rules`): componenta fixă 0,069 vs 0,095, scăderea TG la consum, plafonul pe interval,
contorul facturat. Prima factură PPC este testul final.

Convenție: cost pozitiv = cheltuit. `value` = −cost − inventar (pozitiv = mai bine), ca în scoreboard."""
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = "Europe/Bucharest"


@dataclass(frozen=True)
class Rules:
    vat: float = 0.21
    cap: float = 0.999            # plafon pe componenta PZU a energiei consumate, per interval, fără TVA
    regulated: float = 0.5064632  # tarife reglementate + contribuții + acciză, RON/kWh pe consum brut, fără TVA
    fixed: float = 0.069          # 0,095 − 0,026 dezechilibru estimat (ipoteza Auditorului; PPC scrie 0,095)
    tg: float = 0.00363
    tg_on_consumption: bool = True   # prețul de export din arhivă este deja PZU−TG; False adaugă TG înapoi la consum


DEFAULT_RULES = Rules()


def consumption_price(pe, rules=DEFAULT_RULES):
    """π_c(t) fără TVA, din prețul de export al slotului pe = max(PZU−TG, 0)."""
    v = pe if (rules.tg_on_consumption or pe <= 0) else pe + rules.tg
    return min(max(v, 0.0), rules.cap) + rules.fixed


def settle_totals(c, p, pi_c, pi_inj, rules=DEFAULT_RULES):
    """Decontarea unei luni din totaluri: C, P (kWh), π̄_c și π̄_inj (RON/kWh, fără TVA)."""
    k = 1.0 + rules.vat
    regulated = rules.regulated * c * k
    deficit = max(c - p, 0.0)
    surplus = max(p - c, 0.0)
    energy = deficit * pi_c * k
    surplus_value = surplus * pi_inj
    if deficit > 0:     # regim deficit: un kWh exportat reduce deficitul; un kWh importat evitat reduce și tarifele brute
        v_export = pi_c * k
        v_import = (rules.regulated + pi_c) * k
    else:               # regim surplus: un kWh în plus la export se încasează la π̄_inj
        v_export = pi_inj
        v_import = rules.regulated * k + pi_inj
    return {
        "regime": "DEFICIT" if deficit > 0 else "SURPLUS", "import_kwh": c, "export_kwh": p,
        "balance_kwh": p - c, "compensated_kwh": min(c, p), "deficit_kwh": deficit, "surplus_kwh": surplus,
        "pi_c": pi_c, "pi_inj": pi_inj, "regulated_ron": regulated, "energy_ron": energy,
        "surplus_value_ron": surplus_value, "cost_ron": regulated + energy - surplus_value,
        "v_export": v_export, "v_import": v_import,
    }


def _flows(imp, exp, loss, loss_mode):
    """Pierderea fixă a invertorului (consum în afara LOAD-ului canonic) ca import suplimentar (paritate cu Auditorul
    v0.3.0) sau scăzută din fluxul net al slotului ('net')."""
    if loss:
        if loss_mode == "net":
            net = exp - imp - loss
            return (0.0, net) if net > 0 else (-net, 0.0)
        return imp + loss, exp
    return imp, exp


def settle_slots(slots, rules=DEFAULT_RULES, loss_kwh=0.0, loss_mode="extra_import"):
    """Decontarea unei perioade tratate ca o singură lună. slots: iterabil de (import_kwh, export_kwh, pe)."""
    c = p = wc = wp = 0.0
    for imp, exp, pe in slots:
        imp, exp = _flows(imp, exp, loss_kwh, loss_mode)
        c += imp
        p += exp
        wc += imp * consumption_price(pe, rules)
        wp += exp * max(pe, 0.0)
    return settle_totals(c, p, wc / c if c > 0 else 0.0, wp / p if p > 0 else 0.0, rules)


def settle_months(slots, rules=DEFAULT_RULES, loss_kwh=0.0, loss_mode="extra_import", tz=TZ):
    """Decontare pe luni calendaristice locale. slots: (epoch, import_kwh, export_kwh, pe). Costul total = suma lunilor;
    regimul și valorile marginale sunt cele ale ultimei luni (cea în curs)."""
    z = ZoneInfo(tz)
    by_month = {}
    for epoch, imp, exp, pe in slots:
        by_month.setdefault(datetime.fromtimestamp(epoch, z).strftime("%Y-%m"), []).append((imp, exp, pe))
    if not by_month:
        return None
    months = {m: settle_slots(s, rules, loss_kwh, loss_mode) for m, s in sorted(by_month.items())}
    last = months[max(months)]
    return {
        "months": months, "month": max(months), "cost_ron": sum(m["cost_ron"] for m in months.values()),
        "regime": last["regime"], "v_export": last["v_export"], "v_import": last["v_import"],
        "import_kwh": sum(m["import_kwh"] for m in months.values()),
        "export_kwh": sum(m["export_kwh"] for m in months.values()),
        "pi_c": last["pi_c"], "pi_inj": last["pi_inj"], "balance_kwh": last["balance_kwh"],
    }
