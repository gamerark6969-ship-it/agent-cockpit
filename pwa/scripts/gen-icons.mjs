// Generates placeholder PWA icons (dark background + emerald terminal mark).
// Usage: node scripts/gen-icons.mjs
import { deflateSync } from "node:zlib";
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const publicDir = join(__dirname, "..", "public");
mkdirSync(publicDir, { recursive: true });

const CRC_TABLE = (() => {
  const table = new Int32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c;
  }
  return table;
})();

function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i += 1) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const typeBuf = Buffer.from(type, "ascii");
  const crcBuf = Buffer.alloc(4);
  crcBuf.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])), 0);
  return Buffer.concat([len, typeBuf, data, crcBuf]);
}

function encodePng(width, height, pixels) {
  const sig = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 2; // color type RGB
  ihdr[10] = 0;
  ihdr[11] = 0;
  ihdr[12] = 0;
  const raw = Buffer.alloc(height * (width * 3 + 1));
  for (let y = 0; y < height; y += 1) {
    const rowStart = y * (width * 3 + 1);
    raw[rowStart] = 0;
    for (let x = 0; x < width; x += 1) {
      const src = (y * width + x) * 3;
      const dst = rowStart + 1 + x * 3;
      raw[dst] = pixels[src];
      raw[dst + 1] = pixels[src + 1];
      raw[dst + 2] = pixels[src + 2];
    }
  }
  return Buffer.concat([
    sig,
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

const BG = [10, 10, 10];
const FG = [16, 185, 129];

function distToSegment(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const lenSq = dx * dx + dy * dy || 1;
  let t = ((px - ax) * dx + (py - ay) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));
  const cx = ax + t * dx;
  const cy = ay + t * dy;
  return Math.hypot(px - cx, py - cy);
}

function insideRoundedRect(x, y, left, top, right, bottom, radius) {
  if (x < left || x > right || y < top || y > bottom) return false;
  const rx = Math.min(radius, (right - left) / 2);
  const ry = Math.min(radius, (bottom - top) / 2);
  const nearLeft = x < left + rx;
  const nearRight = x > right - rx;
  const nearTop = y < top + ry;
  const nearBottom = y > bottom - ry;
  if ((nearLeft || nearRight) && (nearTop || nearBottom)) {
    const cx = nearLeft ? left + rx : right - rx;
    const cy = nearTop ? top + ry : bottom - ry;
    return Math.hypot(x - cx, y - cy) <= rx;
  }
  return true;
}

function renderIcon(size) {
  const pixels = new Uint8Array(size * size * 3);
  const s = (v) => v * size;
  const stroke = s(0.055);
  const inset = s(0.16);
  const radius = s(0.24);

  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const px = x + 0.5;
      const py = y + 0.5;
      let color = BG;

      const outer = insideRoundedRect(
        px,
        py,
        inset,
        inset,
        size - inset,
        size - inset,
        radius,
      );
      const inner = insideRoundedRect(
        px,
        py,
        inset + stroke,
        inset + stroke,
        size - inset - stroke,
        size - inset - stroke,
        radius - stroke,
      );
      if (outer && !inner) {
        color = FG;
      } else {
        // chevron ">"
        const dChevron = Math.min(
          distToSegment(px, py, s(0.37), s(0.38), s(0.51), s(0.5)),
          distToSegment(px, py, s(0.51), s(0.5), s(0.37), s(0.62)),
        );
        // underscore
        const dUnderscore = distToSegment(px, py, s(0.55), s(0.62), s(0.67), s(0.62));
        const d = Math.min(dChevron, dUnderscore);
        if (d <= stroke * 0.85) color = FG;
      }

      const idx = (y * size + x) * 3;
      pixels[idx] = color[0];
      pixels[idx + 1] = color[1];
      pixels[idx + 2] = color[2];
    }
  }
  return encodePng(size, size, pixels);
}

function renderMaskable(size) {
  const pixels = new Uint8Array(size * size * 3);
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const idx = (y * size + x) * 3;
      pixels[idx] = FG[0];
      pixels[idx + 1] = FG[1];
      pixels[idx + 2] = FG[2];
    }
  }
  // dark terminal mark in the safe zone
  const s = (v) => v * size;
  const stroke = s(0.05);
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const px = x + 0.5;
      const py = y + 0.5;
      const dChevron = Math.min(
        distToSegment(px, py, s(0.37), s(0.38), s(0.51), s(0.5)),
        distToSegment(px, py, s(0.51), s(0.5), s(0.37), s(0.62)),
      );
      const dUnderscore = distToSegment(px, py, s(0.55), s(0.62), s(0.67), s(0.62));
      if (Math.min(dChevron, dUnderscore) <= stroke) {
        const idx = (y * size + x) * 3;
        pixels[idx] = BG[0];
        pixels[idx + 1] = BG[1];
        pixels[idx + 2] = BG[2];
      }
    }
  }
  return encodePng(size, size, pixels);
}

writeFileSync(join(publicDir, "icon-192.png"), renderIcon(192));
writeFileSync(join(publicDir, "icon-512.png"), renderIcon(512));
writeFileSync(join(publicDir, "icon-maskable-512.png"), renderMaskable(512));
console.log("icons written to public/");
