"""Schelet runner (v0.1.0): verifică mediul și rămâne în viață.

Pașii următori:
  - arhivă sloturi PT15M în /data/archive (din istoricul HA)
  - rezolvare EMHASS cu PV/consum REALE ca prognoză (perfect world)
  - schemă normalizată de rezultat + senzori către HA
"""
import logging
import os
import time
from importlib.metadata import version

logging.basicConfig(
    level=os.environ.get("LAB_LOG_LEVEL", "info").upper(),
    format="%(asctime)s [lab] %(levelname)s %(message)s",
)
log = logging.getLogger("lab")


def selftest() -> None:
    log.info("emhass %s", version("emhass"))
    import emhass.optimization  # noqa: F401  (verifică dependențele grele: cvxpy, pandas)
    log.info("import emhass.optimization OK")


if __name__ == "__main__":
    selftest()
    while True:
        time.sleep(3600)
