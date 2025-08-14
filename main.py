# main.py
from typing import Optional, List, Dict
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel, Field, EmailStr
from sqlalchemy import DateTime, create_engine, Column, Integer, Float, String
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from dotenv import load_dotenv
import os, time, jwt
from datetime import datetime, timedelta, timezone
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext

# --------------------- DB setup ---------------------
load_dotenv()  # reads .env
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set. Add it to .env")

# psycopg3 driver + SSL via ?sslmode=require in the URL
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

class TransactionORM(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    amount = Column(Float, nullable=False)
    category = Column(String, nullable=False)
    date = Column(String, nullable=False)  # keep "YYYY-MM-DD" as string for now

class UserORM(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

# create table(s) on Postgres if missing
Base.metadata.create_all(bind=engine)

def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --------------------- API setup ---------------------
app = FastAPI(title="Expense Tracker API (Supabase Postgres)")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
JWT_ALG = os.getenv("JWT_ALG", "HS256")
JWT_EXPIRES_MINUTES = int(os.getenv("JWT_EXPIRES_MINUTES", "60"))

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, password_hash: str) -> bool:
    return pwd_context.verify(plain_password, password_hash)

def create_access_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRES_MINUTES)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> UserORM:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG], leeway=120)
    except Exception as e:
        print("JWT decode error:", type(e).__name__, str(e))
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Invalid token")

    # This part may fail if DB is unreachable
    user = db.get(UserORM, int(user_id_str))
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

class TransactionIn(BaseModel):
    amount: float = Field(gt=0)
    category: str
    date: str  # "YYYY-MM-DD"

class TransactionOut(TransactionIn):
    id: int

class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class UserOut(BaseModel):
    id: int
    email: EmailStr

@app.get("/")
def root():
    return {"message": "Hello! API is Live"}

@app.post("/transactions", response_model=TransactionOut)
def add_transaction(
    txn: TransactionIn,
    db: Session = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    row = TransactionORM(amount=txn.amount, category=txn.category, date=txn.date)
    db.add(row)
    db.commit()
    db.refresh(row)
    return TransactionOut(id=row.id, amount=row.amount, category=row.category, date=row.date)

@app.get("/transactions", response_model=List[TransactionOut])
def list_transactions(
    category: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    query = db.query(TransactionORM)
    if category:
        query = query.filter(TransactionORM.category == category)
    if from_date:
        query = query.filter(TransactionORM.date >= from_date)
    if to_date:
        query = query.filter(TransactionORM.date <= to_date)
    rows = query.order_by(TransactionORM.id.desc()).all()
    return [TransactionOut(id=r.id, amount=r.amount, category=r.category, date=r.date) for r in rows]

@app.get("/summary")
def summary(
    db: Session = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
) -> Dict[str, float]:
    totals: Dict[str, float] = {}
    for r in db.query(TransactionORM).all():
        totals[r.category] = totals.get(r.category, 0.0) + r.amount
    return totals

@app.post("/transactions/bulk")
def add_transactions_bulk(
    txns: List[TransactionIn],
    db: Session = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    rows = [TransactionORM(amount=t.amount, category=t.category, date=t.date) for t in txns]
    db.add_all(rows)
    db.commit()
    return {"ok": True, "added": len(rows)}

class TransactionUpdate(BaseModel):
    amount: Optional[float] = Field(default=None, gt=0)
    category: Optional[str] = None
    date: Optional[str] = None

@app.get("/transactions/{txn_id}", response_model=TransactionOut)
def get_transaction(
    txn_id: int,
    db: Session = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    row = db.get(TransactionORM, txn_id)
    if not row:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return TransactionOut(id=row.id, amount=row.amount, category=row.category, date=row.date)

@app.put("/transactions/{txn_id}", response_model=TransactionOut)
def replace_transaction(
    txn_id: int,
    data: TransactionIn,
    db: Session = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    row = db.get(TransactionORM, txn_id)
    if not row:
        raise HTTPException(status_code=404, detail="Transaction not found")
    row.amount, row.category, row.date = data.amount, data.category, data.date
    db.commit()
    db.refresh(row)
    return TransactionOut(id=row.id, amount=row.amount, category=row.category, date=row.date)

@app.patch("/transactions/{txn_id}", response_model=TransactionOut)
def update_transaction(
    txn_id: int,
    data: TransactionUpdate,
    db: Session = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    row = db.get(TransactionORM, txn_id)
    if not row:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if data.amount is not None:
        row.amount = data.amount
    if data.category is not None:
        row.category = data.category
    if data.date is not None:
        row.date = data.date
    db.commit()
    db.refresh(row)
    return TransactionOut(id=row.id, amount=row.amount, category=row.category, date=row.date)

@app.delete("/transactions/{txn_id}")
def delete_transaction(
    txn_id: int,
    db: Session = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    row = db.get(TransactionORM, txn_id)
    if not row:
        raise HTTPException(status_code=404, detail="Transaction not found")
    db.delete(row)
    db.commit()
    return {"ok": True, "deleted_id": txn_id}

@app.post("/auth/signup", response_model=UserOut)
def signup(data: SignupIn, db: Session = Depends(get_db)):
    existing = db.query(UserORM).filter(UserORM.email == data.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    user = UserORM(email=data.email, password_hash=hash_password(data.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, email=user.email)


@app.post("/auth/login")
def login(data: LoginIn, db: Session = Depends(get_db)):
    user = db.query(UserORM).filter(UserORM.email == data.email).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(user.id)
    return {"access_token": token, "token_type": "bearer", "expires_in": JWT_EXPIRES_MINUTES * 60}

@app.get("/me", response_model=UserOut)
def me(current_user: UserORM = Depends(get_current_user)):
    return UserOut(id=current_user.id, email=current_user.email)
