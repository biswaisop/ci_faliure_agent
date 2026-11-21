from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db.indexes import setup_indexes
from app.db.mongodb import MongoDb
from app.routes import clerk_webhook, gh_installations, gh_webhook, users


@asynccontextmanager
async def lifespan(app: FastAPI):
    MongoDb.connect()

    try:
        await setup_indexes()
        yield
    finally:
        MongoDb.disconnect()

app = FastAPI(
    title="AutopCI API",
    description="Backend API for AutopCI",
    version="0.1.0",
    lifespan=lifespan,
)

# Configure CORS for local development and frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"message": "AutopCI API is running"}


@app.get("/health", status_code=200)
def health_check():
    return {"status": "ok"}




app.include_router(gh_webhook.router,  tags=["webhook"])
app.include_router(clerk_webhook.router)
app.include_router(users.router)
app.include_router(gh_installations.router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
