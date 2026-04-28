from fastapi import FastAPI

from nitpick.api.webhooks import router as webhooks_router

app = FastAPI(title="Nitpick", version="0.1.0")
app.include_router(webhooks_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
