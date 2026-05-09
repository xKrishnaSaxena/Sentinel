import asyncio
import sqlite3
import os
import uuid
import requests
import uvicorn
import yfinance as yf
import mplfinance as mpf
import matplotlib
matplotlib.use("Agg")
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from twilio.twiml.messaging_response import MessagingResponse
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import tool
from langchain.agents import create_agent

load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_TOKEN = os.getenv("TWILIO_TOKEN")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
FROM_WHATSAPP = 'whatsapp:+14155238886'

CHARTS_DIR = Path("charts")
CHARTS_DIR.mkdir(exist_ok=True)

twilio_client = Client(TWILIO_SID, TWILIO_TOKEN)


def init_db():
    conn = sqlite3.connect('stocks.db', timeout=30)
    c = conn.cursor()
    try:
        c.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError as e:
        print(f"⚠️ Could not enable WAL mode: {e}")
    c.execute('''CREATE TABLE IF NOT EXISTS watchlist
                 (phone_number TEXT, stock_symbol TEXT, last_seen_link TEXT,
                  last_title TEXT, last_impact TEXT)''')
    for col in ("last_title", "last_impact"):
        try:
            c.execute(f"ALTER TABLE watchlist ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass
    c.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_phone_stock ON watchlist(phone_number, stock_symbol)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_phone ON watchlist(phone_number)")
    conn.commit()
    conn.close()


init_db()


def is_valid_ticker(stock: str) -> bool:
    """Quick ticker sanity check via yfinance fast_info — avoids polluting the watchlist with junk."""
    try:
        price = getattr(yf.Ticker(stock).fast_info, "last_price", None)
        return price is not None
    except Exception:
        return False


def safe_send_whatsapp(to_number: str, body: str, media_url: list[str] | None = None) -> bool:
    """Wrapper around Twilio send. Catches the 24-hour session window error (63016) and other API failures."""
    target = to_number if to_number.startswith('whatsapp:') else f"whatsapp:{to_number}"
    try:
        kwargs = {"body": body, "from_": FROM_WHATSAPP, "to": target}
        if media_url:
            kwargs["media_url"] = media_url
        twilio_client.messages.create(**kwargs)
        return True
    except TwilioRestException as e:
        if getattr(e, "code", None) == 63016:
            print(f"⏰ Twilio session expired for {target} (24h window). Skipping.")
        else:
            print(f"⚠️ Twilio error sending to {target}: {e}")
        return False
    except Exception as e:
        print(f"⚠️ Unexpected send error for {target}: {e}")
        return False


def generate_candlestick_chart(stock: str) -> str | None:
    """Builds a 30-day candlestick PNG and returns its public URL, or None on failure."""
    try:
        df = yf.Ticker(stock).history(period="1mo", interval="1d")
        if df is None or df.empty:
            return None
        filename = f"{stock}_{uuid.uuid4().hex[:8]}.png"
        filepath = CHARTS_DIR / filename
        mpf.plot(
            df,
            type='candle',
            style='yahoo',
            title=f"\n{stock} — Last 30 Days",
            ylabel='Price (USD)',
            volume=True,
            mav=(5, 20),
            figratio=(10, 6),
            figscale=1.1,
            savefig=dict(fname=str(filepath), dpi=120, bbox_inches='tight'),
        )
        return f"{PUBLIC_URL}/charts/{filename}"
    except Exception as e:
        print(f"⚠️ Chart generation failed for {stock}: {e}")
        return None


@tool
def add_stock_to_watchlist(stock: str, phone_number: str):
    """Adds a stock ticker (e.g., AAPL, TSLA) to the watchlist."""
    stock = stock.upper().strip()
    if not is_valid_ticker(stock):
        return f"❌ *{stock}* doesn't look like a valid ticker. Try a real symbol like AAPL, TSLA, or MSFT."

    conn = sqlite3.connect('stocks.db')
    c = conn.cursor()
    c.execute("SELECT 1 FROM watchlist WHERE phone_number=? AND stock_symbol=?", (phone_number, stock))
    if c.fetchone():
        conn.close()
        return f"ℹ️ *{stock}* is already in your watchlist."

    c.execute("INSERT INTO watchlist (phone_number, stock_symbol, last_seen_link) VALUES (?, ?, ?)",
              (phone_number, stock, "none"))
    conn.commit()
    conn.close()
    return f"✅ Added *{stock}*. I'll check for news every 10 minutes."


@tool
def remove_stock_from_watchlist(stock: str, phone_number: str):
    """Removes a stock from the watchlist."""
    conn = sqlite3.connect('stocks.db')
    c = conn.cursor()
    stock = stock.upper().strip()
    c.execute("DELETE FROM watchlist WHERE phone_number=? AND stock_symbol=?", (phone_number, stock))
    removed = c.rowcount
    conn.commit()
    conn.close()
    if not removed:
        return f"ℹ️ *{stock}* wasn't in your watchlist."
    return f"🗑️ Stopped tracking *{stock}*."


@tool
def get_last_news(stock: str, phone_number: str):
    """Returns the most recent news headline and sentiment report previously sent to the user for a given stock ticker."""
    conn = sqlite3.connect('stocks.db')
    c = conn.cursor()
    stock = stock.upper().strip()
    c.execute("SELECT last_title, last_impact, last_seen_link FROM watchlist WHERE phone_number=? AND stock_symbol=?",
              (phone_number, stock))
    row = c.fetchone()
    conn.close()
    if not row:
        return f"ℹ️ *{stock}* is not in your watchlist."
    title, impact, link = row
    if not title:
        return f"📭 No news has been captured yet for *{stock}*."
    return (
        f"🗞️ *Last {stock} News*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{impact}\n\n"
        f"📰 _{title}_\n"
        f"🔗 {link}"
    )


@tool
def view_watchlist(phone_number: str):
    """Shows all stocks being tracked."""
    conn = sqlite3.connect('stocks.db')
    c = conn.cursor()
    c.execute("SELECT stock_symbol FROM watchlist WHERE phone_number=? ORDER BY stock_symbol", (phone_number,))
    rows = c.fetchall()
    conn.close()
    if not rows:
        return "📭 You aren't tracking any stocks yet. Add one with: *track AAPL*"
    listing = "\n".join(f"  • *{r[0]}*" for r in rows)
    return f"👀 *Your Watchlist* ({len(rows)})\n━━━━━━━━━━━━━━━\n{listing}"


@tool
def get_stock_chart(stock: str, phone_number: str):
    """Generates and sends a 30-day candlestick chart with volume for a stock ticker via WhatsApp."""
    stock = stock.upper().strip()

    if not is_valid_ticker(stock):
        return f"❌ *{stock}* doesn't look like a valid ticker."

    if not PUBLIC_URL:
        return "⚠️ Chart sharing isn't configured (set PUBLIC_URL to your ngrok URL in .env)."

    chart_url = generate_candlestick_chart(stock)
    if not chart_url:
        return f"⚠️ Couldn't fetch price data for *{stock}*."

    caption = f"📊 *{stock}* — 30-day candlestick (5 & 20-day MAs)"
    if safe_send_whatsapp(phone_number, caption, media_url=[chart_url]):
        return f"📈 Chart for *{stock}* on its way!"
    return f"⚠️ Couldn't deliver the chart for *{stock}* — Twilio rejected the message."


llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key=GOOGLE_API_KEY)
tools = [add_stock_to_watchlist, remove_stock_from_watchlist, view_watchlist, get_last_news, get_stock_chart]
system_message = """You are a stock tracking assistant.
When a user asks to add, remove, or view stocks, you MUST use the provided tools.
If the user asks about the last/latest/previous news or report for a specific stock
(e.g. "what was the last news on AAPL?"), call the get_last_news tool with that ticker.
If the user asks for a chart, candlestick, graph, plot, or visual for a stock
(e.g. "show me a chart for TSLA", "AAPL graph"), call the get_stock_chart tool.
The 'phone_number' argument for tools is provided in the user's prompt as 'User Phone'.
Always extract and use that exact string for the phone_number parameter."""

agent_executor = create_agent(llm, tools, system_prompt=system_message)


async def analyze_news_impact(title):
    prompt = f"""
    You are a financial news summarizer.
    Analyze this headline: '{title}'

    Output format must be exactly like this:
    [EMOJI] [SENTIMENT] - [3-5 WORD SUMMARY]

    Example 1: 📈 BULLISH - Stock hits all-time high
    Example 2: 📉 BEARISH - CEO steps down amid scandal
    Example 3: ⚖️ NEUTRAL - Market awaits earnings report

    Do not add any other text.
    """
    try:
        response = await llm.ainvoke(prompt)
        return response.content.strip()
    except Exception:
        return "⚖️ NEUTRAL - Analysis unavailable"


async def scraper_loop():
    print("🚀 Scraper started (10 min interval)")
    while True:
        try:
            conn = sqlite3.connect('stocks.db', timeout=30)
            c = conn.cursor()
            c.execute("SELECT rowid, phone_number, stock_symbol, last_seen_link FROM watchlist")
            rows = c.fetchall()
            conn.close()
        except Exception as e:
            print(f"⚠️ Could not read watchlist: {e}")
            rows = []

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

        for row in rows:
            row_id, phone, stock, last_link = row
            rss_url = f"https://news.google.com/rss/search?q={stock}+stock&hl=en-US&gl=US&ceid=US:en"

            try:
                resp = requests.get(rss_url, headers=headers, timeout=10)
                soup = BeautifulSoup(resp.content, features='xml')
                items = soup.find_all('item')

                if not items:
                    continue

                latest_item = items[0]
                title = latest_item.title.text
                link = latest_item.link.text

                if link == last_link:
                    continue

                impact = await analyze_news_impact(title)
                msg = (
                    f"🚨 *{stock} ALERT* 🚨\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"{impact}\n\n"
                    f"📰 _{title}_\n"
                    f"🔗 {link}"
                )

                print(f"✨ Sending alert for {stock} to {phone}")
                if not safe_send_whatsapp(phone, msg):
                    continue

                try:
                    update_conn = sqlite3.connect('stocks.db', timeout=30)
                    update_conn.execute(
                        "UPDATE watchlist SET last_seen_link=?, last_title=?, last_impact=? WHERE rowid=?",
                        (link, title, impact, row_id)
                    )
                    update_conn.commit()
                    update_conn.close()
                except Exception as e:
                    print(f"⚠️ Could not persist update for {stock}: {e}")

            except requests.RequestException as e:
                print(f"⚠️ Network error checking {stock}: {e}")
            except Exception as e:
                print(f"⚠️ Error checking {stock}: {e}")

        await asyncio.sleep(600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(scraper_loop())
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/charts", StaticFiles(directory=str(CHARTS_DIR)), name="charts")


@app.post("/bot")
async def reply_whatsapp(request: Request):
    form_data = await request.form()

    incoming_msg = form_data.get('Body')
    sender = form_data.get('From')

    print(f"📩 Message from {sender}: {incoming_msg}")

    prompt = f"User Phone: {sender}\nUser Request: {incoming_msg}"

    try:
        response_state = await agent_executor.ainvoke({"messages": [("user", prompt)]})
        last_message = response_state["messages"][-1]
        if isinstance(last_message.content, list):
            ai_response = "".join([part.get("text", "") if isinstance(part, dict) else str(part) for part in last_message.content])
        else:
            ai_response = str(last_message.content)

    except Exception as e:
        ai_response = "⚠️ Sorry, I'm having trouble processing that request right now."
        print(f"Agent Error: {e}")

    resp = MessagingResponse()
    resp.message(ai_response)

    return Response(content=str(resp), media_type="application/xml")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
