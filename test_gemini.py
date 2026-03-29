
import os
import google.generativeai as genai
from pathlib import Path

# Manually load .env since it's not loaded by default
env_path = Path("/home/nasa/work/investment/.env")
api_key = None
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if line.startswith("GEMINI_API_KEY="):
            api_key = line.split("=", 1)[1].strip()
            break

if not api_key:
    print("API Key not found in .env")
else:
    print(f"Using API Key: {api_key[:5]}...{api_key[-5:]}")
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-1.5-flash") # Test with a stable model
        response = model.generate_content("Hello, wave at me.")
        print("Gemini Response:", response.text)
    except Exception as e:
        print("Gemini Test Failed:", e)
