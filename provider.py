"""
LLM Client Module
-----------------
This module manages the communication with the SiliconFlow API.
"""

import os
import re
import json
import base64
import requests
from typing import Optional, Dict, Any
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# API Configuration
PROVIDERS = {
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1/chat/completions",
        "env_key": "SILICON_API_KEY",
        "default_model": "deepseek-ai/DeepSeek-V3.2"
    },
    "deepseek_official": {
        "base_url": "https://api.deepseek.com/chat/completions",
        "env_key": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-v4-flash"
    },
    "dreamfield": {
        "base_url": "https://www.dreamfield.top/v1/chat/completions",
        "env_key": "DREAMFIELD_API_KEY",
        "default_model": "DeepSeek-V4-Pro"
    },
    "sky": {
        "base_url": "https://www.lant.top/relay-api/v1/chat/completions",
        "env_key": "SKY_API_KEY",
        "default_model": "gpt‑4t"
    }
}

DEFAULT_TEXT_MODEL = "gpt‑4t"
DEFAULT_PROVIDER = "deepseek_official"
VLM_PROVIDER = "siliconflow"
REQUEST_TIMEOUT = (10, 500) 


def _get_headers(api_key: str) -> Dict[str, str]:
    if not api_key:
        raise ValueError("Missing API Key. Please set the corresponding environment variable.")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }


def get_ai_response(content: str, prompt: str, model: str = None, provider: str = DEFAULT_PROVIDER) -> str:
    """Sends a text document to the LLM for analysis/extraction."""
    if provider not in PROVIDERS:
        return f"[Config Error] Unknown provider: {provider}"
    
    config = PROVIDERS[provider]
    api_key = os.getenv(config["env_key"])
    base_url = config["base_url"]
    
    if not model:
        model = config["default_model"]

    if not content or not content.strip():
        return "[Client Error] Input content is empty."
    
    if not api_key:
        return f"[Config Error] {config['env_key']} not found in environment."

    print(f"[LLM] Sending Request ({provider}) -> Model: {model}")

    full_message = f"{prompt}\n\n--- DOCUMENT CONTENT START ---\n{content}\n--- DOCUMENT CONTENT END ---"
    
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": full_message}
        ],
        "stream": False,
        "temperature": 0.2,
        "max_tokens": 16384
    }

    try:
        response = requests.post(
            base_url, 
            headers=_get_headers(api_key), 
            json=payload, 
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        
        data = response.json()
        message = data["choices"][0].get("message", {})
        content = message.get("content") or ""
        if not content:
            content = message.get("reasoning_content") or ""
        if content and "<think>" in content:
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
        if not content:
            debug_text = response.text[:300]
            print(f"[LLM WARN] API returned empty content. Status={response.status_code}, Body={debug_text.encode('gbk', 'replace').decode('gbk')}")
        return content

    except requests.exceptions.Timeout:
        error_msg = f"[Network Error] Request timed out after {REQUEST_TIMEOUT[1]}s."
        print(error_msg)
        return error_msg
        
    except requests.exceptions.RequestException as e:
        error_msg = f"[API Error] {e}"
        if hasattr(e, 'response') and e.response is not None:
            try:
                server_msg = e.response.json().get('error', {}).get('message', '')
                error_msg += f" | Server says: {server_msg}"
            except:
                error_msg += f" | Response: {e.response.text}"
        print(error_msg)
        return error_msg
        
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        error_msg = f"[Parsing Error] Failed to parse API response: {e}"
        print(error_msg)
        return error_msg


def get_visual_response(image_path: str, prompt: str, model: str = "Qwen/Qwen3-VL-8B-Instruct", provider: str = VLM_PROVIDER) -> str:
    """Sends an image and a prompt to the Vision Language Model (VLM)."""
    if provider not in PROVIDERS:
        return f"[Config Error] Unknown provider: {provider}"
    
    config = PROVIDERS[provider]
    api_key = os.getenv(config["env_key"])
    base_url = config["base_url"]
    
    if not api_key:
        return f"[Config Error] {config['env_key']} not found in environment."

    if not os.path.exists(image_path):
        return f"[File Error] Image not found: {image_path}"

    print(f"[VLM] Sending Image Request ({provider}) -> Model: {model}")

    try:
        with open(image_path, "rb") as image_file:
            encoded_image = base64.b64encode(image_file.read()).decode('utf-8')

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{encoded_image}"
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }
        ]

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": 0.1,
            "max_tokens": 1024
        }

        response = requests.post(
            base_url, 
            headers=_get_headers(api_key), 
            json=payload, 
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    except Exception as e:
        error_msg = f"[VLM API Error] {e}"
        if hasattr(e, 'response') and e.response is not None:
            try:
                server_msg = e.response.json().get('error', {}).get('message', '')
                error_msg += f" | Server says: {server_msg}"
            except:
                error_msg += f" | Response: {e.response.text}"
        print(error_msg)
        return error_msg

def chat_with_tools(messages: list, tools: list, model: str = None, provider: str = DEFAULT_PROVIDER) -> dict:
    """支持 Function Calling (工具调用) 的底层网络请求"""
    config = PROVIDERS.get(provider, PROVIDERS[DEFAULT_PROVIDER])
    api_key = os.getenv(config["env_key"])
    base_url = config["base_url"]
    model = model or config["default_model"]
    
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "stream": False
    }
    
    try:
        response = requests.post(
            base_url, 
            headers=_get_headers(api_key), 
            json=payload, 
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]
    except Exception as e:
        print(f"[API Error in Tools] {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(e.response.text)
        return {"role": "assistant", "content": f"API请求失败: {e}"}
