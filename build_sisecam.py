"""
sisecam/index.html -> dist/sisecam/ (arkheips.com'a yüklenecek bağımsız paket) + dist/sisecam.zip

- ../ yolları düzleştirilir, manifest ve eksik poster kaldırılır
- Videolar IDM yakalamasın diye: .mp4 yerine m/<n>.bin, ilk XOR_LEN byte XOR'lu.
  Sayfa fetch -> XOR çöz -> Blob(video/mp4) -> objectURL ile oynatır.
"""
import os
import re
import shutil

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "sisecam", "index.html")
OUT = os.path.join(ROOT, "dist", "sisecam")
KEY = b"arkhe-enerjimetre"
XOR_LEN = 4096

LOADER = """
  <script>
    // Videolar fetch + XOR çözme + blob URL ile yüklenir (indirme yöneticileri .mp4 yakalamasın)
    (function () {
      const KEY = new TextEncoder().encode('%s');
      const XOR_LEN = %d;
      const vids = [...document.querySelectorAll('video[data-v]')];
      let i = 0;
      async function load(v) {
        const buf = new Uint8Array(await (await fetch(v.dataset.v)).arrayBuffer());
        for (let j = 0; j < Math.min(XOR_LEN, buf.length); j++) buf[j] ^= KEY[j %% KEY.length];
        v.src = URL.createObjectURL(new Blob([buf], { type: 'video/mp4' }));
        if (v.closest('.video-scene.active')) v.play().catch(() => { });
      }
      async function worker() {
        while (i < vids.length) {
          const v = vids[i++];
          try { await load(v); } catch (e) { console.warn('video yüklenemedi', v.dataset.v, e); }
        }
      }
      for (let k = 0; k < 3; k++) worker();
    })();
  </script>
</body>""" % (KEY.decode(), XOR_LEN)


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(os.path.join(OUT, "m"))

    html = open(SRC, encoding="utf-8").read()
    html = html.replace('  <link rel="manifest" href="../manifest.json">\n', "")
    html = re.sub(r'\s*poster="\.\./panel_gorsel\.png"', "", html)

    mapping = {}

    def repl(m):
        attrs, path = m.group(1), m.group(2)
        if path not in mapping:
            mapping[path] = f"m/{len(mapping) + 1:02d}.bin"
        inner_attrs = attrs.replace(" autoplay", "").replace(' preload="auto"', "")
        return f'<video{inner_attrs} data-v="{mapping[path]}"></video>'

    html, n = re.subn(
        r'<video([^>]*)>\s*<source src="\.\./(videos/[^"]+\.mp4)" type="video/mp4">\s*</video>',
        repl, html)
    assert n and "<source" not in html, "video etiketi dönüştürülemedi"

    html = html.replace('="../', '="')
    assert "../" not in "".join(re.findall(r'(?:src|href)="[^"]*"', html))
    html = html.replace("</body>", LOADER, 1)

    for src, dst in mapping.items():
        data = bytearray(open(os.path.join(ROOT, src), "rb").read())
        for j in range(min(XOR_LEN, len(data))):
            data[j] ^= KEY[j % len(KEY)]
        open(os.path.join(OUT, dst), "wb").write(data)

    for f in ("logo.svg", "telegram_qr.svg"):
        shutil.copy(os.path.join(ROOT, f), OUT)

    open(os.path.join(OUT, "index.html"), "w", encoding="utf-8").write(html)

    zip_base = os.path.join(ROOT, "dist", "sisecam")
    if os.path.exists(zip_base + ".zip"):
        os.remove(zip_base + ".zip")
    shutil.make_archive(zip_base, "zip", os.path.join(ROOT, "dist"), "sisecam")
    print(f"{n} video etiketi, {len(mapping)} dosya -> {OUT}")


if __name__ == "__main__":
    main()
