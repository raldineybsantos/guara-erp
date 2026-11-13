"""Remove o fundo branco do logo, mantendo os tons creme do animal."""
import sys
import numpy as np
from PIL import Image
from scipy import ndimage

src, dst = sys.argv[1], sys.argv[2]
im = np.asarray(Image.open(src).convert("RGB")).astype(np.float32)
minc = im.min(axis=2)
claro = minc > 225                                   # candidatos a fundo (tolerância p/ ruído do JPEG)
rotulos, n = ndimage.label(claro)
idx = np.arange(1, n + 1)
media = ndimage.mean(minc, rotulos, idx)
tam = ndimage.sum(claro, rotulos, idx)
# Fundo = regiões BRANCO PURO (média >= 248): o externo, o vão entre as patas e o miolo dos
# "nós" do circuito. O creme do animal (orelhas, peito, ponta da cauda) tem média ~227 e é mantido.
fundo_ids = idx[(media >= 248) & (tam > 40)]
fundo = np.isin(rotulos, fundo_ids)

# faixa de transição (antisserrilhado): pixels a até 3px do fundo ganham alfa proporcional à "não-brancura"
perto = ndimage.binary_dilation(fundo, iterations=3) & ~fundo
alfa = np.ones(minc.shape, np.float32)
alfa[fundo] = 0
a_borda = np.clip((255 - minc) / (255 - 120), 0, 1)
alfa[perto] = a_borda[perto]

# descontamina a cor (remove o branco misturado nas bordas)
a3 = np.maximum(alfa[..., None], 1e-3)
cor = np.where(perto[..., None], np.clip((im - 255 * (1 - a3)) / a3, 0, 255), im)
rgba = np.dstack([cor, alfa * 255]).astype(np.uint8)
out = Image.fromarray(rgba, "RGBA")
out = out.crop(out.getbbox())                        # recorta as sobras transparentes
w, h = out.size; lado = max(w, h); pad = int(lado * 0.04)
quadro = Image.new("RGBA", (lado + 2 * pad, lado + 2 * pad), (0, 0, 0, 0))
quadro.paste(out, ((lado - w) // 2 + pad, (lado - h) // 2 + pad), out)
quadro.save(dst)
print("ok", quadro.size, "transparente:", round(float((alfa == 0).mean()) * 100, 1), "%")
