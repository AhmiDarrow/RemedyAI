import urllib.request, urllib.parse, json, time, os

API_URL = "http://127.0.0.1:8188"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "assets")
os.makedirs(OUTPUT_DIR, exist_ok=True)

def make_flux_workflow(prompt, negative, width, height, seed=None, steps=20):
    if seed is None:
        seed = int(time.time()) % 1000000
    wf = {
        "10": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux-2-klein-base-9b-fp8.safetensors", "weight_dtype": "fp8_e4m3fn"}},
        "11": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_8b_fp8mixed.safetensors", "type": "flux2"}},
        "12": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors"}},
        "13": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["11", 0]}},
        "14": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["11", 0]}},
        "15": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "16": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": steps, "cfg": 3.5,
            "sampler_name": "euler", "scheduler": "simple",
            "denoise": 1.0, "model": ["10", 0], "positive": ["13", 0],
            "negative": ["14", 0], "latent_image": ["15", 0]
        }},
        "17": {"class_type": "VAEDecode", "inputs": {"samples": ["16", 0], "vae": ["12", 0]}},
        "18": {"class_type": "SaveImage", "inputs": {"filename_prefix": "remedy_", "images": ["17", 0]}}
    }
    return wf

def queue_prompt(workflow):
    payload = json.dumps({"prompt": workflow}).encode("utf-8")
    req = urllib.request.Request(f"{API_URL}/prompt", data=payload, headers={"Content-Type": "application/json"})
    result = json.loads(urllib.request.urlopen(req).read())
    return result["prompt_id"]

def poll_and_download(pid, dest_name, timeout=300):
    start = time.time()
    last_log = 0
    while time.time() - start < timeout:
        req = urllib.request.Request(f"{API_URL}/history/{pid}")
        history = json.loads(urllib.request.urlopen(req).read())
        if pid in history:
            h = history[pid]
            status_str = h.get("status", {}).get("status_str")
            if status_str == "error":
                msgs = h.get("status", {}).get("messages", [])
                for m in msgs:
                    if m[0] == "execution_error":
                        err = m[1].get("exception_message", "unknown")
                        raise RuntimeError(f"Generation failed: {err}")
            outputs = h.get("outputs", {})
            for nid, node_out in outputs.items():
                for img in node_out.get("images", []):
                    fname = img["filename"]
                    sf = img.get("subfolder", "")
                    params = urllib.parse.urlencode({"filename": fname, "subfolder": sf, "type": "output"})
                    img_req = urllib.request.Request(f"{API_URL}/view?{params}")
                    data = urllib.request.urlopen(img_req).read()
                    dest = os.path.join(OUTPUT_DIR, dest_name)
                    with open(dest, "wb") as f:
                        f.write(data)
                    print(f"  saved -> {dest} ({len(data)} bytes)")
                    return dest
            # no images yet, keep waiting
        elapsed = int(time.time() - start)
        if elapsed - last_log >= 10:
            print(f"  waiting... ({elapsed}s)", flush=True)
            last_log = elapsed
        time.sleep(2)
    raise TimeoutError("Generation timed out")

ICON_PROMPT = (
    "app icon for Remedy AI - a healing/medical AI brand, stylized caduceus "
    "symbol integrated with digital circuit patterns, snake wrapped around a "
    "glowing staff, geometric minimal vector style, dark navy background, "
    "teal and gold accents, clean sharp lines, professional tech logo, "
    "high quality, sharp focus, no text"
)

LOGO_PROMPT = (
    "horizontal wordmark logo for Remedy AI agent framework, bold modern "
    "sans-serif typography reading 'REMEDY', small healing caduceus icon "
    "beside the text, dark navy background, teal and gold gradient, "
    "clean corporate style, high contrast, sharp vector quality, "
    "no other text or words"
)

NEGATIVE = "blurry, low quality, distorted, ugly, deformed, watermark, signature, cropped, jpeg artifacts, bad anatomy, text"

def main():
    print("=== Remedy Branding Generator ===\n")

    print("[1/2] Generating icon (1024x1024)...")
    wf = make_flux_workflow(ICON_PROMPT, NEGATIVE, 1024, 1024, steps=20)
    pid = queue_prompt(wf)
    print(f"  queued: {pid}")
    poll_and_download(pid, "remedy_icon.png")
    print()

    print("[2/2] Generating logo (1024x512)...")
    wf = make_flux_workflow(LOGO_PROMPT, NEGATIVE, 1024, 512, steps=20)
    pid = queue_prompt(wf)
    print(f"  queued: {pid}")
    poll_and_download(pid, "remedy_logo.png")
    print()

    print(f"=== Done! Assets saved to {OUTPUT_DIR} ===")

if __name__ == "__main__":
    main()
