import os
import re
import json
import base64
import requests
from typing import Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Kaala-Vaani Voice Fraud Interceptor")

# -------------------------------------------------------------------
# 1. Configuration & API Key Embedded
# -------------------------------------------------------------------
GNANI_API_KEY = "a74c7732"  # Embedded directly as requested

# Core endpoints from Gnani Speech APIs
GNANI_STT_REST_URL = "https://api.vachana.ai/stt/v3"
GNANI_TTS_REST_URL = "https://api.vachana.ai/api/v1/tts/inference"

# Risk Detection Keywords & Regex Patterns (Multilingual: EN, HI, KN)
FRAUD_PATTERNS = [
    r"\b(digital arrest|police department|cbi|trai|cyber crime|warrant|customs)\b",
    r"\b(transfer money|pay fine|bank block|otp|cvv|account blocked|send funds)\b",
    r"\b(डिजिटल अरेस्ट|सीबीआई|पुलिस|खाता बंद|ओटीपी|पैसे भेजो|फाइन भरो)\b",
    r"\b(ಡಿಜಿಟಲ್ ಬಂಧನ|ಪೋಲಿಸ್|ಹಣ ಕಳುಹಿಸಿ|ಒಟಿಪಿ|ಖಾತೆ ಲಾಕ್)\b"
]

# Localized Warning Audio Prompts
WARNING_TEXTS = {
    "hi-IN": "सावधान! यह कॉल एक संदिग्ध धोखाधड़ी हो सकती है। कृपया पैसे या OTP साझा न करें।",
    "kn-IN": "ಎಚ್ಚರಿಕೆ! ಈ ಕರೆ ವಂಚನೆಯಾಗಿರಬಹುದು. ದಯವಿಟ್ಟು ಹಣ ಅಥವಾ ಒಟಿಪಿಯನ್ನು ಹಂಚಿಕೊಳ್ಳಬೇಡಿ.",
    "en-IN": "Warning! This call shows signs of high-risk fraud. Do not transfer funds or share your OTP."
}

# -------------------------------------------------------------------
# 2. Gnani AI API Helper Functions
# -------------------------------------------------------------------
def transcribe_audio_gnani(audio_bytes: bytes, language_code: str = "indic-auto") -> str:
    """Sends raw audio bytes to Gnani Prisma STT API for speech-to-text transcription."""
    headers = {
        "Authorization": f"Bearer {GNANI_API_KEY}"
    }
    files = {
        "file": ("audio.wav", audio_bytes, "audio/wav")
    }
    data = {
        "model": "prisma-v3.0",
        "language_code": language_code
    }
    
    try:
        response = requests.post(GNANI_STT_REST_URL, headers=headers, files=files, data=data, timeout=5)
        if response.status_code == 200:
            res_json = response.json()
            return res_json.get("transcript", "")
    except Exception as e:
        print(f"[STT Error]: {e}")
    return ""


def synthesize_voice_warning_gnani(text: str, language_code: str = "hi-IN") -> Optional[bytes]:
    """Synthesizes localized warning audio via Gnani Timbre TTS API."""
    headers = {
        "Authorization": f"Bearer {GNANI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "text": text,
        "voice": "Nalini",
        "model": "timbre-v2.5",
        "language": language_code,
        "audio_config": {
            "sample_rate": 16000,
            "encoding": "linear_pcm",
            "container": "wav"
        }
    }
    
    try:
        response = requests.post(GNANI_TTS_REST_URL, headers=headers, json=payload, timeout=5)
        if response.status_code == 200:
            return response.content
    except Exception as e:
        print(f"[TTS Error]: {e}")
    return None


def evaluate_threat_score(text: str) -> bool:
    """Evaluates incoming transcript text against financial fraud & coercion patterns."""
    for pattern in FRAUD_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False

# -------------------------------------------------------------------
# 3. HTTP REST Endpoints
# -------------------------------------------------------------------
class ThreatCheckRequest(BaseModel):
    text: str
    language: str = "hi-IN"

@app.post("/api/check-threat")
async def check_threat(req: ThreatCheckRequest):
    """Evaluates threat score and generates TTS voice warning if fraud is detected."""
    is_fraud = evaluate_threat_score(req.text)
    warning_text = WARNING_TEXTS.get(req.language, WARNING_TEXTS["en-IN"])
    audio_b64 = None
    
    if is_fraud:
        raw_audio = synthesize_voice_warning_gnani(warning_text, req.language)
        if raw_audio:
            audio_b64 = base64.b64encode(raw_audio).decode("utf-8")
            
    return JSONResponse({
        "is_fraud": is_fraud,
        "detected_text": req.text,
        "warning_text": warning_text if is_fraud else None,
        "audio_b64": audio_b64
    })

# -------------------------------------------------------------------
# 4. Real-Time Streaming WebSocket Interceptor
# -------------------------------------------------------------------
@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    await websocket.accept()
    print("[WebSocket] Client connected for live call interception.")
    
    try:
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)
            
            transcript = ""
            lang = data.get("language", "hi-IN")
            
            if "audio_b64" in data:
                audio_bytes = base64.b64decode(data["audio_b64"])
                transcript = transcribe_audio_gnani(audio_bytes, lang)
            elif "text" in data:
                transcript = data["text"]
                
            if transcript:
                is_threat = evaluate_threat_score(transcript)
                response_payload = {
                    "transcript": transcript,
                    "is_threat": is_threat
                }
                
                if is_threat:
                    warning_msg = WARNING_TEXTS.get(lang, WARNING_TEXTS["en-IN"])
                    response_payload["warning_msg"] = warning_msg
                    
                    audio_raw = synthesize_voice_warning_gnani(warning_msg, lang)
                    if audio_raw:
                        response_payload["audio_b64"] = base64.b64encode(audio_raw).decode("utf-8")
                        
                await websocket.send_text(json.dumps(response_payload))
                
    except WebSocketDisconnect:
        print("[WebSocket] Session disconnected.")

# -------------------------------------------------------------------
# 5. Built-in Interactive Web Interface (HTML/JS)
# -------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Kaala-Vaani | Voice Fraud Interceptor</title>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 20px; }
            .container { max-width: 800px; margin: 0 auto; background: #1e293b; border-radius: 12px; padding: 24px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }
            h1 { color: #38bdf8; margin-top: 0; }
            .status-box { padding: 16px; border-radius: 8px; background: #334155; margin-bottom: 20px; border-left: 4px solid #38bdf8; }
            .alert-box { display: none; padding: 16px; border-radius: 8px; background: #7f1d1d; border: 2px solid #ef4444; color: #fecaca; margin-bottom: 20px; font-weight: bold; }
            textarea { width: 100%; height: 80px; background: #0f172a; color: #fff; border: 1px solid #475569; border-radius: 6px; padding: 10px; box-sizing: border-box; }
            button { background: #0284c7; color: white; border: none; padding: 10px 20px; border-radius: 6px; font-weight: bold; cursor: pointer; margin-top: 10px; margin-right: 8px; }
            button:hover { background: #0369a1; }
            .log { background: #0f172a; border-radius: 6px; padding: 12px; height: 180px; overflow-y: auto; font-family: monospace; font-size: 13px; color: #94a3b8; border: 1px solid #334155; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🛡️ Kaala-Vaani Interceptor</h1>
            <p>Real-Time Multilingual Voice Fraud Interceptor (Powered by Gnani AI)</p>
            
            <div id="alertBox" class="alert-box">
                🚨 THREAT DETECTED: <span id="alertMsg">Suspected Extortion / Digital Arrest Call</span>
            </div>

            <div class="status-box">
                <label>Select Preferred Language:</label>
                <select id="langSelect" style="background:#0f172a; color:#fff; padding:6px; border-radius:4px; border:1px solid #475569;">
                    <option value="hi-IN">Hindi (हिंदी)</option>
                    <option value="kn-IN">Kannada (ಕನ್ನಡ)</option>
                    <option value="en-IN">English</option>
                </select>
            </div>

            <div style="margin-bottom: 20px;">
                <h3>Simulate Incoming Speech / Audio Transcript</h3>
                <textarea id="inputText" placeholder="e.g. This is Police Department, transfer money immediately or face digital arrest..."></textarea>
                <br>
                <button onclick="testSimulatedCall()">Scan Call Text</button>
                <button onclick="testPredefinedScam()" style="background:#dc2626;">Test "Digital Arrest" Fraud</button>
            </div>

            <h3>Live Interception Log</h3>
            <div id="logBox" class="log">Ready to monitor call session...</div>
        </div>

        <script>
            function log(msg) {
                const box = document.getElementById("logBox");
                box.innerHTML += "<div>> " + msg + "</div>";
                box.scrollTop = box.scrollHeight;
            }

            async function testSimulatedCall() {
                const text = document.getElementById("inputText").value;
                const lang = document.getElementById("langSelect").value;
                if (!text) return;

                log("Scanning transcript: '" + text + "'");
                
                const response = await fetch('/api/check-threat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: text, language: lang })
                });

                const data = await response.json();
                if (data.is_fraud) {
                    document.getElementById("alertBox").style.display = "block";
                    document.getElementById("alertMsg").innerText = data.warning_text;
                    log("<span style='color:#ef4444;'>[ALERT TRIGGERED] High-risk fraud pattern found!</span>");
                    
                    if (data.audio_b64) {
                        log("Playing Gnani Timbre TTS Warning Audio...");
                        const audio = new Audio("data:audio/wav;base64," + data.audio_b64);
                        audio.play();
                    }
                } else {
                    document.getElementById("alertBox").style.display = "none";
                    log("<span style='color:#22c55e;'>[CLEAN] No threat vectors detected.</span>");
                }
            }

            function testPredefinedScam() {
                document.getElementById("inputText").value = "We are calling from CBI Police department. Your account is linked to illegal money laundering. Pay fine immediately to avoid digital arrest.";
                testSimulatedCall();
            }
        </script>
    </body>
    </html>
    """