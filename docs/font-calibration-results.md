# Font Calibration Results — Supernote A5X

**Device:** Supernote A5X (1404x1872 @ 226 DPI)  
**Date:** (pending on-device review)  
**Status:** Awaiting calibration pass

## Test Matrix

| Agent | Font | Tested Sizes (px) | Recommended Body | Recommended Date/Sig |
|---|---|---|---|---|
| Sam | Bradley Hand | 32, 40, 48, 56, 64 | _TBD_ | _TBD_ |
| Caroline | Noteworthy | 32, 40, 48, 56, 64 | _TBD_ | _TBD_ |
| Ingrid | Chalkduster | 32, 40, 48, 56, 64 | _TBD_ | _TBD_ |

## How to Run

```bash
# Generate preview PNGs (no .note files)
python scripts/calibrate_fonts.py --dry-run

# Generate PNGs + RATTA_RLE encoded pages
python scripts/calibrate_fonts.py
```

## Notes

- Date/signature sizes are scaled at 55% of body size
- View at 100% zoom or on the actual device for accurate assessment
- After selecting sizes, update `BODY_FONT_SIZE`, `DATE_FONT_SIZE`, and `SIGNATURE_FONT_SIZE` in `src/paia_supernote/writer.py`
