# 📈 Sentinel — WhatsApp AI Stock News & Charts Bot

A real-time, AI-powered stock tracking agent that lives in your WhatsApp.

Sentinel uses **FastAPI** to handle webhooks, **LangChain agents** on top of **Google Gemini** for natural-language understanding, **Google News RSS** for headline discovery, and **yfinance + mplfinance** to render on-demand candlestick charts. Users manage a watchlist via natural conversation and receive automated, sentiment-analyzed alerts whenever significant news drops.

---

## ✨ Features

- **🤖 Conversational AI Agent** — built with LangChain + Gemini 2.5 Flash. Talk naturally; no rigid command syntax (e.g., *"please start tracking Tesla for me"*, *"show me an Apple chart"*).
- **📰 Sentiment-Tagged News Alerts** — every fresh headline is classified by Gemini as **Bullish 📈**, **Bearish 📉**, or **Neutral ⚖️** with a 3–5 word summary, then pushed to your WhatsApp.
- **📊 On-Demand Candlestick Charts** — request a chart for any ticker and the bot replies with a 30-day OHLC candlestick (with 5 & 20-day moving averages and volume) delivered as a Twilio media attachment.
- **🗞️ Last-News Recall** — ask *"what was the last news on AAPL?"* and the bot replays the most recent stored alert, even if you missed it the first time.
- **🛡️ Robust Edge-Case Handling** — invalid tickers are rejected up front via yfinance validation; Twilio failures (including the 24-hour session-window error 63016) are caught and logged without crashing the scraper.
- **🔄 Async Background Scraper** — checks every tracked stock's RSS feed every 10 minutes in a non-blocking asyncio task, alongside the FastAPI server.
- **📱 WhatsApp Integration** — both inbound commands and outbound alerts flow through Twilio, with formatted messages using `*bold*` / `_italic_` and emoji-driven layouts.
- **💾 Indexed SQLite Storage** — watchlists are persisted per phone number with composite indexes for fast lookups.

---

## 📸 Screenshots

![Screenshot 1](https://github.com/user-attachments/assets/ddb86d24-ed75-4916-b8eb-b2ac24573122)
![Screenshot 2](https://github.com/user-attachments/assets/1536a52d-0f45-4d79-bbf3-eb7660a6ab03)
![Screenshot 3](https://github.com/user-attachments/assets/187b6b4e-8731-4fde-915f-abc87f426e79)

---

## 🛠️ Architecture

Sentinel runs two cooperating loops inside a single FastAPI process:

```
                          ┌──────────────────────┐
   WhatsApp user ──msg──▶ │  Twilio Sandbox      │
                          └─────────┬────────────┘
                                    │ webhook (POST /bot)
                                    ▼
                          ┌──────────────────────┐
                          │  FastAPI App         │
                          │                      │
                          │  ┌────────────────┐  │
                          │  │ LangChain      │  │── tools ──┐
                          │  │ Agent (Gemini) │  │           │
                          │  └────────────────┘  │           ▼
                          │                      │   add / remove / view /
                          │  ┌────────────────┐  │   last_news / chart
                          │  │ Scraper Loop   │  │           │
                          │  │ (every 10 min) │  │           ▼
                          │  └────────────────┘  │      ┌─────────┐
                          │                      │      │ SQLite  │
                          │  /charts (static)    │      │watchlist│
                          └──────────┬───────────┘      └─────────┘
                                     │
                          chart PNGs │ alerts (text + media)
                                     ▼
                          ┌──────────────────────┐
                          │  Twilio API          │ ──▶ WhatsApp
                          └──────────────────────┘
```

### The two loops

1. **Interaction loop** — A Twilio webhook hits `POST /bot`. The handler builds a prompt containing the sender's phone number and message, invokes the LangChain agent, lets Gemini decide which tool to call (`add_stock_to_watchlist`, `remove_stock_from_watchlist`, `view_watchlist`, `get_last_news`, or `get_stock_chart`), and returns the tool's output as a TwiML reply.
2. **Scraper loop** — Started in FastAPI's `lifespan` context. Every 10 minutes it reads the entire watchlist, fetches each stock's Google News RSS feed, and — if the latest headline link differs from `last_seen_link` — runs Gemini sentiment analysis, pushes a formatted alert via Twilio, and persists the new headline.

### Chart delivery flow

```
user: "chart for AAPL"
  └▶ agent calls get_stock_chart(stock="AAPL", phone_number=…)
       ├▶ is_valid_ticker("AAPL")  ── yfinance fast_info ──▶ True
       ├▶ generate_candlestick_chart  ── yfinance.history → mplfinance.plot ──▶ charts/AAPL_<uuid>.png
       ├▶ safe_send_whatsapp(media_url=[PUBLIC_URL + "/charts/<file>"])
       │     └▶ Twilio fetches the PNG from the FastAPI /charts static mount
       └▶ returns "📈 Chart for *AAPL* on its way!"  (sent as TwiML reply)
```

---

## 🚀 Prerequisites

- Python 3.10+ (the code uses PEP 604 union type hints)
- A **Twilio account** with a WhatsApp Sandbox (or a production WhatsApp Sender)
- A **Google AI Studio API key** with access to `gemini-2.5-flash`
- **ngrok** (or any public HTTPS tunnel) — required so Twilio can reach both the `/bot` webhook *and* fetch chart images from `/charts/...`

---

## 📦 Installation

### 1. Clone the repository

```bash
git clone https://github.com/yourusername/Sentinel.git
cd Sentinel
```

### 2. Create a virtual environment

```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS / Linux:
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

`requirements.txt` includes:

| Group | Packages |
|---|---|
| Web server | `fastapi`, `uvicorn`, `python-multipart` |
| AI agent | `langchain`, `langchain-google-genai`, `google-generativeai`, `langgraph`, `langchain-community` |
| News scraping | `requests`, `beautifulsoup4`, `lxml` |
| Charts | `yfinance`, `mplfinance`, `pandas` |
| Messaging | `twilio` |
| Misc | `python-dotenv`, `nltk` |

### 4. Configure environment

Create a `.env` file in the project root:

```env
GOOGLE_API_KEY=your_google_gemini_api_key
TWILIO_SID=your_twilio_account_sid
TWILIO_TOKEN=your_twilio_auth_token
PUBLIC_URL=https://your-ngrok-domain.ngrok-free.app
```

> **`PUBLIC_URL` is required for charts.** It must be the public HTTPS base URL that Twilio can reach (no trailing slash, no `/bot` suffix). Twilio fetches the chart PNG from `<PUBLIC_URL>/charts/<file>.png`, so an unreachable URL means charts silently fail to deliver.

---

## 🏃 Running the bot

You'll typically need three things running:

### Terminal 1 — start the server

```bash
python main.py
```

You should see:

```
🚀 Scraper started (10 min interval)
INFO:     Uvicorn running on http://0.0.0.0:8000
```

The scraper loop launches automatically via FastAPI's `lifespan` context.

### Terminal 2 — expose port 8000

```bash
ngrok http 8000
```

Copy the `https://...ngrok-free.app` URL it prints, and make sure it matches `PUBLIC_URL` in `.env`. (If the ngrok URL changes, update `.env` and restart the server.)

### Twilio Console — point the sandbox at ngrok

In **Messaging → Try it → Send a WhatsApp message → Sandbox settings**, set:

- **When a message comes in:** `https://<your-ngrok-domain>/bot` — method `POST`
- Save.

Then join your sandbox by sending the join code (e.g. `join <two-words>`) from your WhatsApp to the sandbox number.

---

## 💬 How to interact

Talk naturally — the agent figures out which tool to call.

| Intent | Example messages | Backing tool |
|---|---|---|
| Add a ticker | *"start tracking Apple for me"*, *"add TSLA"*, *"watch MSFT"* | `add_stock_to_watchlist` |
| Remove a ticker | *"stop watching Apple"*, *"remove TSLA"*, *"untrack MSFT"* | `remove_stock_from_watchlist` |
| View watchlist | *"what am I tracking?"*, *"show my watchlist"*, *"list my stocks"* | `view_watchlist` |
| Recall last alert | *"what was the last news on AAPL?"*, *"latest TSLA report"* | `get_last_news` |
| Request a chart | *"show me a chart for AAPL"*, *"TSLA candlestick"*, *"plot NVDA"* | `get_stock_chart` |

### Invalid ticker behaviour

```
You: track XYZNONSENSE
Bot: ❌ *XYZNONSENSE* doesn't look like a valid ticker.
     Try a real symbol like AAPL, TSLA, or MSFT.
```

The check uses `yfinance.Ticker(...).fast_info.last_price`, so anything Yahoo Finance doesn't recognize is rejected before it pollutes the watchlist.

---

## 🔔 Message formats

### Background news alert (auto-pushed by the scraper)

```
🚨 *TSLA ALERT* 🚨
━━━━━━━━━━━━━━━
📈 BULLISH - Stock hits all-time high

📰 _Tesla Q4 deliveries beat expectations_
🔗 https://news.google.com/rss/articles/...
```

### Last-news recall (`get_last_news`)

```
🗞️ *Last AAPL News*
━━━━━━━━━━━━━━━
⚖️ NEUTRAL - Market awaits earnings report

📰 _Apple to report earnings next Thursday_
🔗 https://news.google.com/...
```

### Watchlist (`view_watchlist`)

```
👀 *Your Watchlist* (3)
━━━━━━━━━━━━━━━
  • *AAPL*
  • *MSFT*
  • *TSLA*
```

### Candlestick chart (`get_stock_chart`)

You receive **two** WhatsApp messages:

1. A media message with the PNG candlestick attached and caption `📊 *AAPL* — 30-day candlestick (5 & 20-day MAs)`
2. A short TwiML reply: `📈 Chart for *AAPL* on its way!`

The chart shows daily OHLC candles, 5-day & 20-day moving averages, and a volume sub-panel. Files are saved under `charts/<TICKER>_<uuid>.png` and served via FastAPI's static mount.

---

## 🧰 Tool reference

All tools are defined as `@tool` decorated functions in [main.py](main.py) and registered with the LangChain agent. The agent extracts the WhatsApp sender's phone number from the user prompt and passes it as `phone_number` to whichever tool it picks.

| Tool | Args | Behaviour |
|---|---|---|
| `add_stock_to_watchlist` | `stock`, `phone_number` | Validates ticker → checks for duplicate → inserts row. |
| `remove_stock_from_watchlist` | `stock`, `phone_number` | Deletes row; replies with whether anything was actually removed. |
| `view_watchlist` | `phone_number` | Returns sorted, formatted list of tracked tickers. |
| `get_last_news` | `stock`, `phone_number` | Returns the most recent stored headline + sentiment + link for that ticker. |
| `get_stock_chart` | `stock`, `phone_number` | Validates ticker → renders 30-day candlestick → pushes via Twilio media. |

---

## 🗄️ Database schema

SQLite, single file at `stocks.db`:

```sql
CREATE TABLE watchlist (
    phone_number    TEXT,    -- e.g. "whatsapp:+15551234567"
    stock_symbol    TEXT,    -- e.g. "AAPL"
    last_seen_link  TEXT,    -- last news URL we alerted on
    last_title      TEXT,    -- last headline (for get_last_news)
    last_impact     TEXT     -- last sentiment line (for get_last_news)
);

CREATE INDEX idx_watchlist_phone_stock ON watchlist(phone_number, stock_symbol);
CREATE INDEX idx_watchlist_phone       ON watchlist(phone_number);
```

WAL mode is enabled at startup so the scraper loop can write while the webhook handler reads. Schema migrations are handled idempotently via `CREATE IF NOT EXISTS` and best-effort `ALTER TABLE ADD COLUMN` calls in `init_db()`.

---

## 📂 Project structure

```
Sentinel/
├── main.py              # FastAPI app + agent + scraper + tools
├── requirements.txt     # Python dependencies
├── stocks.db            # SQLite database (auto-generated, git-ignored)
├── charts/              # Generated PNG candlesticks (auto-created, git-ignored)
├── .env                 # Secrets + PUBLIC_URL (git-ignored)
├── .gitignore
├── ngrok.exe            # (optional) Windows ngrok binary
└── README.md
```

---

## 🧪 Manual test plan

After joining the Twilio sandbox, send these messages in order to exercise every feature:

| # | Message | Verifies |
|---|---|---|
| 1 | `view watchlist` | Empty-state copy |
| 2 | `track NOTAREALSTOCK` | Invalid ticker rejection |
| 3 | `track AAPL` | Add + ticker validation |
| 4 | `track TSLA` | Second add |
| 5 | `add AAPL` | Duplicate prevention |
| 6 | `show my watchlist` | Populated list rendering |
| 7 | `chart for AAPL` | Candlestick generation + Twilio media delivery |
| 8 | `chart for FAKETICK` | Chart-path invalid ticker rejection |
| 9 | `last news on AAPL` | "no news yet" branch |
| 10 | *(wait ≤ 10 min)* | Background scraper alert |
| 11 | `last news on AAPL` | Stored alert recall |
| 12 | `stop tracking TSLA` | Removal |
| 13 | `stop tracking ZZZZ` | Remove-non-existent branch |

### Force the scraper to fire immediately (for impatient testing)

Reset the `last_seen_link` column so the next poll treats every headline as new:

```bash
python -c "import sqlite3; c=sqlite3.connect('stocks.db'); c.execute(\"UPDATE watchlist SET last_seen_link='none'\"); c.commit()"
```

Or temporarily change `await asyncio.sleep(600)` near the bottom of `scraper_loop()` to a smaller value and restart.

---

## ⚠️ Limitations & notes

- **Twilio sandbox 24-hour window** — the sandbox can only send free-form messages within 24 hours of the user's last inbound message. After that, sends fail with error code **63016**, which Sentinel catches and logs as `⏰ Twilio session expired for ... (24h window). Skipping.` For production, register an approved WhatsApp Business sender + templates.
- **Google News RSS fragility** — the scraper depends on the RSS feed format. If Google changes the XML structure, the BeautifulSoup parsing will need updating.
- **`PUBLIC_URL` must point to the running tunnel** — if ngrok restarts and the URL changes, charts will fail to deliver until you update `.env` and restart the server. (Twilio fetches the media URL synchronously when sending.)
- **Chart files are not auto-cleaned** — PNGs accumulate under `charts/`. Add a periodic cleanup task if disk usage matters.
- **yfinance is unofficial** — it scrapes Yahoo Finance and may break or rate-limit. The `is_valid_ticker` and chart paths both gracefully degrade with a friendly message if yfinance fails.
- **Model access** — `gemini-2.5-flash` must be available to your Google API key. To switch models, edit the `ChatGoogleGenerativeAI(model=...)` line in `main.py`.

---

## 🧠 Implementation highlights

A few non-obvious design choices worth flagging:

- **Charts are pushed, not embedded in TwiML.** The `get_stock_chart` tool calls `twilio_client.messages.create(media_url=[...])` directly, then returns a short text confirmation that the agent surfaces as the TwiML reply. This sends two messages but keeps the tool boundary clean — agents return strings, not TwiML.
- **`safe_send_whatsapp()` wraps every outbound send.** It distinguishes the Twilio 24-hour session-window error (63016) from generic API failures, so a single expired session can't take down the whole scraper loop.
- **`fast_info.last_price` (attribute access) is used for ticker validation**, not `.get("last_price")` — the latter exists on `FastInfo` but always returns `None` in current yfinance.
- **Indexes are created lazily** with `CREATE INDEX IF NOT EXISTS`, so existing `stocks.db` files from earlier versions of the bot pick up the optimisation on next start without a migration step.
