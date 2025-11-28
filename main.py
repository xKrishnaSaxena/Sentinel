import asyncio
import sqlite3
import os
import requests
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response 
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_TOKEN = os.getenv("TWILIO_TOKEN")
FROM_WHATSAPP = 'whatsapp:+14155238886' 

twilio_client = Client(TWILIO_SID, TWILIO_TOKEN)

def init_db():
    conn = sqlite3.connect('stocks.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS watchlist 
                 (phone_number TEXT, stock_symbol TEXT, last_seen_link TEXT)''')
    conn.commit()
    conn.close()

init_db()
@tool
def add_stock_to_watchlist(stock: str, phone_number: str):
    """Adds a stock ticker (e.g., AAPL, TSLA) to the watchlist."""
    conn = sqlite3.connect('stocks.db')
    c = conn.cursor()
    stock = stock.upper().strip()
    
    c.execute("SELECT * FROM watchlist WHERE phone_number=? AND stock_symbol=?", (phone_number, stock))
    if c.fetchone():
        conn.close()
        return f"{stock} is already in your watchlist."
    
    c.execute("INSERT INTO watchlist (phone_number, stock_symbol, last_seen_link) VALUES (?, ?, ?)", 
              (phone_number, stock, "none"))
    conn.commit()
    conn.close()
    return f"✅ Added {stock}. I'll check for news every 10 mins."

@tool
def remove_stock_from_watchlist(stock: str, phone_number: str):
    """Removes a stock from the watchlist."""
    conn = sqlite3.connect('stocks.db')
    c = conn.cursor()
    stock = stock.upper().strip()
    c.execute("DELETE FROM watchlist WHERE phone_number=? AND stock_symbol=?", (phone_number, stock))
    conn.commit()
    conn.close()
    return f"🗑️ Stopped tracking {stock}."

@tool
def view_watchlist(phone_number: str):
    """Shows all stocks being tracked."""
    conn = sqlite3.connect('stocks.db')
    c = conn.cursor()
    c.execute("SELECT stock_symbol FROM watchlist WHERE phone_number=?", (phone_number,))
    rows = c.fetchall()
    conn.close()
    if not rows: return "You are not tracking anything."
    return "👀 Tracking: " + ", ".join([r[0] for r in rows])

llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite", google_api_key=GOOGLE_API_KEY)
tools = [add_stock_to_watchlist, remove_stock_from_watchlist, view_watchlist]
agent_executor = create_react_agent(llm, tools)

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
    except:
        return "⚖️ NEUTRAL - Analysis unavailable"

async def scraper_loop():
    print("🚀 Scraper started (10 min interval)")
    while True:
        conn = sqlite3.connect('stocks.db')
        c = conn.cursor()
        c.execute("SELECT rowid, phone_number, stock_symbol, last_seen_link FROM watchlist")
        rows = c.fetchall()
        
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

                if items:
                    latest_item = items[0]
                    title = latest_item.title.text
                    link = latest_item.link.text

                    if link != last_link:
                        impact = await analyze_news_impact(title)
                        
                        msg = f"🔔 *{stock} News*\n\n{impact}\n\n🔗 {link}"
                        
                        print(f"✨ Sending alert for {stock} to {phone}")
                        
                        target_number = phone if phone.startswith('whatsapp:') else f"whatsapp:{phone}"
                        
                        twilio_client.messages.create(
                            body=msg, 
                            from_=FROM_WHATSAPP, 
                            to=target_number
                        )
                        
                        c.execute("UPDATE watchlist SET last_seen_link=? WHERE rowid=?", (link, row_id))
                        conn.commit()
            except Exception as e:
                print(f"⚠️ Error checking {stock}: {e}")

        conn.close()
        await asyncio.sleep(600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(scraper_loop())
    yield

app = FastAPI(lifespan=lifespan)

@app.post("/bot")
async def reply_whatsapp(request: Request):
    form_data = await request.form()
    
    incoming_msg = form_data.get('Body')
    sender = form_data.get('From') 

    print(f"📩 Message from {sender}: {incoming_msg}")

    prompt = f"User Phone: {sender}\nUser Request: {incoming_msg}"
    
    try:
        response_state = await agent_executor.ainvoke({"messages": [("user", prompt)]})
        ai_response = response_state["messages"][-1].content
    except Exception as e:
        ai_response = "Sorry, I'm having trouble processing that request right now."
        print(f"Agent Error: {e}")

    resp = MessagingResponse()
    resp.message(ai_response)
    
    return Response(content=str(resp), media_type="application/xml")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)