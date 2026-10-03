# 🏌️‍♂️ WhatsApp Golf Bot ("Anura Kumara")

A containerized Python-centric WhatsApp bot designed for golf groups to automate **tee time bookings**, **WhatsApp event creation**, **18Birdies scorecard synchronization & witty post-round roasts**, **weather alerts**, and **Sri Lankan caddy banter** powered by **Google Gemini AI**.

---

## 🌴 Key Features

1. **📸 Tee Time Screenshot to WhatsApp Event**:
   - Send or forward any golf booking screenshot from your club app (e.g. MiClub, GolfNow, ClubV1, BRS Golf).
   - Gemini Multimodal AI extracts course name, date, tee off time, and players.
   - Automatically publishes a native **WhatsApp Event** in the group with full RSVP details and Anura Kumara's confirmation commentary.

2. **📊 18Birdies Automated Sync & Scorecard Roast**:
   - Runs a periodic background process to pull your group's round data from [18birdies.com/download-account-data/](https://18birdies.com/download-account-data/).
   - Also detects JSON exports dropped into `./data/18birdies/`.
   - When a new round is posted, Anura Kumara analyzes fairways, GIR %, 3-putts, and blowup holes to deliver a witty Sri Lankan caddy round summary & roast to the group.

3. **🇱🇰 Anura Kumara Caddy Persona & Banter Chatbot**:
   - Responds to `@caddy`, `!caddy`, or direct questions.
   - Employs iconic AKD political rally rhetoric: *"Sahodaraya"*, *"Dan balanna sahodaraya..."*, *"Meka thama thathwe!"*, *"Mage laga files thiyanawa!"*, and the Compass (*Malimawa* 🧭).
   - Audits scorecards, catches 3-putts like corruption, demands System Change on slices, and gives fiery caddy advice.

4. **🌧️ Automated Rain & Weather Alerts**:
   - Automatically tracks your upcoming tee time and queries hourly precipitation from Open-Meteo.
   - If rain probability exceeds threshold (default: 40%) at 24 hours and 2 hours before tee off, alerts the group with advice (or instructions to head straight to the 19th hole clubhouse).

5. **🐳 Dockerized for Your Old PC / Home Server**:
   - Runs isolated in Docker with persistent session storage, so you scan the WhatsApp QR code once and it runs forever.

6. **🌐 Web Monitoring Dashboard (`http://localhost:8080`)**:
   - A lightweight web console to monitor live WhatsApp connectivity, message sent/received counts, 18Birdies sync health per friend, failure logs, and upcoming tee times in SQLite.

---

## 🚀 Quick Start Guide

### 1. Configure `.env`
Fill in your credentials in [.env](file:///Users/thilinafernando/Projects/golf-bot/.env):
```bash
# Google Gemini API (Get free key from https://aistudio.google.com/)
GEMINI_API_KEY=your_gemini_api_key_here

# 18Birdies Account Credentials (https://18birdies.com/download-account-data/)
BIRDIES_EMAIL=your_email@example.com
BIRDIES_PASSWORD=your_password

# Default Golf Course and Coordinates for Weather (Beaconhills Golf Club, Melbourne)
DEFAULT_COURSE_NAME="Beaconhills Golf Club"
DEFAULT_COURSE_LAT=-38.0845
DEFAULT_COURSE_LON=145.4385
TIMEZONE=Australia/Melbourne
```

### 2. Configure Players (Optional)
List your golf buddies in [`config/players.json`](file:///Users/thilinafernando/Projects/golf-bot/config/players.json) to track multiple 18Birdies accounts:
```json
[
  {
    "name": "Thilina",
    "birdies_email": "thilina@example.com",
    "birdies_password": "password",
    "handicap": 18.0
  },
  {
    "name": "Kasun",
    "birdies_email": "kasun@example.com",
    "birdies_password": "password",
    "handicap": 24.5
  }
]
```

### 3. Run with Docker Compose
Run on your PC or home server:
```bash
docker compose up --build -d
```

### 4. Scan WhatsApp QR Code (One-time Setup)
View the logs to scan the WhatsApp QR code with your dedicated bot phone number:
```bash
docker compose logs -f golf-bot
```
1. Open WhatsApp on the bot's phone.
2. Go to **Settings > Linked Devices > Link a Device**.
3. Scan the QR code shown in your terminal.
4. Add the bot's number into your golf WhatsApp group!

---

## 💬 Interacting with the Bot in WhatsApp

- **Upload Screenshot**: Send a screenshot of your booking app. Anura Kumara parses the time and course and creates a WhatsApp event.
- **Tee Time Query**: `@caddy when is the next tee time?` or `!teetime`
- **Weather Forecast**: `@caddy what is the weather like for our round?`
- **Manual Scorecard Sync**: `@caddy sync rounds`
- **General Golf Banter**: `@caddy roast Kasun about his putting` or `@caddy advice for hole 3`

---

## 🛠️ Testing Features Locally Without WhatsApp
You can test the features independently using the test utility:
```bash
# Test weather forecast & alert format
python scripts/test_features.py --weather

# Test Anura Kumara chat response
python scripts/test_features.py --caddy-chat "Who told Kasun to use driver on hole 2?"

# Test 18Birdies scorecard parsing & roast generation
python scripts/test_features.py --test-roast
```
