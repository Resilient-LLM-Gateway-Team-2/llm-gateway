#!/usr/bin/env python3
"""List available Gemini models for the API key."""

import os
import sys

def list_gemini_models():
    """List available Gemini models."""
    api_key = os.getenv("GEMINI_API_KEY")
    
    if not api_key:
        print("❌ GEMINI_API_KEY not found in environment")
        return False
    
    print("🚀 Listing available Gemini models...")
    print(f"API Key: {api_key[:20]}...{api_key[-10:]}")
    print("=" * 80)
    
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        
        print("\n📋 Available Models:")
        print("=" * 80)
        
        models = genai.list_models()
        available_models = []
        
        for model in models:
            model_name = model.name.split('/')[-1]  # Extract model name from path
            capabilities = model.supported_generation_methods
            available_models.append({
                'name': model_name,
                'full_path': model.name,
                'capabilities': capabilities
            })
            
            # Check if it supports generateContent
            if 'generateContent' in capabilities:
                print(f"✅ {model_name}")
                print(f"   Full Path: {model.name}")
                print(f"   Supports: {', '.join(capabilities)}")
            else:
                print(f"⚠️  {model_name} (does not support generateContent)")
                print(f"   Supports: {', '.join(capabilities)}")
            print()
        
        print("=" * 80)
        print(f"\n📊 Summary: Found {len(available_models)} models")
        
        # Try testing with the first available generateContent model
        for model in available_models:
            if 'generateContent' in model['capabilities']:
                print(f"\n🧪 Testing with: {model['name']}")
                test_model(genai, model['name'], api_key)
                return True
        
        return False
        
    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
        print(f"\n🔍 Error Details:")
        print(f"   Type: {type(e).__name__}")
        print(f"   Message: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_model(genai, model_name, api_key):
    """Test a specific model."""
    try:
        print(f"\n⏳ Calling {model_name}...")
        
        model = genai.GenerativeModel(model_name)
        response = model.generate_content("What is 2+2? Answer in one sentence.")
        
        print(f"✅ {model_name} is WORKING!")
        print(f"   Response: {response.text[:100]}...")
        
    except Exception as e:
        print(f"❌ {model_name} failed: {str(e)}")

if __name__ == "__main__":
    success = list_gemini_models()
    sys.exit(0 if success else 1)
