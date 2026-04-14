import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from auto_generate import call_openai_compatible, build_story_system_prompt
from dotenv import load_dotenv

load_dotenv("map_story_poster/.env")

api_key = os.getenv("API_KEY")
base_url = os.getenv("BASE_URL")
model = os.getenv("MODEL")

messages = [
    {"role": "system", "content": build_story_system_prompt()},
    {"role": "user", "content": "人物姓名：岳飞"},
]

try:
    raw = call_openai_compatible(
        messages=messages,
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout=120,
        temperature=0.2,
    )
    print("SUCCESS")
    print(raw[:100])
except Exception as e:
    import traceback
    traceback.print_exc()
