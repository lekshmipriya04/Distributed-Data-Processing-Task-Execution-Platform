"""Ray job template — real Ray + NumPy baseline CNN on a borrowed node (Phase 8).

``generate_ray_cnn_script`` emits a self-contained program that the
RuntimeManager runs with the node's cached Ray venv. It:

  1. reads a JSON payload ``{"rows": [[pixel_0..pixel_{side*side-1}, label], ...],
     "arch"?}`` on stdin (an image shard flattened to pixels + an int label),
  2. starts a local Ray (``ray.init(num_cpus=N)``) and trains a small CNN with
     **data-parallel minibatch gradients fanned out as ``@ray.remote`` tasks**,
  3. prints ``{"w": [...], "b": 0.0, "n": N, "arch": {"F","side","C"}}``.

The flat parameter layout ``[conv_w(F*9), conv_b(F), fc_w(flat*C), fc_b(C)]`` is
identical to the master-side ``ml_templates._evaluate_cnn`` and the CNN branch of
``onnx_export`` — so federated averaging, evaluation and ONNX export all reuse
the same bytes. Ray does the parallel compute; NumPy does the math.

The template is parameter-substituted (no f-string) to keep the NumPy/Ray body's
braces intact.
"""
from __future__ import annotations

RAY_CNN_TEMPLATE = r'''
import sys, json

def main():
    payload = json.loads(sys.stdin.read() or "{}")
    rows = payload.get("rows", [])
    if not rows:
        sys.stdout.write(json.dumps({"w": [], "b": 0.0, "n": 0}))
        return

    import math
    import numpy as np
    import ray

    LR = __LR__
    EPOCHS = __EPOCHS__
    F = __FILTERS__
    NUM_CLASSES = __NUM_CLASSES__
    NUM_CPUS = __NUM_CPUS__

    arch = payload.get("arch") or {}
    d = len(rows[0]) - 1
    side = int(arch.get("side", 0)) or int(math.isqrt(d))
    side -= side % 2
    if side < 4:
        side = 4
    px = side * side
    oh = side - 2
    ph = oh // 2
    flat = F * ph * ph
    data = np.array([r[:px] + [0.0] * (px - len(r[:d])) if len(r[:d]) < px else r[:px]
                     for r in rows], dtype=np.float64)
    X = data.reshape(-1, side, side)
    y = np.array([int(round(float(r[d]))) for r in rows], dtype=np.int64)
    C = int(arch.get("C", 0)) or int(y.max()) + 1
    if NUM_CLASSES > 0:
        C = NUM_CLASSES
    y = np.clip(y, 0, C - 1)

    fc_base = F * 9 + F
    total = F * 9 + F + flat * C + C
    rng = np.random.default_rng(1234)
    params = np.concatenate([
        rng.uniform(-math.sqrt(2.0 / 9.0), math.sqrt(2.0 / 9.0), F * 9),
        np.zeros(F),
        rng.uniform(-math.sqrt(2.0 / flat), math.sqrt(2.0 / flat), flat * C),
        np.zeros(C),
    ])

    def unpack(p):
        cw = p[:F * 9].reshape(F, 3, 3)
        cb = p[F * 9:F * 9 + F]
        fw = p[fc_base:fc_base + flat * C].reshape(flat, C)
        fb = p[fc_base + flat * C:]
        return cw, cb, fw, fb

    def forward(p, Xb):
        cw, cb, fw, fb = unpack(p)
        B = Xb.shape[0]
        conv = np.zeros((B, F, oh, oh))
        for i in range(oh):
            for j in range(oh):
                patch = Xb[:, i:i + 3, j:j + 3].reshape(B, 9)
                conv[:, :, i, j] = patch @ cw.reshape(F, 9).T + cb
        relu = np.maximum(conv, 0.0)
        pooled = np.zeros((B, F, ph, ph)); mask = np.zeros((B, F, ph, ph, 2), dtype=np.int64)
        for a in range(ph):
            for b in range(ph):
                win = relu[:, :, a*2:a*2+2, b*2:b*2+2].reshape(B, F, 4)
                idx = win.argmax(axis=2)
                pooled[:, :, a, b] = win.max(axis=2)
                mask[:, :, a, b, 0] = idx // 2; mask[:, :, a, b, 1] = idx % 2
        flatv = pooled.reshape(B, flat)
        logits = flatv @ fw + fb
        logits -= logits.max(axis=1, keepdims=True)
        e = np.exp(logits); probs = e / e.sum(axis=1, keepdims=True)
        return conv, relu, pooled, mask, flatv, probs

    @ray.remote
    def grad_chunk(p, Xb, yb):
        cw, cb, fw, fb = unpack(p)
        B = Xb.shape[0]
        conv, relu, pooled, mask, flatv, probs = forward(p, Xb)
        onehot = np.zeros_like(probs); onehot[np.arange(B), yb] = 1.0
        loss = float(-np.log(probs[np.arange(B), yb] + 1e-12).sum())
        dlogits = (probs - onehot)
        g_fw = flatv.T @ dlogits
        g_fb = dlogits.sum(axis=0)
        dflat = dlogits @ fw.T
        dpool = dflat.reshape(B, F, ph, ph)
        drelu = np.zeros_like(relu)
        for a in range(ph):
            for b in range(ph):
                for bi in range(B):
                    for f in range(F):
                        di, dj = mask[bi, f, a, b]
                        drelu[bi, f, a*2+di, b*2+dj] += dpool[bi, f, a, b]
        dconv = drelu * (conv > 0)
        g_cw = np.zeros((F, 3, 3)); g_cb = dconv.sum(axis=(0, 2, 3))
        for i in range(oh):
            for j in range(oh):
                patch = Xb[:, i:i + 3, j:j + 3]
                for f in range(F):
                    g_cw[f] += (dconv[:, f, i, j][:, None, None] * patch).sum(axis=0)
        grad = np.concatenate([g_cw.ravel(), g_cb, g_fw.ravel(), g_fb])
        return grad, loss, B

    ray.init(num_cpus=(NUM_CPUS or None), include_dashboard=False, ignore_reinit_error=True, log_to_driver=False)
    try:
        n = X.shape[0]
        workers = max(1, min(NUM_CPUS or 4, n))
        chunks = [(X[k::workers], y[k::workers]) for k in range(workers)]
        chunks = [c for c in chunks if len(c[0]) > 0]
        for _ in range(EPOCHS):
            futs = [grad_chunk.remote(params, xb, yb) for xb, yb in chunks]
            results = ray.get(futs)
            gsum = np.zeros(total); nsum = 0
            for grad, _loss, B in results:
                gsum += grad; nsum += B
            if nsum > 0:
                params -= LR * (gsum / nsum)
    finally:
        ray.shutdown()

    sys.stdout.write(json.dumps({
        "w": [float(v) for v in params], "b": 0.0, "n": int(X.shape[0]),
        "arch": {"F": int(F), "side": int(side), "C": int(C)},
    }))

main()
'''


def generate_ray_cnn_script(
    lr: float = 0.05, epochs: int = 5, filters: int = 8, num_classes: int = 0, num_cpus: int = 0
) -> str:
    return (
        RAY_CNN_TEMPLATE
        .replace("__LR__", repr(float(lr)))
        .replace("__EPOCHS__", str(int(epochs)))
        .replace("__FILTERS__", str(int(filters)))
        .replace("__NUM_CLASSES__", str(int(num_classes)))
        .replace("__NUM_CPUS__", str(int(num_cpus)))
    )
