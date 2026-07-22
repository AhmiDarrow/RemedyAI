import urllib.request, json, time

API = "http://127.0.0.1:8188"

wf = {
    "10": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux-2-klein-base-9b-fp8.safetensors", "weight_dtype": "fp8_e4m3fn"}},
    "11": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_8b_fp8mixed.safetensors", "type": "flux2"}},
    "12": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors"}},
    "13": {"class_type": "CLIPTextEncode", "inputs": {"text": "test astronaut cat on moon, digital art", "clip": ["11", 0]}},
    "14": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry, bad", "clip": ["11", 0]}},
    "15": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
    "16": {"class_type": "KSampler", "inputs": {
        "seed": 42, "steps": 10, "cfg": 3.5, "sampler_name": "euler", "scheduler": "simple",
        "denoise": 1.0, "model": ["10", 0], "positive": ["13", 0], "negative": ["14", 0],
        "latent_image": ["15", 0]
    }},
    "17": {"class_type": "VAEDecode", "inputs": {"samples": ["16", 0], "vae": ["12", 0]}},
    "18": {"class_type": "SaveImage", "inputs": {"filename_prefix": "test_", "images": ["17", 0]}}
}

payload = json.dumps({"prompt": wf}).encode("utf-8")
req = urllib.request.Request(f"{API}/prompt", data=payload, headers={"Content-Type": "application/json"})
try:
    resp = urllib.request.urlopen(req)
    data = json.loads(resp.read())
    p_id = data["prompt_id"]
    print(f"Queued! prompt_id={p_id}")
    start = time.time()
    while time.time() - start < 120:
        req2 = urllib.request.Request(f"{API}/history/{p_id}")
        history = json.loads(urllib.request.urlopen(req2).read())
        if p_id in history:
            print("Completed!")
            print(json.dumps(history[p_id]["outputs"], indent=2)[:800])
            break
        print(f"Waiting... ({int(time.time()-start)}s)")
        time.sleep(3)
except urllib.error.HTTPError as e:
    print(f"Error {e.code}: {e.read().decode()[:1000]}")
