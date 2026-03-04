from fastapi import FastAPI, APIRouter, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Literal
import uuid
from datetime import datetime, date
from jose import JWTError, jwt
from passlib.context import CryptContext
import io
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ.get('DB_NAME', 'obras_app')]

# JWT Settings
SECRET_KEY = os.environ.get('SECRET_KEY', 'super-secret-key-change-in-production')
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

# Create the main app without a prefix
app = FastAPI(title="Gestão de Obras API")

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== MODELS ====================

class UserCreate(BaseModel):
    email: str
    password: str
    name: str
    role: Literal["admin", "funcionario"] = "funcionario"

class UserLogin(BaseModel):
    email: str
    password: str

class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    role: str
    created_at: datetime

class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse

class FuncionarioCreate(BaseModel):
    name: str

class FuncionarioResponse(BaseModel):
    id: str
    name: str
    user_id: str
    created_at: datetime

class ObraCreate(BaseModel):
    name: str

class ObraResponse(BaseModel):
    id: str
    name: str
    user_id: str
    created_at: datetime

class RegistoEntrada(BaseModel):
    tipo: str  # 'obra' or 'falta'
    obra_id: Optional[str] = None
    horas: float  # hours worked

class RegistoCreate(BaseModel):
    funcionario_id: str
    data: str  # date in YYYY-MM-DD format
    entradas: List[RegistoEntrada]
    observacoes: Optional[str] = None  # Campo para observações/notas

class RegistoResponse(BaseModel):
    id: str
    funcionario_id: str
    data: str
    entradas: List[dict]
    total_horas: float
    observacoes: Optional[str] = None
    user_id: str
    created_by: Optional[str] = None
    created_by_name: Optional[str] = None
    created_at: datetime

# ==================== AUTH HELPERS ====================

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Token inválido")
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido")
    
    user = await db.users.find_one({"id": user_id})
    if user is None:
        raise HTTPException(status_code=401, detail="Utilizador não encontrado")
    return user

async def get_user_from_token(token: str):
    """Get user from token string (for query parameter auth)"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            return None
    except JWTError:
        return None
    
    user = await db.users.find_one({"id": user_id})
    return user

async def get_current_user_optional(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Same as get_current_user but doesn't raise on failure"""
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            return None
    except:
        return None
    
    user = await db.users.find_one({"id": user_id})
    return user

def require_admin(current_user: dict):
    """Check if user is admin"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Acesso não autorizado. Apenas administradores podem realizar esta ação.")
    return current_user

# ==================== AUTH ROUTES ====================

@api_router.post("/auth/register", response_model=Token)
async def register(user_data: UserCreate):
    # Check if user exists
    existing = await db.users.find_one({"email": user_data.email.lower()})
    if existing:
        raise HTTPException(status_code=400, detail="Email já registado")
    
    # Create user
    user_id = str(uuid.uuid4())
    user = {
        "id": user_id,
        "email": user_data.email.lower(),
        "password": get_password_hash(user_data.password),
        "name": user_data.name,
        "role": user_data.role,
        "created_at": datetime.utcnow()
    }
    await db.users.insert_one(user)
    
    # Create token
    access_token = create_access_token(data={"sub": user_id})
    
    return Token(
        access_token=access_token,
        token_type="bearer",
        user=UserResponse(
            id=user_id,
            email=user["email"],
            name=user["name"],
            role=user["role"],
            created_at=user["created_at"]
        )
    )

@api_router.post("/auth/login", response_model=Token)
async def login(user_data: UserLogin):
    user = await db.users.find_one({"email": user_data.email.lower()})
    if not user or not verify_password(user_data.password, user["password"]):
        raise HTTPException(status_code=401, detail="Email ou palavra-passe incorretos")
    
    access_token = create_access_token(data={"sub": user["id"]})
    
    return Token(
        access_token=access_token,
        token_type="bearer",
        user=UserResponse(
            id=user["id"],
            email=user["email"],
            name=user["name"],
            role=user.get("role", "funcionario"),
            created_at=user["created_at"]
        )
    )

@api_router.get("/auth/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    return UserResponse(
        id=current_user["id"],
        email=current_user["email"],
        name=current_user["name"],
        role=current_user.get("role", "funcionario"),
        created_at=current_user["created_at"]
    )

# ==================== ADMIN: CREATE FUNCIONARIO USER ====================

@api_router.post("/admin/create-user", response_model=UserResponse)
async def admin_create_user(user_data: UserCreate, current_user: dict = Depends(get_current_user)):
    """Admin creates a new funcionario user account"""
    require_admin(current_user)
    
    # Check if user exists
    existing = await db.users.find_one({"email": user_data.email.lower()})
    if existing:
        raise HTTPException(status_code=400, detail="Email já registado")
    
    # Create user (funcionario by default)
    user_id = str(uuid.uuid4())
    user = {
        "id": user_id,
        "email": user_data.email.lower(),
        "password": get_password_hash(user_data.password),
        "name": user_data.name,
        "role": "funcionario",  # Always funcionario when created by admin
        "admin_id": current_user["id"],  # Track which admin created this user
        "created_at": datetime.utcnow()
    }
    await db.users.insert_one(user)
    
    return UserResponse(
        id=user_id,
        email=user["email"],
        name=user["name"],
        role=user["role"],
        created_at=user["created_at"]
    )

@api_router.get("/admin/users", response_model=List[UserResponse])
async def admin_list_users(current_user: dict = Depends(get_current_user)):
    """Admin lists all funcionario users they created"""
    require_admin(current_user)
    
    users = await db.users.find({"admin_id": current_user["id"]}).to_list(1000)
    return [UserResponse(
        id=u["id"],
        email=u["email"],
        name=u["name"],
        role=u.get("role", "funcionario"),
        created_at=u["created_at"]
    ) for u in users]

@api_router.delete("/admin/users/{user_id}")
async def admin_delete_user(user_id: str, current_user: dict = Depends(get_current_user)):
    """Admin deletes a funcionario user"""
    require_admin(current_user)
    
    result = await db.users.delete_one({"id": user_id, "admin_id": current_user["id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Utilizador não encontrado")
    return {"message": "Utilizador eliminado"}

# ==================== FUNCIONARIOS ROUTES (ADMIN ONLY) ====================

@api_router.post("/funcionarios", response_model=FuncionarioResponse)
async def create_funcionario(data: FuncionarioCreate, current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    
    funcionario_id = str(uuid.uuid4())
    funcionario = {
        "id": funcionario_id,
        "name": data.name,
        "user_id": current_user["id"],
        "created_at": datetime.utcnow()
    }
    await db.funcionarios.insert_one(funcionario)
    return FuncionarioResponse(**funcionario)

@api_router.get("/funcionarios", response_model=List[FuncionarioResponse])
async def list_funcionarios(current_user: dict = Depends(get_current_user)):
    # Both admin and funcionario can see the list
    # Funcionarios see their admin's funcionarios
    if current_user.get("role") == "admin":
        admin_id = current_user["id"]
    else:
        admin_id = current_user.get("admin_id")
        if not admin_id:
            raise HTTPException(status_code=403, detail="Conta não associada a um administrador")
    
    funcionarios = await db.funcionarios.find({"user_id": admin_id}).to_list(1000)
    return [FuncionarioResponse(**f) for f in funcionarios]

@api_router.put("/funcionarios/{funcionario_id}", response_model=FuncionarioResponse)
async def update_funcionario(funcionario_id: str, data: FuncionarioCreate, current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    
    result = await db.funcionarios.update_one(
        {"id": funcionario_id, "user_id": current_user["id"]},
        {"$set": {"name": data.name}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Funcionário não encontrado")
    
    funcionario = await db.funcionarios.find_one({"id": funcionario_id})
    return FuncionarioResponse(**funcionario)

@api_router.delete("/funcionarios/{funcionario_id}")
async def delete_funcionario(funcionario_id: str, current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    
    result = await db.funcionarios.delete_one({"id": funcionario_id, "user_id": current_user["id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Funcionário não encontrado")
    # Also delete all registos for this funcionario
    await db.registos.delete_many({"funcionario_id": funcionario_id})
    return {"message": "Funcionário eliminado"}

# ==================== OBRAS ROUTES (ADMIN ONLY) ====================

@api_router.post("/obras", response_model=ObraResponse)
async def create_obra(data: ObraCreate, current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    
    obra_id = str(uuid.uuid4())
    obra = {
        "id": obra_id,
        "name": data.name,
        "user_id": current_user["id"],
        "created_at": datetime.utcnow()
    }
    await db.obras.insert_one(obra)
    return ObraResponse(**obra)

@api_router.get("/obras", response_model=List[ObraResponse])
async def list_obras(current_user: dict = Depends(get_current_user)):
    # Both admin and funcionario can see the list
    if current_user.get("role") == "admin":
        admin_id = current_user["id"]
    else:
        admin_id = current_user.get("admin_id")
        if not admin_id:
            raise HTTPException(status_code=403, detail="Conta não associada a um administrador")
    
    obras = await db.obras.find({"user_id": admin_id}).to_list(1000)
    return [ObraResponse(**o) for o in obras]

@api_router.put("/obras/{obra_id}", response_model=ObraResponse)
async def update_obra(obra_id: str, data: ObraCreate, current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    
    result = await db.obras.update_one(
        {"id": obra_id, "user_id": current_user["id"]},
        {"$set": {"name": data.name}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Obra não encontrada")
    
    obra = await db.obras.find_one({"id": obra_id})
    return ObraResponse(**obra)

@api_router.delete("/obras/{obra_id}")
async def delete_obra(obra_id: str, current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    
    result = await db.obras.delete_one({"id": obra_id, "user_id": current_user["id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Obra não encontrada")
    return {"message": "Obra eliminada"}

# ==================== REGISTOS ROUTES ====================

@api_router.post("/registos", response_model=RegistoResponse)
async def create_or_update_registo(data: RegistoCreate, current_user: dict = Depends(get_current_user)):
    # Determine admin_id based on user role
    if current_user.get("role") == "admin":
        admin_id = current_user["id"]
    else:
        admin_id = current_user.get("admin_id")
        if not admin_id:
            raise HTTPException(status_code=403, detail="Conta não associada a um administrador")
    
    # Validate total hours <= 8
    total_horas = sum(e.horas for e in data.entradas)
    if total_horas > 8:
        raise HTTPException(status_code=400, detail="Total de horas não pode exceder 8 horas por dia")
    
    # Check if registo exists for this date and funcionario
    existing = await db.registos.find_one({
        "funcionario_id": data.funcionario_id,
        "data": data.data,
        "user_id": admin_id
    })
    
    if existing:
        # VALIDATION: Check if another user already created this registo
        if existing.get("created_by") and existing.get("created_by") != current_user["id"]:
            # Get the name of who created it
            creator = await db.users.find_one({"id": existing["created_by"]})
            creator_name = creator["name"] if creator else "outro utilizador"
            
            # Get funcionario name
            funcionario = await db.funcionarios.find_one({"id": data.funcionario_id})
            funcionario_name = funcionario["name"] if funcionario else "este funcionário"
            
            raise HTTPException(
                status_code=409, 
                detail=f"ERRO: O registo do dia {data.data} para {funcionario_name} já foi preenchido por {creator_name}. Não é possível alterar."
            )
    
    entradas_dict = [{"tipo": e.tipo, "obra_id": e.obra_id, "horas": e.horas} for e in data.entradas]
    
    if existing:
        # Update existing (only if same user created it)
        await db.registos.update_one(
            {"id": existing["id"]},
            {"$set": {"entradas": entradas_dict, "total_horas": total_horas, "observacoes": data.observacoes}}
        )
        registo = await db.registos.find_one({"id": existing["id"]})
    else:
        # Create new
        registo_id = str(uuid.uuid4())
        registo = {
            "id": registo_id,
            "funcionario_id": data.funcionario_id,
            "data": data.data,
            "entradas": entradas_dict,
            "total_horas": total_horas,
            "observacoes": data.observacoes,
            "user_id": admin_id,  # The admin who owns this data
            "created_by": current_user["id"],  # The user who created this registo
            "created_by_name": current_user["name"],
            "created_at": datetime.utcnow()
        }
        await db.registos.insert_one(registo)
    
    return RegistoResponse(**registo)

@api_router.get("/registos", response_model=List[RegistoResponse])
async def list_registos(
    funcionario_id: Optional[str] = None,
    obra_id: Optional[str] = None,
    mes: Optional[int] = None,
    ano: Optional[int] = None,
    current_user: dict = Depends(get_current_user)
):
    # Determine admin_id based on user role
    if current_user.get("role") == "admin":
        admin_id = current_user["id"]
    else:
        admin_id = current_user.get("admin_id")
        if not admin_id:
            raise HTTPException(status_code=403, detail="Conta não associada a um administrador")
    
    query = {"user_id": admin_id}
    
    if funcionario_id:
        query["funcionario_id"] = funcionario_id
    
    registos = await db.registos.find(query).to_list(10000)
    
    # Filter by date if specified
    result = []
    for r in registos:
        data_parts = r["data"].split("-")
        r_ano = int(data_parts[0])
        r_mes = int(data_parts[1])
        
        if ano and r_ano != ano:
            continue
        if mes and r_mes != mes:
            continue
        
        # Filter by obra_id if specified
        if obra_id:
            has_obra = any(e.get("obra_id") == obra_id for e in r["entradas"])
            if not has_obra:
                continue
        
        result.append(RegistoResponse(**r))
    
    return result

@api_router.get("/registos/{registo_id}", response_model=RegistoResponse)
async def get_registo(registo_id: str, current_user: dict = Depends(get_current_user)):
    if current_user.get("role") == "admin":
        admin_id = current_user["id"]
    else:
        admin_id = current_user.get("admin_id")
    
    registo = await db.registos.find_one({"id": registo_id, "user_id": admin_id})
    if not registo:
        raise HTTPException(status_code=404, detail="Registo não encontrado")
    return RegistoResponse(**registo)

@api_router.delete("/registos/{registo_id}")
async def delete_registo(registo_id: str, current_user: dict = Depends(get_current_user)):
    # Only admin can delete
    require_admin(current_user)
    
    result = await db.registos.delete_one({"id": registo_id, "user_id": current_user["id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Registo não encontrado")
    return {"message": "Registo eliminado"}

# ==================== REPORTS ROUTES (ADMIN ONLY) ====================

@api_router.get("/relatorios/funcionario/{funcionario_id}")
async def get_relatorio_funcionario(
    funcionario_id: str,
    mes: Optional[int] = None,
    ano: Optional[int] = None,
    current_user: dict = Depends(get_current_user)
):
    require_admin(current_user)
    
    funcionario = await db.funcionarios.find_one({"id": funcionario_id, "user_id": current_user["id"]})
    if not funcionario:
        raise HTTPException(status_code=404, detail="Funcionário não encontrado")
    
    query = {"funcionario_id": funcionario_id, "user_id": current_user["id"]}
    registos = await db.registos.find(query).to_list(10000)
    
    # Get all obras for name lookup
    obras = await db.obras.find({"user_id": current_user["id"]}).to_list(1000)
    obras_dict = {o["id"]: o["name"] for o in obras}
    
    # Filter and aggregate
    obras_horas = {}
    total_faltas = 0
    total_horas = 0
    registos_filtered = []
    
    for r in registos:
        data_parts = r["data"].split("-")
        r_ano = int(data_parts[0])
        r_mes = int(data_parts[1])
        
        if ano and r_ano != ano:
            continue
        if mes and r_mes != mes:
            continue
        
        registos_filtered.append(r)
        
        for e in r["entradas"]:
            if e["tipo"] == "falta":
                total_faltas += e["horas"]
            elif e["tipo"] == "obra" and e.get("obra_id"):
                obra_name = obras_dict.get(e["obra_id"], "Obra Desconhecida")
                obras_horas[obra_name] = obras_horas.get(obra_name, 0) + e["horas"]
            total_horas += e["horas"]
    
    return {
        "funcionario": funcionario["name"],
        "periodo": f"{mes}/{ano}" if mes and ano else (str(ano) if ano else "Todos"),
        "total_horas": total_horas,
        "total_faltas": total_faltas,
        "obras": obras_horas,
        "num_registos": len(registos_filtered)
    }

@api_router.get("/relatorios/obra/{obra_id}")
async def get_relatorio_obra(
    obra_id: str,
    mes: Optional[int] = None,
    ano: Optional[int] = None,
    current_user: dict = Depends(get_current_user)
):
    require_admin(current_user)
    
    obra = await db.obras.find_one({"id": obra_id, "user_id": current_user["id"]})
    if not obra:
        raise HTTPException(status_code=404, detail="Obra não encontrada")
    
    registos = await db.registos.find({"user_id": current_user["id"]}).to_list(10000)
    
    # Get all funcionarios for name lookup
    funcionarios = await db.funcionarios.find({"user_id": current_user["id"]}).to_list(1000)
    funcionarios_dict = {f["id"]: f["name"] for f in funcionarios}
    
    # Filter and aggregate
    funcionarios_horas = {}
    total_horas = 0
    
    for r in registos:
        data_parts = r["data"].split("-")
        r_ano = int(data_parts[0])
        r_mes = int(data_parts[1])
        
        if ano and r_ano != ano:
            continue
        if mes and r_mes != mes:
            continue
        
        for e in r["entradas"]:
            if e["tipo"] == "obra" and e.get("obra_id") == obra_id:
                funcionario_name = funcionarios_dict.get(r["funcionario_id"], "Funcionário Desconhecido")
                funcionarios_horas[funcionario_name] = funcionarios_horas.get(funcionario_name, 0) + e["horas"]
                total_horas += e["horas"]
    
    return {
        "obra": obra["name"],
        "periodo": f"{mes}/{ano}" if mes and ano else (str(ano) if ano else "Todos"),
        "total_horas": total_horas,
        "funcionarios": funcionarios_horas
    }

# ==================== EXPORT ROUTES (ADMIN ONLY) ====================

from fastapi import Request

@api_router.get("/export/funcionario/{funcionario_id}/excel")
async def export_funcionario_excel(
    request: Request,
    funcionario_id: str,
    mes: Optional[int] = None,
    ano: Optional[int] = None,
    token: Optional[str] = None,
):
    # Get token from header or query parameter
    current_user = None
    
    # Try header first
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        header_token = auth_header.split(" ")[1]
        current_user = await get_user_from_token(header_token)
    
    # Try query parameter
    if not current_user and token:
        current_user = await get_user_from_token(token)
    
    if not current_user:
        raise HTTPException(status_code=401, detail="Token inválido ou ausente")
    
    require_admin(current_user)
    
    funcionario = await db.funcionarios.find_one({"id": funcionario_id, "user_id": current_user["id"]})
    if not funcionario:
        raise HTTPException(status_code=404, detail="Funcionário não encontrado")
    
    query = {"funcionario_id": funcionario_id, "user_id": current_user["id"]}
    registos = await db.registos.find(query).to_list(10000)
    
    obras = await db.obras.find({"user_id": current_user["id"]}).to_list(1000)
    obras_dict = {o["id"]: o["name"] for o in obras}
    
    wb = Workbook()
    ws = wb.active
    ws.title = "Mapa de Horas"
    
    # Header
    ws.append([f"Mapa de Horas - {funcionario['name']}"])
    periodo = f"{mes}/{ano}" if mes and ano else (str(ano) if ano else "Todos os períodos")
    ws.append([f"Período: {periodo}"])
    ws.append([])
    ws.append(["Data", "Obra/Falta", "Horas"])
    
    total_horas = 0
    for r in registos:
        data_parts = r["data"].split("-")
        r_ano = int(data_parts[0])
        r_mes = int(data_parts[1])
        
        if ano and r_ano != ano:
            continue
        if mes and r_mes != mes:
            continue
        
        for e in r["entradas"]:
            tipo = "Falta" if e["tipo"] == "falta" else obras_dict.get(e.get("obra_id"), "Obra Desconhecida")
            ws.append([r["data"], tipo, e["horas"]])
            total_horas += e["horas"]
    
    ws.append([])
    ws.append(["Total", "", total_horas])
    
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    
    filename = f"mapa_{funcionario['name'].replace(' ', '_')}_{periodo.replace('/', '-')}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@api_router.get("/export/funcionario/{funcionario_id}/pdf")
async def export_funcionario_pdf(
    request: Request,
    funcionario_id: str,
    mes: Optional[int] = None,
    ano: Optional[int] = None,
    token: Optional[str] = None,
):
    # Get token from header or query parameter
    current_user = None
    
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        header_token = auth_header.split(" ")[1]
        current_user = await get_user_from_token(header_token)
    
    if not current_user and token:
        current_user = await get_user_from_token(token)
    
    if not current_user:
        raise HTTPException(status_code=401, detail="Token inválido ou ausente")
    
    require_admin(current_user)
    
    funcionario = await db.funcionarios.find_one({"id": funcionario_id, "user_id": current_user["id"]})
    if not funcionario:
        raise HTTPException(status_code=404, detail="Funcionário não encontrado")
    
    query = {"funcionario_id": funcionario_id, "user_id": current_user["id"]}
    registos = await db.registos.find(query).to_list(10000)
    
    obras = await db.obras.find({"user_id": current_user["id"]}).to_list(1000)
    obras_dict = {o["id"]: o["name"] for o in obras}
    
    output = io.BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()
    
    # Title
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=16, spaceAfter=20)
    elements.append(Paragraph(f"Mapa de Horas - {funcionario['name']}", title_style))
    
    periodo = f"{mes}/{ano}" if mes and ano else (str(ano) if ano else "Todos os períodos")
    elements.append(Paragraph(f"Período: {periodo}", styles['Normal']))
    elements.append(Spacer(1, 20))
    
    # Table data
    data = [["Data", "Obra/Falta", "Horas"]]
    total_horas = 0
    
    for r in registos:
        data_parts = r["data"].split("-")
        r_ano = int(data_parts[0])
        r_mes = int(data_parts[1])
        
        if ano and r_ano != ano:
            continue
        if mes and r_mes != mes:
            continue
        
        for e in r["entradas"]:
            tipo = "Falta" if e["tipo"] == "falta" else obras_dict.get(e.get("obra_id"), "Obra Desconhecida")
            data.append([r["data"], tipo, str(e["horas"])])
            total_horas += e["horas"]
    
    data.append(["Total", "", str(total_horas)])
    
    table = Table(data, colWidths=[4*cm, 8*cm, 3*cm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    elements.append(table)
    
    doc.build(elements)
    output.seek(0)
    
    filename = f"mapa_{funcionario['name'].replace(' ', '_')}_{periodo.replace('/', '-')}.pdf"
    return StreamingResponse(
        output,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@api_router.get("/export/obra/{obra_id}/excel")
async def export_obra_excel(
    request: Request,
    obra_id: str,
    mes: Optional[int] = None,
    ano: Optional[int] = None,
    token: Optional[str] = None,
):
    # Get token from header or query parameter
    current_user = None
    
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        header_token = auth_header.split(" ")[1]
        current_user = await get_user_from_token(header_token)
    
    if not current_user and token:
        current_user = await get_user_from_token(token)
    
    if not current_user:
        raise HTTPException(status_code=401, detail="Token inválido ou ausente")
    
    require_admin(current_user)
    
    obra = await db.obras.find_one({"id": obra_id, "user_id": current_user["id"]})
    if not obra:
        raise HTTPException(status_code=404, detail="Obra não encontrada")
    
    registos = await db.registos.find({"user_id": current_user["id"]}).to_list(10000)
    
    funcionarios = await db.funcionarios.find({"user_id": current_user["id"]}).to_list(1000)
    funcionarios_dict = {f["id"]: f["name"] for f in funcionarios}
    
    wb = Workbook()
    ws = wb.active
    ws.title = "Mapa de Horas"
    
    periodo = f"{mes}/{ano}" if mes and ano else (str(ano) if ano else "Todos os períodos")
    ws.append([f"Mapa de Horas - Obra: {obra['name']}"])
    ws.append([f"Período: {periodo}"])
    ws.append([])
    ws.append(["Data", "Funcionário", "Horas"])
    
    total_horas = 0
    for r in registos:
        data_parts = r["data"].split("-")
        r_ano = int(data_parts[0])
        r_mes = int(data_parts[1])
        
        if ano and r_ano != ano:
            continue
        if mes and r_mes != mes:
            continue
        
        for e in r["entradas"]:
            if e["tipo"] == "obra" and e.get("obra_id") == obra_id:
                funcionario_name = funcionarios_dict.get(r["funcionario_id"], "Funcionário Desconhecido")
                ws.append([r["data"], funcionario_name, e["horas"]])
                total_horas += e["horas"]
    
    ws.append([])
    ws.append(["Total", "", total_horas])
    
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    
    filename = f"mapa_obra_{obra['name'].replace(' ', '_')}_{periodo.replace('/', '-')}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@api_router.get("/export/obra/{obra_id}/pdf")
async def export_obra_pdf(
    request: Request,
    obra_id: str,
    mes: Optional[int] = None,
    ano: Optional[int] = None,
    token: Optional[str] = None,
):
    # Get token from header or query parameter
    current_user = None
    
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        header_token = auth_header.split(" ")[1]
        current_user = await get_user_from_token(header_token)
    
    if not current_user and token:
        current_user = await get_user_from_token(token)
    
    if not current_user:
        raise HTTPException(status_code=401, detail="Token inválido ou ausente")
    
    require_admin(current_user)
    
    obra = await db.obras.find_one({"id": obra_id, "user_id": current_user["id"]})
    if not obra:
        raise HTTPException(status_code=404, detail="Obra não encontrada")
    
    registos = await db.registos.find({"user_id": current_user["id"]}).to_list(10000)
    
    funcionarios = await db.funcionarios.find({"user_id": current_user["id"]}).to_list(1000)
    funcionarios_dict = {f["id"]: f["name"] for f in funcionarios}
    
    output = io.BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=16, spaceAfter=20)
    elements.append(Paragraph(f"Mapa de Horas - Obra: {obra['name']}", title_style))
    
    periodo = f"{mes}/{ano}" if mes and ano else (str(ano) if ano else "Todos os períodos")
    elements.append(Paragraph(f"Período: {periodo}", styles['Normal']))
    elements.append(Spacer(1, 20))
    
    data = [["Data", "Funcionário", "Horas"]]
    total_horas = 0
    
    for r in registos:
        data_parts = r["data"].split("-")
        r_ano = int(data_parts[0])
        r_mes = int(data_parts[1])
        
        if ano and r_ano != ano:
            continue
        if mes and r_mes != mes:
            continue
        
        for e in r["entradas"]:
            if e["tipo"] == "obra" and e.get("obra_id") == obra_id:
                funcionario_name = funcionarios_dict.get(r["funcionario_id"], "Funcionário Desconhecido")
                data.append([r["data"], funcionario_name, str(e["horas"])])
                total_horas += e["horas"]
    
    data.append(["Total", "", str(total_horas)])
    
    table = Table(data, colWidths=[4*cm, 8*cm, 3*cm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    elements.append(table)
    
    doc.build(elements)
    output.seek(0)
    
    filename = f"mapa_obra_{obra['name'].replace(' ', '_')}_{periodo.replace('/', '-')}.pdf"
    return StreamingResponse(
        output,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

# ==================== HEALTH CHECK ====================

@api_router.get("/")
async def root():
    return {"message": "Gestão de Obras API", "status": "running"}

@api_router.get("/health")
async def health():
    return {"status": "healthy"}

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
