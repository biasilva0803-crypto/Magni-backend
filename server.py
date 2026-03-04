import os
import hashlib
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

client: Optional[AsyncIOMotorClient] = None
db = None

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def normalize_password(p: str) -> str:
    """
    bcrypt tem limite de 72 bytes. Para evitar problemas com espaços/acentos/cópias,
    transformamos a password num digest SHA-256 (tamanho fixo).
    Depois bcrypt faz hash desse digest.
    """
    raw = (p or "").strip().encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# ---------------- MODELS ----------------
class SetupAdmin(BaseModel):
    setup_key: str
    email: str
    password: str
    name: str


class Login(BaseModel):
    email: str
    password: str


# ---------------- STARTUP / SHUTDOWN ----------------
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
        # garante email único
        await db.users.create_index("email", unique=True)
    except Exception as e:
        print(f"❌ erro ao ligar ao MongoDB: {e}")


@app.on_event("shutdown")
async def shutdown():
    global client
    if client is not None:
        client.close()


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
    except Exception as e:
        return {"error": f"erro ao ligar ao MongoDB: {e}"}


# ---------------- CREATE ADMIN ----------------
@app.post("/setup-admin")
async def setup_admin(data: SetupAdmin):
    if db is None:
        raise HTTPException(status_code=500, detail="DB não conectada (ver MONGODB_URI)")

    if not SETUP_KEY:
        raise HTTPException(status_code=500, detail="SETUP_KEY não definido no Railway")

    if data.setup_key != SETUP_KEY:
        raise HTTPException(status_code=403, detail="setup_key inválida")

    # já existe admin?
    admin = await db.users.find_one({"role": "admin"})
    if admin:
        raise HTTPException(status_code=400, detail="Admin já existe")

    # password -> digest -> bcrypt
    pw_digest = normalize_password(data.password)
    password_hash = pwd_context.hash(pw_digest)

    user = {
        "email": data.email.strip().lower(),
        "password": password_hash,
        "name": data.name.strip(),
        "role": "admin",
        "created_at": datetime.utcnow().isoformat() + "Z",
    }

    try:
        result = await db.users.insert_one(user)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao criar admin: {e}")

    return {
        "id": str(result.inserted_id),
        "email": user["email"],
        "role": "admin",
        "name": user["name"],
    }


# ---------------- LOGIN ----------------
@app.post("/login")
async def login(data: Login):
    if db is None:
        raise HTTPException(status_code=500, detail="DB não conectada (ver MONGODB_URI)")

    if not JWT_SECRET:
        raise HTTPException(status_code=500, detail="JWT_SECRET não definido no Railway")

    user = await db.users.find_one({"email": data.email.strip().lower()})
    if not user:
        raise HTTPException(status_code=401, detail="credenciais inválidas")

    pw_digest = normalize_password(data.password)

    if not pwd_context.verify(pw_digest, user["password"]):
        raise HTTPException(status_code=401, detail="credenciais inválidas")

    token = jwt.encode(
        {"user_id": str(user["_id"]), "role": user["role"]},
        JWT_SECRET,
        algorithm="HS256",
    )

    return {
        "token": token,
        "role": user["role"],
        "name": user.get("name", ""),
    }