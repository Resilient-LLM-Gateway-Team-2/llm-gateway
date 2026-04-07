#!/usr/bin/env python3
"""Test Gemini API key and model availability."""

import os
import sys
import time

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.providers import call_gemini
from app.schemas import ChatRequest, Message

def test_gemini():
    """Test Gemini API with the provided key."""
    api_key = os.getenv("GEMINI_API_KEY")
    
    if not api_key:
        print("❌ GEMINI_API_KEY not found in environment")
        return False
    
    print("🚀 Starting Gemini API Test...")
    print(f"API Key: {api_key[:20]}...{api_key[-10:]}")
    print("=" * 80)
    
    test_prompt = "What is 2+2? Answer in one sentence."
    
    try:
        print(f"\n📝 Test Prompt: {test_prompt}")
        print("\n⏳ Calling Gemini API (gemini-2.5-flash)...")
        
        # Create ChatRequest
        request = ChatRequest(
            model="gemini-2.5-flash",
            messages=[Message(role="user", content=test_prompt)],
            temperature=0.7,
            max_tokens=256
        )
        
        start_time = time.time()
        response = call_gemini(request)
        latency = time.time() - start_time
        
        print(f"\n✅ SUCCESS - Gemini API is working!")
        print(f"\n📊 Response Details:")
        print(f"   Provider: {response.provider}")
        print(f"   Model: {response.model}")
        print(f"   Content: {response.content}")
        print(f"   Latency: {latency:.2f}s")
        
        if response.usage:
            print(f"   Prompt Tokens: {response.usage.prompt_tokens}")
            print(f"   Completion Tokens: {response.usage.completion_tokens}")
            print(f"   Total Tokens: {response.usage.total_tokens}")
        
        print("\n" + "=" * 80)
        print("✅ Gemini API Key is VALID and WORKING!")
        print("=" * 80)
        return True
        
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        print(f"\n🔍 Error Details:")
        print(f"   Type: {type(e).__name__}")
        print(f"   Message: {str(e)}")
        import traceback
        print(f"\n📋 Full Traceback:")
        traceback.print_exc()
        print("\n" + "=" * 80)
        print("❌ Gemini API Key test FAILED")
        print("=" * 80)
        return False

if __name__ == "__main__":
    success = test_gemini()
    sys.exit(0 if success else 1)
