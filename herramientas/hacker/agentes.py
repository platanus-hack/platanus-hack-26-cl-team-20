import json, sys
try:
    agents = json.load(sys.stdin)["result"]["agents"]
except Exception:
    agents = []
for a in agents:
    name = (a.get("name") or a.get("agent") or "?")[:9]
    st = {"working": "RUN", "blocked": "WAIT", "idle": "idle", "done": "done"}.get(a.get("agent_status"), "?")
    print(f"{name:<9} {a.get('pane_id', '').split(':')[0]:<3} {st}")
