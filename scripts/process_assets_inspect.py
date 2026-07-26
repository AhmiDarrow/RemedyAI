import collections

import numpy as np
from PIL import Image

for name in ["assets/remedy_icon.png", "assets/remedy_logo.png"]:
    im = Image.open(name).convert("RGBA")
    arr = np.array(im)
    print("===", name, "===")
    print("size", im.size, "dtype", arr.dtype)
    rgb = arr[:, :, :3].astype(np.int16)
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    lum = (0.2126 * r + 0.7152 * g + 0.0722 * b)
    print("lum min/mean/max", float(lum.min()), float(lum.mean()), float(lum.max()))
    print("r min/mean/max", int(r.min()), float(r.mean()), int(r.max()))
    print("g min/mean/max", int(g.min()), float(g.mean()), int(g.max()))
    print("b min/mean/max", int(b.min()), float(b.mean()), int(b.max()))

    # teal-ish: g and b high relative, cyan/teal
    teal = (g > r + 20) & (b > r + 10) & (g > 60)
    gold = (r > 80) & (g > 60) & (r > b + 20) & (g > b)
    bright = lum > 80
    not_dark_bg = lum > 45
    print("teal px", int(teal.sum()), "gold px", int(gold.sum()), "bright>80", int(bright.sum()), "lum>45", int(not_dark_bg.sum()))

    # sample some teal/gold pixels
    ys, xs = np.where(teal)
    if len(ys):
        for i in range(0, min(5, len(ys)), 1):
            y, x = ys[i * len(ys) // 5], xs[i * len(ys) // 5]
            print(" teal sample", (x, y), arr[y, x].tolist())
    ys, xs = np.where(gold)
    if len(ys):
        for i in range(0, min(5, len(ys)), 1):
            y, x = ys[i * len(ys) // 5], xs[i * len(ys) // 5]
            print(" gold sample", (x, y), arr[y, x].tolist())

    # top colors via RGB quantize
    rgb_im = im.convert("RGB")
    q = rgb_im.quantize(colors=16, method=Image.Quantize.MEDIANCUT)
    palette = q.getpalette()[:48]
    counts = collections.Counter(q.getdata())
    print("top colors:")
    for idx, c in counts.most_common(16):
        rr, gg, bb = palette[idx * 3 : idx * 3 + 3]
        print(f"  #{rr:02x}{gg:02x}{bb:02x} rgb({rr},{gg},{bb}) count={c} ({100 * c / arr.shape[0] / arr.shape[1]:.1f}%)")

    # find bbox of non-background: bg is dark blue-ish low lum
    # Use distance from median corner color
    corners = np.array(
        [
            arr[0, 0, :3],
            arr[0, -1, :3],
            arr[-1, 0, :3],
            arr[-1, -1, :3],
        ],
        dtype=np.float32,
    )
    bg = corners.mean(axis=0)
    print("bg estimate", bg.tolist())
    dist = np.linalg.norm(arr[:, :, :3].astype(np.float32) - bg, axis=2)
    print("dist min/mean/max", float(dist.min()), float(dist.mean()), float(dist.max()))
    for thr in [15, 25, 35, 50, 70, 100]:
        mask = dist > thr
        print(f"  dist>{thr}: {int(mask.sum())} px ({100*mask.mean():.2f}%)")
        if mask.any():
            ys, xs = np.where(mask)
            print(f"    bbox x={xs.min()}-{xs.max()} y={ys.min()}-{ys.max()}")
