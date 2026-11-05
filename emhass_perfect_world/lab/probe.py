"""Sentinel de compatibilitate: la fiecare versiune EMHASS nouă scrie în data_dir/probe.json
ce expune versiunea instalată (semnături, construcția emhass_conf, config de producție fără secrete).
Adaptorul se scrie pe baza acestui raport, nu pe presupuneri despre API-ul intern."""
import importlib
import inspect
import json
import os
import re
import urllib.request
from importlib.metadata import version

PROBE_REV = 2
PROD_API = "http://5b918bf2-emhass:5000"   # doar GET-uri, nu se apelează nicio acțiune
SECRET_KEY = re.compile(r"key|token|password|secret|username|latitude|longitude|altitude", re.I)


def _sig(obj):
    try:
        return f"{'async ' if inspect.iscoroutinefunction(obj) else ''}{inspect.signature(obj)}"
    except (TypeError, ValueError):
        return "?"


def _funcs(modname, names=None, prefixes=()):
    mod = importlib.import_module(modname)
    out = {}
    for n, o in inspect.getmembers(mod, inspect.isfunction):
        if o.__module__ != modname:
            continue
        if (names and n in names) or any(n.startswith(p) for p in prefixes):
            out[n] = _sig(o)
    return out


def _grep_source(modname, pattern, context=0, limit=80):
    src = inspect.getsource(importlib.import_module(modname)).splitlines()
    rx = re.compile(pattern)
    hits = []
    for i, line in enumerate(src):
        if rx.search(line):
            hits.append(f"{i + 1}: " + " | ".join(l.strip() for l in src[max(0, i - context): i + context + 1]))
            if len(hits) >= limit:
                break
    return hits


def run(data_dir):
    rep = {"emhass_version": version("emhass")}
    steps = {
        "command_line": lambda: _funcs(
            "emhass.command_line",
            names={"set_input_data_dict", "naive_mpc_optim", "publish_data", "perfect_forecast_optim", "dayahead_forecast_optim"},
            prefixes=("_prepare",),
        ),
        "utils": lambda: _funcs("emhass.utils", prefixes=("build_", "get_root", "get_yaml", "get_logger", "treat_runtimeparams", "set_df_index")),
        "web_server_emhass_conf": lambda: _grep_source("emhass.web_server", r"emhass_conf\[|emhass_conf\s*=|build_params|build_config|build_secrets", limit=60),
        "web_server_args": lambda: _grep_source("emhass.web_server", r"add_argument|--addon|--data_path|--config", limit=30),
        "web_server_routes": lambda: _grep_source("emhass.web_server", r"@app\.(route|get|post)|add_url_rule", limit=40),
        "web_server_setup_source": lambda: inspect.getsource(importlib.import_module("emhass.web_server")).splitlines()[1085:1215],
        "share_emhass_files": lambda: sorted(os.listdir("/share/emhass")) if os.path.isdir("/share/emhass") else "lipsă",
        "treat_runtimeparams_keys": lambda: sorted(set(re.findall(r'"([a-z_]+)"', inspect.getsource(importlib.import_module("emhass.utils").treat_runtimeparams))))[:400],
    }
    for k, fn in steps.items():
        try:
            rep[k] = fn()
        except Exception as exc:  # noqa: BLE001 — sentinel: raportăm, nu oprim
            rep[k] = f"EROARE: {exc!r}"
    # configul de producție, prin API-ul intern (GET), fără chei sensibile
    for route in ("/get-config", "/get-config/defaults"):
        try:
            with urllib.request.urlopen(PROD_API + route, timeout=15) as resp:
                cfg = json.load(resp)
            rep["prod" + route.replace("/", "_")] = {k: v for k, v in cfg.items() if not SECRET_KEY.search(k)} \
                if isinstance(cfg, dict) else cfg
        except Exception as exc:  # noqa: BLE001
            rep["prod" + route.replace("/", "_")] = f"EROARE: {exc!r}"
    rep["probe_rev"] = PROBE_REV
    path = os.path.join(data_dir, "probe.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, ensure_ascii=False, default=str)
    os.replace(tmp, path)
    return rep


def needs_run(data_dir):
    try:
        with open(os.path.join(data_dir, "probe.json"), encoding="utf-8") as fh:
            d = json.load(fh)
            return d.get("emhass_version") != version("emhass") or d.get("probe_rev") != PROBE_REV
    except (OSError, ValueError):
        return True
