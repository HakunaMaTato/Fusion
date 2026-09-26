from fastapi import FastAPI

app = FastAPI(title="LP Radar")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
