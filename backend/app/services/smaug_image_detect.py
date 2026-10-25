"""Smaug AI Image Detector wrapper.

Vendored from abacusai/Smaug-AI-Image-Detector (Apache-2.0) so TAYO does
not execute Python downloaded from the model hub at runtime.

Source:
https://huggingface.co/abacusai/Smaug-AI-Image-Detector/blob/main/smaug_image_detect.py

    from smaug_image_detect import SmaugImageDetect
    detector = SmaugImageDetect.from_pretrained('abacusai/Smaug-AI-Image-Detector')
    detector('photo.jpg')                 # -> 0.97  (probability the image is AI-generated)
    detector(['a.jpg', pil_image, ...])   # -> [0.97, 0.02, ...]
    detector('photo.jpg', crops=1)        # 3x faster, slightly less accurate

Runs on NVIDIA GPUs (CUDA), Apple Silicon (Metal / MPS) and CPU; the device is
picked automatically.

Upstream code by Abacus.AI, licensed under the Apache License, Version 2.0.
See third_party/smaug/LICENSE in this repository.
"""

import math

import torch
from PIL import Image, ImageOps
from sentence_transformers import SentenceTransformer


CROP, SIDE, BUDGET = 528, 576, 140  # must match training: shorter side -> 576, 528 crops, 140 vision tokens


def views(img, crops=3):
    """Shorter side to 576 px (upscaled to 528 if smaller), then 528 x 528 crops at native resolution:
    crops=3 -> start, centre and end of the long side (default; covers most of the image), crops=1 -> centre only."""
    img = ImageOps.exif_transpose(img).convert('RGB')
    w, h = img.size
    if min(w, h) > SIDE:
        s = SIDE / min(w, h)
        img = img.resize((round(w * s), round(h * s)), Image.LANCZOS)
    elif min(w, h) < CROP:
        s = CROP / min(w, h)
        img = img.resize((math.ceil(w * s), math.ceil(h * s)), Image.BICUBIC)
    w, h = img.size
    cx, cy = (w - CROP) // 2, (h - CROP) // 2
    if crops == 1:
        return [img.crop((cx, cy, cx + CROP, cy + CROP))]
    if w >= h:
        return [img.crop((x, cy, x + CROP, cy + CROP)) for x in (0, cx, w - CROP)]
    return [img.crop((cx, y, cx + CROP, y + CROP)) for y in (0, cy, h - CROP)]


class SmaugImageDetect:
    def __init__(self, st):
        self.st = st
        ip = st[0].processor.image_processor
        ip.max_soft_tokens = ip.image_seq_length = BUDGET

    @classmethod
    def from_pretrained(cls, name_or_path, device=None):
        # NVIDIA GPU, then Apple Silicon (Metal), then CPU. bf16 on CUDA; float32 elsewhere (this model is unstable in fp16)
        device = device or ('cuda' if torch.cuda.is_available() else 'mps' if torch.backends.mps.is_available() else 'cpu')
        return cls(SentenceTransformer(name_or_path, device=device, config_kwargs={'audio_config': None},
                                       model_kwargs={'torch_dtype': torch.bfloat16 if device == 'cuda' else torch.float32}))

    @torch.no_grad()
    def __call__(self, images, crops=3, batch_size=16):
        assert crops in (1, 3)
        single = not isinstance(images, (list, tuple))
        imgs = [Image.open(i) if isinstance(i, str) else i for i in ([images] if single else images)]
        flat = [{'image': v} for i in imgs for v in views(i, crops)]
        logits = self.st.encode(flat, batch_size=batch_size, convert_to_tensor=True).float().view(len(imgs), crops)
        probs = torch.sigmoid(logits.mean(1)).tolist()  # average the crops' logits, then convert to a probability
        return probs[0] if single else probs
