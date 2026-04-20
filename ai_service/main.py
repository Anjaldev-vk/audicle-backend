from fastapi import FastAPI

app = FastAPI()

@app.get("/api/v1/ai/health")
def health_check():
    return {"status": "AI Service is Online", "version": "v1"}
