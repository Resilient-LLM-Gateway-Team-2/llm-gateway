from fastapi import FastAPI

app = FastAPI(title="Resilient LLM Gateway - Sprint 1")

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/chat")
def chat():
    # Sprint 1 placeholder response
    return {"reply": "Mock response from gateway"}