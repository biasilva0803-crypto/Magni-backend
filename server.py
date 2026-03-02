import os
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field

app = FastAPI()

client: Optional[AsyncIOMotorClient] = None
db = None

# ---------- Models ----------
class NoteCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    content: str = Field(..., min_length=1, max_length=5000)

class NoteOut(BaseModel):
    id: str
    title: str
    content: str
    created_at: str

# ---------- Startup / Shutdown ----------
@app.on_event("startup")
async def startup_db():
    global client, db
    mongo_uri = os.getenv("MONGODB_URI")
    if mongo_uri is None:
        print("❌ MONGODB_URI não definida")
        return

    client = AsyncIOMotorClient(mongo_uri, serverSelectionTimeoutMS=5000)
    db = client["magni"]

    # Ping
    await db.command("ping")
    print("✅ MongoDB ligado com sucesso")

@app.on_event("shutdown")
async def shutdown_db():
    global client
    if client is not None:
        client.close()

# ---------- Helpers ----------
def doc_to_note(doc) -> NoteOut:
    return NoteOut(
        id=str(doc["_id"]),
        title=doc["title"],
        content=doc["content"],
        created_at=doc["created_at"],
    )

def ensure_db():
    if db is None:
        raise HTTPException(status_code=500, detail="DB não conectada (MONGODB_URI em falta?)")

# ---------- Routes ----------
@app.get("/")
async def root():
    return {"status": "ok", "service": "magni-backend"}

@app.get("/db-test")
async def db_test():
    ensure_db()
    await db.command("ping")
    return {"ok": True}

# Create
@app.post("/notes", response_model=NoteOut)
async def create_note(payload: NoteCreate):
    ensure_db()
    doc = {
        "title": payload.title,
        "content": payload.content,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    res = await db.notes.insert_one(doc)
    saved = await db.notes.find_one({"_id": res.inserted_id})
    return doc_to_note(saved)

# Read all
@app.get("/notes", response_model=List[NoteOut])
async def list_notes(limit: int = 50):
    ensure_db()
    limit = max(1, min(limit, 200))
    cursor = db.notes.find().sort("created_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [doc_to_note(d) for d in docs]

# Read one
@app.get("/notes/{note_id}", response_model=NoteOut)
async def get_note(note_id: str):
    ensure_db()
    from bson import ObjectId
    try:
        oid = ObjectId(note_id)
    except Exception:
        raise HTTPException(status_code=400, detail="note_id inválido")

    doc = await db.notes.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Nota não encontrada")
    return doc_to_note(doc)

# Delete
@app.delete("/notes/{note_id}")
async def delete_note(note_id: str):
    ensure_db()
    from bson import ObjectId
    try:
        oid = ObjectId(note_id)
    except Exception:
        raise HTTPException(status_code=400, detail="note_id inválido")

    res = await db.notes.delete_one({"_id": oid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Nota não encontrada")
    return {"ok": True}