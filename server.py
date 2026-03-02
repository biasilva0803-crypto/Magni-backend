import os
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

app = FastAPI()

client = None
db = None

@app.on_event("startup")
async def startup_db():
    global client, db

    mongo_uri = os.getenv("MONGODB_URI")

    if mongo_uri is None:
        print("❌ MONGODB_URI não definida")
        return

    try:
        client = AsyncIOMotorClient(mongo_uri, serverSelectionTimeoutMS=5000)

        # Define explicitamente a base de dados
        db = client["magni"]

        await db.command("ping")
        print("✅ MongoDB ligado com sucesso")

    except Exception as e:
        print("❌ Erro ao ligar ao MongoDB:", e)
        client = None
        db = None


@app.on_event("shutdown")
async def shutdown_db():
    global client
    if client is not None:
        client.close()


@app.get("/")
async def root():
    return {"status": "ok", "service": "magni-backend"}


@app.get("/db-test")
async def db_test():
    if db is None:
        return {"ok": False, "error": "DB não conectada"}

    try:
        await db.command("ping")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}