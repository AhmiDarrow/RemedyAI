import urllib.request, json, time, sys

API = "http://127.0.0.1:8188"
p_id = sys.argv[1] if len(sys.argv) > 1 else "263a4246-08ea-4de4-abb1-eb02666dcc22"

start = time.time()
while time.time() - start < 600:
    req = urllib.request.Request(f"{API}/history/{p_id}")
    history = json.loads(urllib.request.urlopen(req).read())
    if p_id in history:
        print("Completed!")
        outputs = history[p_id].get("outputs", {})
        for nid, node_out in outputs.items():
            for img in node_out.get("images", []):
                fname = img["filename"]
                sf = img.get("subfolder", "")
                print(f"  Image: {fname} folder={sf}")
        sys.exit(0)
    req2 = urllib.request.Request(f"{API}/queue")
    q = json.loads(urllib.request.urlopen(req2).read())
    running = any(item[1] == p_id for item in q.get("queue_running", []))
    pending = any(item[1] == p_id for item in q.get("queue_pending", []))
    elapsed = int(time.time() - start)
    print(f"{elapsed}s: running={running}, pending={pending}", flush=True)
    if not running and not pending:
        print("Job not in queue - checking error status")
        break
    time.sleep(5)

print("Timed out or job vanished")
