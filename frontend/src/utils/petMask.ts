export type AlphaMask = { width: number; height: number; data: Uint8Array };

/** 从精灵帧/画布生成窗口内 alpha 掩码（1 = 可交互）。 */
export function imageAlphaMask(image: HTMLImageElement | HTMLCanvasElement, width: number, height: number): AlphaMask {
  const w = Math.max(1, Math.round(width));
  const h = Math.max(1, Math.round(height));
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) return { width: w, height: h, data: new Uint8Array(w * h) };
  context.clearRect(0, 0, w, h);
  context.drawImage(image, 0, 0, w, h);
  const pixels = context.getImageData(0, 0, w, h).data;
  const data = new Uint8Array(w * h);
  for (let index = 0; index < data.length; index += 1) {
    data[index] = pixels[index * 4 + 3] > 24 ? 1 : 0;
  }
  return { width: w, height: h, data };
}

/** 按 object-fit: contain / object-position: center bottom 的布局生成窗口内 alpha 掩码。 */
export function containedImageMask(image: HTMLImageElement | HTMLCanvasElement, width: number, height: number): AlphaMask {
  const w = Math.max(1, Math.round(width));
  const h = Math.max(1, Math.round(height));
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) return { width: w, height: h, data: new Uint8Array(w * h) };
  context.clearRect(0, 0, w, h);
  const sourceWidth = Number((image as HTMLImageElement).naturalWidth) || image.width || w;
  const sourceHeight = Number((image as HTMLImageElement).naturalHeight) || image.height || h;
  const scale = Math.min(w / Math.max(1, sourceWidth), h / Math.max(1, sourceHeight));
  const drawnWidth = Math.max(1, sourceWidth * scale);
  const drawnHeight = Math.max(1, sourceHeight * scale);
  context.drawImage(image, (w - drawnWidth) / 2, h - drawnHeight, drawnWidth, drawnHeight);
  const pixels = context.getImageData(0, 0, w, h).data;
  const data = new Uint8Array(w * h);
  for (let index = 0; index < data.length; index += 1) {
    data[index] = pixels[index * 4 + 3] > 24 ? 1 : 0;
  }
  return { width: w, height: h, data };
}

/** 把播放器上报的 0/255 网格转换成同一掩码结构。 */
export function gridMask(width: number, height: number, values: ArrayLike<number>): AlphaMask {
  const w = Math.max(1, Math.round(width));
  const h = Math.max(1, Math.round(height));
  const data = new Uint8Array(w * h);
  for (let index = 0; index < data.length && index < values.length; index += 1) {
    data[index] = Number(values[index]) > 0 ? 1 : 0;
  }
  return { width: w, height: h, data };
}

/** 命中判断；radius 提供与旧版一致的扩边容差。 */
export function hitTestMask(mask: AlphaMask, x: number, y: number, radius = 2): boolean {
  const centerX = Math.round(x);
  const centerY = Math.round(y);
  if (centerX < -radius || centerY < -radius || centerX > mask.width + radius || centerY > mask.height + radius) {
    return false;
  }
  for (let offsetY = -radius; offsetY <= radius; offsetY += 1) {
    for (let offsetX = -radius; offsetX <= radius; offsetX += 1) {
      const px = centerX + offsetX;
      const py = centerY + offsetY;
      if (px < 0 || py < 0 || px >= mask.width || py >= mask.height) continue;
      if (mask.data[py * mask.width + px]) return true;
    }
  }
  return false;
}
