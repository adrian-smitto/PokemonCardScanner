import os
from dotenv import load_dotenv

load_dotenv()

# Camera
CAMERA_INDEX = 0
CAMERA_WIDTH = 1280
CAMERA_HEIGHT = 720
CAMERA_FPS = 30
CAMERA_NATIVE_ZOOM = 0       # Set to >0 to apply hardware zoom (0 = no change)

# Digital zoom
DIGITAL_ZOOM_DEFAULT = 1.0   # 1.0 = no zoom
DIGITAL_ZOOM_MIN = 1.0
DIGITAL_ZOOM_MAX = 4.0
DIGITAL_ZOOM_STEP = 0.25

# ROI brightness-based presence detection (used when ROI is set)
# Empty black box ≈ 5-15; card present ≈ 60-120. Threshold between these.
ROI_BRIGHTNESS_THRESHOLD = 40

# Card detection
CARD_AREA_MIN_FRACTION = 0.08
CARD_AREA_MAX_FRACTION = 0.70
CARD_ASPECT_RATIO = 0.714        # 63mm / 88mm
ASPECT_RATIO_TOLERANCE = 0.12

# Stabilisation
STABILIZE_FRAMES = 8
STABILIZE_HASH_THRESHOLD = 10    # max hamming distance between consecutive frames
LOST_FRAMES_THRESHOLD = 60       # consecutive frames without contour before card is considered gone (~2s at 30fps)

# Hash matching
MATCH_HAMMING_THRESHOLD = 72     # max hamming distance for a positive match

# Duplicate suppression
DUPLICATE_COOLDOWN_SECONDS = 5.0

# Audio
BEEP_FREQUENCY_SUCCESS = 1000    # Hz
BEEP_FREQUENCY_FAILURE = 600     # Hz
BEEP_DURATION_MS = 200

# Paths
DB_PATH = "db/cards.db"
SCAN_LOG_PATH = "db/scan_log.db"
CAPTURES_DIR = "db/captures"
IMAGES_DIR = "db/images"

# Manual remap
REMAP_TOP_N = 100   # default number of candidates shown in the remap dialog

# Screen snip
SNIP_HOTKEY = "<Control-Shift-S>"   # Tkinter key binding syntax

# Pokemon TCG API
POKEMONTCG_API_KEY = os.getenv("POKEMONTCG_API_KEY", "")
POKEMONTCG_BASE_URL = "https://api.pokemontcg.io/v2"
API_TIMEOUT_SECONDS = 20

# PriceCharting API (disabled by default — requires a paid API key)
PRICECHARTING_ENABLED = False
PRICECHARTING_API_KEY = os.getenv("PRICECHARTING_API_KEY", "")

# UI
UI_FEED_WIDTH = 640
UI_FEED_HEIGHT = 360
UI_TICK_MS = 33                  # ~30fps
