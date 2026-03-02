import os
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

app = FastAPI()

MONGODB_URI = os.getenv("MONGODB_URI")

client = None
db = None

@app.on_event("startup")
async def startup_db():
    global client, db
    if not MONGODB_URI:
        print("❌ MONGODB_URI não definida")
        return
    
    client = AsyncIOMotorClient(MONGODB_URI)
    db = client.get_default_database()
    print("✅ MongoDB ligado com sucesso")

@app.get("/")
async def root():
    return {"status": "ok", "service": "magni-backend"}

@app.get("/db-test")
async def db_test():
    if not db:
        return {"error": "DB não conectada"}
    
    await db.command("ping")
    return {"ok": True}