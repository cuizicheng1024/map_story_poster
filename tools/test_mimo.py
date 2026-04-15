import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from auto_generate import build_story_system_prompt, call_openai_compatible


load_dotenv(".env")

api_key = os.getenv("API_KEY")
base_url = os.getenv("BASE_URL")
model = os.getenv("MODEL")

messages = [
    {"role": "system", "content": build_story_system_prompt()},
    {"role": "user", "content": "人物姓名：岳飞"},
]

raw = call_openai_compatible(
    messages=messages,
    api_key=api_key,
    model=model,
    base_url=base_url,
    timeout=120,
    temperature=0.2,
)
print(raw[:300])
