# ARK Log Watchdog (OCR → Discord)

A small Python tool that watches the on‑screen ARK log panel and sends a screenshot + snippet to a Discord channel via webhook when **keywords** (or regexes) are detected.

This avoids needing game file access by using OCR on the visible log area. You can also use **color filters** to match only certain log line colors.

## Features
- Calibrate a **Region of Interest (ROI)** once by drawing a rectangle over your ARK log panel.
- OCR using Tesseract with simple pre‑processing for better accuracy.
- Keyword and regex matching (case‑insensitive) with **deduping** + cooldown.
- Optional HSV **color filtering** (e.g., only lines in a given color range).
- Sends annotated screenshots + text to **Discord webhook**.
- Lightweight and local. No game hooks; just reads pixels.

> Tested on Windows; should work on macOS/Linux too. For best results, enable **high contrast** log text in ARK and keep the log pane stationary.

---

## 1) Install Prereqs

**Python 3.10+** recommended.

Install [Tesseract OCR]:  
- **Windows (Chocolatey):**
  ```powershell
  choco install tesseract
  ```
- **Windows (manual):** Install from UB Mannheim builds and note the install path, e.g. `C:\Program Files\Tesseract-OCR\tesseract.exe`.
- **macOS (Homebrew):**
  ```bash
  brew install tesseract
  ```
- **Ubuntu/Debian:**
  ```bash
  sudo apt-get update && sudo apt-get install -y tesseract-ocr
  ```

Then install Python deps:
```bash
pip install -r requirements.txt
```

If Tesseract isn’t on PATH, set its full path in `config.yaml` -> `tesseract_cmd`.

Copy `.env.example` to `.env` and put your Discord webhook URL:
```
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/....
```

---

## 2) Calibrate ROI

Open ARK so the log panel is visible, then run:
```bash
python calibrate_roi.py
```
- Your full desktop screenshot appears.
- Drag to draw a rectangle around the **log text area** (not the whole game).
- Press **S** to save. (Press **R** to reset if you want to reselect; **Q** to quit.)

This stores `roi: {x,y,w,h}` in `config.yaml`.

---

## 3) Configure Keywords, Colors, and Cooldowns

Edit `config.yaml`. Examples are inside:
- `keywords`: plain words (case‑insensitive)
- `regex`: advanced patterns
- `color_filters`: optional HSV ranges for “must‑match color”. Leave empty to ignore color.
- `cooldown_seconds`: dedupe window per identical line

You can also populate `keywords.txt` (one per line) if you prefer.

---

## 4) Run the Watcher

```bash
python watcher.py
```
It captures the ROI at intervals, OCRs text, looks for keywords, and posts matching lines with a screenshot to Discord.

**Hot tips**
- Keep the ARK log panel font large and high contrast.
- Reduce overlays and moving UIs near the ROI.
- Adjust `min_confidence` and `ocr_psm` if accuracy is off.

---

## Troubleshooting

- **Nothing detected**: Increase font size; reduce motion; lower `min_confidence` (e.g., 40); try different `preprocess.mode`.
- **Duplicates**: Increase `cooldown_seconds`; turn on `event_hash_on_text_only` to ignore the image.
- **Wrong text**: Tweak `preprocess` (threshold vs. adaptive); set `ocr_psm: 6` or `7`.
- **Webhook not posting**: Verify `.env` is loaded and the URL is correct; Discord may rate-limit—messages will print errors in console.

---

## Notes on Color Matching
If your ARK log uses distinct colors for events, you can set a `color_filters` entry with HSV ranges. The watcher samples the median color of each OCR line area and checks if it falls within any HSV range you define (see `config.yaml` for examples). If `color_filters` is empty, color is ignored.

---

## Optional: File Log Mode
If you later discover a log file on disk, we can add a file‑watcher mode (more reliable than OCR). For now, this project is OCR‑only as requested.

---

## License
MIT
