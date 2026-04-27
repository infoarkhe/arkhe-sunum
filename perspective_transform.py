"""
Canlı yayın slaytı için perspektif transform üretici.

İş akışı:
  1. Playwright ile VDO.ninja yayınından frame çeker (frame.png)
  2. OpenCV penceresinde 4 köşeyi sırayla tıklarsın (SOL ÜST → SAĞ ÜST → SAĞ ALT → SOL ALT)
  3. Bu 4 noktayı iframe'in 4 köşesine homografi ile eşleyip CSS matrix3d() üretir
  4. (Opsiyonel) sunum_linkedin.html içindeki #live-stream'e direkt uygular

Kullanım:
  pip install playwright opencv-python numpy
  python -m playwright install chromium
  python perspective_transform.py

Bağımlılıklar yüklü değilse adım adım kurmanı söyler.
"""

import sys
import os
import argparse

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(THIS_DIR, 'sunum_linkedin.html')
STREAM_URL = 'https://vdo.ninja/?view=arkhesunum&room=azad&solo&cover&transparent&autostart&noaudio'
DEFAULT_FRAME = os.path.join(THIS_DIR, 'frame.png')


def capture_frame(out_path=DEFAULT_FRAME, wait_ms=10000):
    """Playwright ile yayından frame çeker. Headed mod (görünür browser) — WebRTC için gerekli."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright yüklü değil. Kur: pip install playwright && python -m playwright install chromium")
        sys.exit(1)

    print(f"[1/3] Tarayıcı açılıyor, yayına bağlanılıyor... ({wait_ms/1000:.0f}sn bekleme)")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=[
            '--use-fake-ui-for-media-stream',
            '--autoplay-policy=no-user-gesture-required',
        ])
        ctx = browser.new_context(viewport={'width': 1280, 'height': 720})
        page = ctx.new_page()
        page.goto(STREAM_URL)
        page.wait_for_timeout(wait_ms)
        # Sadece video elementinin frame'ini al — yoksa full sayfayı çek
        try:
            video = page.query_selector('video')
            if video:
                video.screenshot(path=out_path)
            else:
                page.screenshot(path=out_path)
        except Exception:
            page.screenshot(path=out_path)
        browser.close()
    print(f"[1/3] Frame kaydedildi → {out_path}")
    return out_path


def select_4_points(image_path):
    """OpenCV penceresinde 4 köşe seçtirir. Sıra: SOL ÜST → SAĞ ÜST → SAĞ ALT → SOL ALT."""
    try:
        import cv2
    except ImportError:
        print("OpenCV yüklü değil. Kur: pip install opencv-python")
        sys.exit(1)

    img = cv2.imread(image_path)
    if img is None:
        print(f"Frame okunamadı: {image_path}")
        sys.exit(1)
    h, w = img.shape[:2]
    print(f"[2/3] Frame boyutu: {w}x{h}")
    print("[2/3] Pencerede 4 köşeyi sırayla tıkla:")
    print("       SOL ÜST → SAĞ ÜST → SAĞ ALT → SOL ALT")
    print("       (yanlış tıkladıysan 'r' tuşuna bas — sıfırlar)")
    print("       4 nokta tamamlanınca otomatik kapanır")

    points = []
    label_names = ['SOL ÜST', 'SAĞ ÜST', 'SAĞ ALT', 'SOL ALT']
    img_orig = img.copy()
    win = '4 köşe seç (sırasıyla)'

    def redraw():
        disp = img_orig.copy()
        for i, (x, y) in enumerate(points):
            cv2.circle(disp, (x, y), 8, (0, 0, 255), -1)
            cv2.putText(disp, f"{i+1} {label_names[i]}", (x+12, y+6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        if len(points) >= 2:
            for i in range(len(points)):
                a = points[i]
                b = points[(i+1) % len(points)] if len(points) == 4 else None
                if b:
                    cv2.line(disp, a, b, (255, 200, 0), 2)
        cv2.imshow(win, disp)

    def on_click(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN and len(points) < 4:
            points.append((x, y))
            redraw()

    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win, min(w, 1280), min(h, 720))
    cv2.setMouseCallback(win, on_click)
    redraw()

    while True:
        key = cv2.waitKey(20) & 0xFF
        if key == ord('r'):
            points.clear()
            redraw()
        if len(points) == 4:
            cv2.waitKey(800)  # son noktayı görmek için kısa bekleme
            break
        if key == 27:  # ESC
            print("İptal edildi.")
            sys.exit(0)
    cv2.destroyAllWindows()
    return points, (w, h)


def calc_matrix3d(src_w, src_h, dst_points):
    """4 nokta homografi → CSS matrix3d string."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        print("OpenCV/NumPy yüklü değil.")
        sys.exit(1)

    src = np.array([[0, 0], [src_w, 0], [src_w, src_h], [0, src_h]], dtype=np.float32)
    dst = np.array(dst_points, dtype=np.float32)
    M = cv2.getPerspectiveTransform(src, dst)

    # 3x3 perspective → 4x4 CSS matrix3d (column-major)
    css_matrix = [
        M[0, 0], M[1, 0], 0, M[2, 0],
        M[0, 1], M[1, 1], 0, M[2, 1],
        0,        0,        1, 0,
        M[0, 2], M[1, 2], 0, M[2, 2],
    ]
    return 'matrix3d(' + ', '.join(f'{v:.6f}' for v in css_matrix) + ')'


def update_iframe_transform(matrix_css):
    """sunum_linkedin.html içindeki #live-stream iframe'ine transform: matrix3d(...) ekler veya günceller."""
    if not os.path.exists(HTML_PATH):
        print(f"HTML bulunamadı: {HTML_PATH}")
        return False

    with open(HTML_PATH, 'r', encoding='utf-8') as f:
        html = f.read()

    # iframe'in inline style'ında transform varsa değiştir, yoksa ekle
    import re
    pattern = r'(id="live-stream"[^>]*style="[^"]*?)transform:[^;]+;?'
    new_transform = f'transform: translate(-50%,-50%) {matrix_css};'

    if re.search(pattern, html):
        new_html = re.sub(pattern, r'\1' + new_transform, html)
    else:
        # style="..." içine transform ekle
        new_html = html.replace(
            'id="live-stream"',
            f'id="live-stream" style="{new_transform}" data-orig-style="kept"',
            1,
        )

    if new_html == html:
        print("HTML güncellenemedi (pattern eşleşmedi). Manuel ekle:")
        print(new_transform)
        return False

    with open(HTML_PATH, 'w', encoding='utf-8') as f:
        f.write(new_html)
    print(f"[3/3] HTML güncellendi: {HTML_PATH}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--frame', help='Hazır frame yolu (capture atla)', default=None)
    ap.add_argument('--no-apply', action='store_true', help='HTML\'e uygulama, sadece matrix bas')
    ap.add_argument('--wait', type=int, default=10000, help='Yayın bağlantı bekleme süresi (ms)')
    args = ap.parse_args()

    frame_path = args.frame or capture_frame(wait_ms=args.wait)
    points, (w, h) = select_4_points(frame_path)
    print(f"[2/3] Seçilen noktalar: {points}")

    matrix = calc_matrix3d(w, h, points)
    print()
    print("=" * 60)
    print(f"[3/3] CSS:")
    print(f"  transform: translate(-50%,-50%) {matrix};")
    print("=" * 60)

    if not args.no_apply:
        update_iframe_transform(matrix)
    else:
        print("(--no-apply ile çağrıldı, HTML değiştirilmedi)")


if __name__ == '__main__':
    main()
