"""Voice EOD — generates a 30-sec audio summary of the day via ElevenLabs (free tier 10k chars/mo).
Sends to Telegram as voice message."""
from __future__ import annotations
import os, json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests, pandas as pd
from dotenv import load_dotenv
load_dotenv()

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data"
IST = ZoneInfo("Asia/Kolkata")
TG = os.getenv("TELEGRAM_NEWS_BOT_TOKEN","")
CHAT = os.getenv("TELEGRAM_CHAT_ID","")
CLAUDE_KEY = os.getenv("ANTHROPIC_API_KEY","")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL","claude-sonnet-4-5-20250929")
ELEVENLABS_KEY = os.getenv("ELEVENLABS_API_KEY","")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # Rachel voice


def build_summary_text():
    """Get EOD context and ask Claude for a TIGHT 30-sec script (~80 words)."""
    if not CLAUDE_KEY: return None
    ctx = {"date": datetime.now(IST).strftime("%a %d %b")}
    try:
        gh = pd.read_csv(DATA / "groww_holdings.csv")
        ctx["pnl_pct"] = float(((gh["ltp"]-gh["avg_price"])*gh["qty"]).sum() / (gh["avg_price"]*gh["qty"]).sum() * 100)
        ctx["winners"] = gh.nlargest(2, "pnl_pct")[["symbol","pnl_pct"]].to_dict("records")
        ctx["losers"] = gh.nsmallest(2, "pnl_pct")[["symbol","pnl_pct"]].to_dict("records")
    except: pass
    try:
        rg = pd.read_csv(DATA / "market_regime.csv").iloc[-1].to_dict()
        ctx["regime"] = rg.get("regime","")
    except: pass
    system = """Write a SPOKEN 30-second EOD market note for an Indian retail trader. ~80 words. Conversational, calm, decisive. No emojis, no markdown — this gets converted to speech.
Structure: regime · top winner & loser today · 1 thing to watch tomorrow."""
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key":CLAUDE_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
            json={"model":CLAUDE_MODEL,"max_tokens":300,"system":system,
                  "messages":[{"role":"user","content":json.dumps(ctx, default=str)}]}, timeout=45)
        if r.ok:
            return "".join(b.get("text","") for b in r.json().get("content",[]) if b.get("type")=="text").strip()
    except Exception as e:
        print(f"[voice] claude err: {e}")
    return None


def synthesize(text: str) -> bytes | None:
    if not ELEVENLABS_KEY: return None
    try:
        r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
            headers={"xi-api-key": ELEVENLABS_KEY, "Content-Type": "application/json"},
            json={"text": text, "model_id": "eleven_turbo_v2_5",
                  "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}, timeout=60)
        if r.ok and r.content: return r.content
        print(f"[voice] EL fail {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[voice] EL err: {e}")
    return None


def send_voice(audio_bytes: bytes, caption: str = ""):
    if not TG or not CHAT or not audio_bytes: return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG}/sendVoice",
            files={"voice": ("eod.mp3", audio_bytes, "audio/mpeg")},
            data={"chat_id": CHAT, "caption": caption[:1024]}, timeout=30)
        return r.ok
    except Exception as e:
        print(f"[voice] tg err: {e}"); return False


def send_text(text):
    try:
        requests.post(f"https://api.telegram.org/bot{TG}/sendMessage",
                      json={"chat_id":CHAT,"text":text}, timeout=10)
    except: pass


def main():
    summary = build_summary_text()
    if not summary: print("[voice] no summary"); return
    audio = synthesize(summary)
    if audio:
        send_voice(audio, "🎙 EOD Voice Note")
        print(f"[voice] sent voice ({len(audio)} bytes)")
    else:
        send_text(f"🎙 EOD (text fallback — ElevenLabs key missing):\n\n{summary}")
        print("[voice] sent text fallback")


if __name__=="__main__": main()
