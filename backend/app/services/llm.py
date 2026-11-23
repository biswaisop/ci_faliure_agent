import json
from langchain_nvidia_ai_endpoints import ChatNVIDIA
import os

from dotenv import load_dotenv

load_dotenv()

llm = ChatNVIDIA(
    model="nvidia/nemotron-3.5-lightning-30b-a3b",
    api_key=os.getenv("NVIDIA_API_KEY"), 
    temperature=1,
    top_p=0.95,
    max_tokens=16384,
    reasoning_budget=16384,
    chat_template_kwargs={"enable_thinking":True},
)


for chunk in llm.stream([{"role":"user","content":"Write a limerick about the wonders of GPU computing."}]):
  
    if chunk.additional_kwargs and "reasoning_content" in chunk.additional_kwargs:
      print(chunk.additional_kwargs["reasoning_content"], end="")
  
    print(chunk.content, end="")