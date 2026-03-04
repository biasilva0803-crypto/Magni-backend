import os
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import jwt

app = FastAPI()

# ---------------- CONFIG ----------------

MONGODB_URI = os.getenv("MONGODB_URI")
JWT_SECRET = os.getenv("JWT_SECRET")
SETUP_KEY = os.getenv("SETUP_KEY")

client = None
db = None

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ---------------- MODELS ----------------


class SetupAdmin(BaseModel):
    setup_key: str
    email: str
    password: str
    name: str


class Login(BaseModel):
    email: str
    password: str


# ---------------- STARTUP ----------------


@app.on_event("startup")
async def startup():
    global client, db

    if not MONGODB_URI:
        print("❌ MONGODB_URI não definida")
        return

    client = AsyncIOMotorClient(MONGODB_URI)
    db = client.magni

    try:
        await db.command("ping")
        print("✅ MongoDB ligado com sucesso")
    except:
        print("❌ erro ao ligar ao MongoDB")


# ---------------- ROOT ----------------


@app.get("/")
def root():
    return {"status": "ok", "service": "magni-backend"}


# ---------------- DB TEST ----------------


@app.get("/db-test")
async def db_test():

    if db is None:
        return {"error": "DB não conectada"}

    try:
        await db.command("ping")
        return {"ok": True}
    except:
        return {"error": "erro ao ligar ao MongoDB"}


# ---------------- CREATE ADMIN ----------------


@app.post("/setup-admin")
async def setup_admin(data: SetupAdmin):

    if SETUP_KEY != data.setup_key:
        raise HTTPException(status_code=403, detail="setup_key inválida")

    admin = await db.users.find_one({"role": "admin"})

    if admin:
        raise HTTPException(status_code=400, detail="Admin já existe")

    password_hash = pwd_context.hash(data.password)

    user = {
        "email": data.email,
        "password": password_hash,
        "name": data.name,
        "role": "admin",
        "created_at": datetime.utcnow(),
    }

    result = await db.users.insert_one(user)

    return {
        "id": str(result.inserted_id),
        "email": data.email,
        "role": "admin",
        "name": data.name,
    }


# ---------------- LOGIN ----------------


@app.post("/login")
async def login(data: Login):

    user = await db.users.find_one({"email": data.email})

    if not user:
        raise HTTPException(status_code=401, detail="credenciais inválidas")

    if not pwd_context.verify(data.password, user["password"]):
        raise HTTPException(status_code=401, detail="credenciais inválidas")

    token = jwt.encode(
        {"user_id": str(user["_id"]), "role": user["role"]},
        JWT_SECRET,
        algorithm="HS256",
    )

    return {
        "token": token,
        "role": user["role"],
        "name": user["name"],
    }