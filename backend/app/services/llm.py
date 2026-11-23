import os
from functools import lru_cache

from dotenv import load_dotenv
from langchain_nvidia_ai_endpoints import ChatNVIDIA

load_dotenv()


@lru_cache(maxsize=1)
def get_llm():
    return ChatNVIDIA(
        model="nvidia/nemotron-3.5-lightning-30b-a3b",
        api_key=os.getenv("NVIDIA_API_KEY"),
        temperature=1,
        top_p=0.95,
        max_tokens=16384,
        reasoning_budget=16384,
        chat_template_kwargs={"enable_thinking": True},
    )
