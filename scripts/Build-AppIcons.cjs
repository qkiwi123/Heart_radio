// Render the selected vector master for Android launchers and Windows widgets.
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const sharp = process.env.HEARTRADIO_NODE_MODULES
  ? require(path.join(process.env.HEARTRADIO_NODE_MODULES, 'sharp')) : require('sharp');
const root = path.resolve(__dirname, '..');
const iconDir = path.join(root, 'assets/app-icon');
const resDir = path.join(root, 'app/src/main/res');
const sizes = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256];

function roundedRect({ x, y, width, height, rx = 0 }) {
  const r = Math.min(rx, width / 2, height / 2);
  return `M${x + r},${y}H${x + width - r}A${r},${r} 0 0 1 ${x + width},${y + r}V${y + height - r}A${r},${r} 0 0 1 ${x + width - r},${y + height}H${x + r}A${r},${r} 0 0 1 ${x},${y + height - r}V${y + r}A${r},${r} 0 0 1 ${x + r},${y}Z`;
}

async function main() {
  const original = await fs.readFile(path.join(iconDir, 'heartradio.svg'), 'utf8');
  const master = original.match(/<path id="turn-master" d="([^"]+)"/)[1];
  const rects = [...original.matchAll(/<rect\b([^>]+)\/>/g)].map(match => {
    const values = Object.fromEntries([...match[1].matchAll(/([\w-]+)="([^"]+)"/g)].map(a => [a[1], a[2]]));
    for (const name of ['x', 'y', 'width', 'height', 'rx']) values[name] = Number(values[name] || 0);
    return values;
  }).filter(rect => rect.width !== 1024);
  const devicePaths = [];
  for (let i = 0; i < rects.length; i++) {
    if (rects[i].fill !== '#000000') throw new Error('Expected a black outer shape');
    let d = roundedRect(rects[i]);
    if (rects[i + 1]?.fill === '#ffffff') d += roundedRect(rects[++i]);
    devicePaths.push(d);
  }
  const vectorPath = d => `        <path android:fillColor="#FF000000" android:fillType="evenOdd" android:pathData="${d}" />`;
  const vector = `<?xml version="1.0" encoding="utf-8"?>\n<!-- Generated from assets/app-icon/heartradio.svg. Keep the half-turn copy. -->\n<vector xmlns:android="http://schemas.android.com/apk/res/android" android:width="108dp" android:height="108dp" android:viewportWidth="1024" android:viewportHeight="1024">\n    <group android:pivotX="512" android:pivotY="512" android:scaleX="0.8888889" android:scaleY="0.8888889">\n${vectorPath(master)}\n        <group android:pivotX="512" android:pivotY="512" android:rotation="180">\n${vectorPath(master)}\n        </group>\n${devicePaths.map(vectorPath).join('\n')}\n    </group>\n</vector>\n`;
  await fs.writeFile(path.join(resDir, 'drawable/ic_launcher_foreground.xml'), vector);
  await fs.writeFile(path.join(resDir, 'drawable/ic_launcher_background.xml'), '<?xml version="1.0" encoding="utf-8"?>\n<color xmlns:android="http://schemas.android.com/apk/res/android" android:color="#FFFFFFFF" />\n');
  const legacy = original.replace('viewBox="0 0 1024 1024"', 'viewBox="128 128 768 768"');
  const inner = legacy.replace(/^<svg[^>]*>/, '').replace(/<\/svg>\s*$/, '');
  const round = `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="1024" height="1024" viewBox="128 128 768 768"><defs><clipPath id="launcher-circle"><circle cx="512" cy="512" r="384"/></clipPath></defs><g clip-path="url(#launcher-circle)">${inner}</g></svg>`;
  for (const [density, pixels] of Object.entries({ mdpi: 48, hdpi: 72, xhdpi: 96, xxhdpi: 144, xxxhdpi: 192 })) {
    await sharp(Buffer.from(legacy)).resize(pixels, pixels).webp({ lossless: true }).toFile(path.join(resDir, `mipmap-${density}/ic_launcher.webp`));
    await sharp(Buffer.from(round)).resize(pixels, pixels).webp({ lossless: true }).toFile(path.join(resDir, `mipmap-${density}/ic_launcher_round.webp`));
  }
  await sharp(Buffer.from(original)).resize(1024, 1024).png().toFile(path.join(iconDir, 'heartradio.png'));
  await sharp(Buffer.from(round)).resize(256, 256).png().toFile(path.join(iconDir, 'watch-launcher-preview.png'));
  const desktop = original.replace('viewBox="0 0 1024 1024"', 'viewBox="160 160 704 704"');
  const frames = [];
  for (const size of sizes) frames.push(await sharp(Buffer.from(desktop)).resize(size, size).ensureAlpha().png().toBuffer());
  const directory = Buffer.alloc(6 + 16 * frames.length);
  directory.writeUInt16LE(1, 2); directory.writeUInt16LE(frames.length, 4);
  let offset = directory.length;
  frames.forEach((frame, i) => {
    const pos = 6 + i * 16;
    directory[pos] = sizes[i] === 256 ? 0 : sizes[i];
    directory[pos + 1] = directory[pos];
    directory.writeUInt16LE(1, pos + 4); directory.writeUInt16LE(32, pos + 6);
    directory.writeUInt32LE(frame.length, pos + 8); directory.writeUInt32LE(offset, pos + 12);
    offset += frame.length;
  });
  await fs.writeFile(path.join(iconDir, 'heartradio.ico'), Buffer.concat([directory, ...frames]));
  await fs.writeFile(path.join(iconDir, 'desktop-icon-preview.png'), frames[frames.length - 1]);
  const hash = crypto.createHash('sha256').update(original).digest('hex');
  await fs.writeFile(path.join(iconDir, 'manifest.json'), JSON.stringify({ selected: 'B: bold devices, centrally symmetric', source: 'heartradio.svg', sourceSha256: hash, windowsSizes: sizes, androidAdaptiveForegroundScale: 8 / 9, androidLegacyViewport: [128, 128, 768, 768], windowsViewport: [160, 160, 704, 704] }, null, 2) + '\n');
  console.log(JSON.stringify({ sourceSha256: hash, windowsSizes: sizes, androidLegacyResources: 10 }));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
