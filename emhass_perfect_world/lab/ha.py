"""Client minimal pentru Home Assistant Core API (via Supervisor proxy). Doar stdlib."""
import json
import os
import urllib.parse
import urllib.request


class HA:
    def __init__(self, base=None, token=None):
        self.base = (base or os.environ.get("HA_API_BASE", "http://supervisor/core/api")).rstrip("/")
        self.token = token or os.environ.get("SUPERVISOR_TOKEN", "")

    def _get(self, path, params=None, timeout=30):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + self.token})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)

    def config(self):
        return self._get("/config")

    def state(self, entity_id):
        return self._get("/states/" + entity_id)

    def history(self, entity_id, start_iso, end_iso):
        """Lista de stări (cu atribute) ale unei entități, în ordine cronologică."""
        # significant_changes_only=0: senzorul rămâne VALID, slotul se schimbă doar în atribute
        params = {"filter_entity_id": entity_id, "significant_changes_only": "0", "end_time": end_iso}
        res = self._get("/history/period/" + urllib.parse.quote(start_iso, safe=""), params, timeout=180)
        return res[0] if res else []

    def post_state(self, entity_id, state, attributes):
        """Publică (suprascrie) starea unei entități. Dispare la repornirea HA: se republică periodic."""
        body = json.dumps({"state": state, "attributes": attributes}).encode()
        req = urllib.request.Request(self.base + "/states/" + entity_id, data=body, method="POST",
                                     headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
