import cv2
import numpy as np
from mss import mss
from utils import load_config, save_config

def select_roi_on_image(img):
    clone = img.copy()
    roi = [None, None]  # (x0,y0), (x1,y1)
    selecting = False
    rect = None

    def on_mouse(event, x, y, flags, param):
        nonlocal roi, selecting, rect, clone
        if event == cv2.EVENT_LBUTTONDOWN:
            roi[0] = (x,y)
            roi[1] = (x,y)
            selecting = True
        elif event == cv2.EVENT_MOUSEMOVE and selecting:
            roi[1] = (x,y)
        elif event == cv2.EVENT_LBUTTONUP:
            roi[1] = (x,y)
            selecting = False

    cv2.namedWindow("Select ROI (drag). Press S=save, R=reset, Q=quit")
    cv2.setMouseCallback("Select ROI (drag). Press S=save, R=reset, Q=quit", on_mouse)

    while True:
        frame = clone.copy()
        if roi[0] and roi[1]:
            x0,y0 = roi[0]
            x1,y1 = roi[1]
            x0,x1 = sorted([x0,x1])
            y0,y1 = sorted([y0,y1])
            cv2.rectangle(frame, (x0,y0), (x1,y1), (0,255,0), 2)
        cv2.imshow("Select ROI (drag). Press S=save, R=reset, Q=quit", frame)
        key = cv2.waitKey(20) & 0xFF
        if key in (ord('q'), 27):
            cv2.destroyAllWindows()
            return None
        if key == ord('r'):
            roi = [None,None]
        if key == ord('s'):
            if roi[0] and roi[1]:
                x0,y0 = roi[0]
                x1,y1 = roi[1]
                x0,x1 = sorted([x0,x1])
                y0,y1 = sorted([y0,y1])
                cv2.destroyAllWindows()
                return (x0,y0,x1-x0,y1-y0)

def main():
    with mss() as sct:
        monitor = sct.monitors[0]  # virtual screen
        shot = sct.grab(monitor)
        img = np.array(shot)[:,:,:3]  # BGRA -> BGR

    roi = select_roi_on_image(img)
    if roi is None:
        print("[INFO] ROI selection cancelled.")
        return
    x,y,w,h = roi
    cfg = load_config()
    cfg["roi"] = {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}
    save_config(cfg)
    print(f"[OK] Saved ROI to config.yaml: {cfg['roi']}")

if __name__ == "__main__":
    main()
