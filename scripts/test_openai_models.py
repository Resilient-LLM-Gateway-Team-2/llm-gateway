#!/usr/bin/env python3
"""
OpenAI Model Availability Test
Tests which OpenAI models are available with the current API key
"""

import os
import sys
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def test_openai_models():
    """Test available OpenAI models with current API key"""
    
    try:
        from openai import OpenAI, APIError, AuthenticationError, RateLimitError
    except ImportError:
        print("❌ OpenAI package not installed")
        print("Run: pip install openai")
        return False
    
    api_key = os.getenv('OPENAI_API_KEY')
    
    if not api_key:
        print("❌ OPENAI_API_KEY not found in .env file")
        return False
    
    print("=" * 80)
    print("OpenAI Model Availability Test")
    print("=" * 80)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"API Key: {api_key[:20]}...{api_key[-10:]}")
    print()
    
    # List of models to test (in order of preference)
    models_to_test = [
        "gpt-4o-mini",
        "gpt-4.1-mini",
        "gpt-3.5-turbo",
        "gpt-3.5-turbo-16k",
    ]
    
    client = OpenAI(api_key=api_key)
    working_models = []
    failed_models = []
    
    for model in models_to_test:
        print(f"Testing model: {model}...", end=" ")
        sys.stdout.flush()
        
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "user", "content": "What is 2+2?"}
                ],
                max_tokens=10,
                temperature=0.7,
                timeout=10
            )
            
            print("✅ WORKING")
            print(f"  Response: {response.choices[0].message.content.strip()}")
            print(f"  Tokens used: {response.usage.total_tokens}")
            working_models.append(model)
            print()
            
        except RateLimitError as e:
            print("⚠️  RATE LIMITED")
            print(f"  Error: {str(e)[:100]}")
            failed_models.append((model, "Rate Limited"))
            print()
            
        except AuthenticationError as e:
            print("❌ AUTHENTICATION ERROR")
            print(f"  Error: {str(e)[:100]}")
            failed_models.append((model, "Auth Error"))
            print()
            
        except APIError as e:
            error_msg = str(e)
            if "does not exist" in error_msg.lower() or "not found" in error_msg.lower():
                print("❌ MODEL NOT FOUND")
                print(f"  Error: {error_msg[:100]}")
                failed_models.append((model, "Not Found"))
            elif "quota" in error_msg.lower():
                print("❌ QUOTA EXCEEDED")
                print(f"  Error: {error_msg[:100]}")
                failed_models.append((model, "Quota Exceeded"))
            else:
                print("❌ API ERROR")
                print(f"  Error: {error_msg[:100]}")
                failed_models.append((model, error_msg[:50]))
            print()
            
        except Exception as e:
            print(f"❌ ERROR: {str(e)[:80]}")
            failed_models.append((model, str(e)[:50]))
            print()
    
    # Summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    
    if working_models:
        print(f"✅ Available Models ({len(working_models)}):")
        for model in working_models:
            print(f"   • {model}")
        print()
    else:
        print("❌ No working models found")
        print()
    
    if failed_models:
        print(f"❌ Unavailable Models ({len(failed_models)}):")
        for model, reason in failed_models:
            print(f"   • {model}: {reason}")
        print()
    
    print("=" * 80)
    
    if working_models:
        print(f"✅ SUCCESS - {working_models[0]} is available!")
        print(f"Recommended model: {working_models[0]}")
        return True
    else:
        print("❌ No models available - please check your API key and account status")
        print()
        print("Troubleshooting steps:")
        print("1. Check your OpenAI billing page: https://platform.openai.com/account/billing/overview")
        print("2. Verify API key is active and has usage quota")
        print("3. Check rate limits in your account settings")
        print("4. Ensure your API key hasn't expired")
        return False


if __name__ == "__main__":
    print("\n🚀 Starting OpenAI Model Availability Test...\n")
    success = test_openai_models()
    sys.exit(0 if success else 1)
