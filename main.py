# main.py
from typing import Optional
from datetime import datetime
from fastapi import HTTPException
from fastapi import FastAPI, Depends
from pydantic import BaseModel, Field
from typing import List, Dict
from sqlalchemy import create_engine, Column, Integer, Float, String
from sqlalchemy.orm import sessionmaker, declarative_base, Session

# --------------------- DB setup ---------------------
SQLITE_URL = "sqlite:///./expense.db"   # file will appear in project root
engine = create_engine(SQLITE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

class TransactionORM(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    amount = Column(Float, nullable=False)
    category = Column(String, nullable=False)
    date = Column(String, nullable=False)  # keep simple for now (YYYY-MM-DD)

Base.metadata.create_all(bind=engine)  # create table if it doesn't exist

def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --------------------- API setup ---------------------
app = FastAPI(title="Expense Tracker API (SQLite)")

class TransactionIn(BaseModel):
    amount: float = Field(gt=0)
    category: str
    date: str                   # "YYYY-MM-DD"

class TransactionOut(TransactionIn):
    id: int

@app.get("/")
def root():
    return {"message": "Hello! API is live"}

# create (one)
@app.post("/transactions", response_model=TransactionOut)
def add_transaction(txn: TransactionIn, db: Session = Depends(get_db)):
    row = TransactionORM(amount=txn.amount, category=txn.category, date=txn.date)
    db.add(row)
    db.commit()
    db.refresh(row)
    return TransactionOut(id=row.id, amount=row.amount, category=row.category, date=row.date)

# read all
@app.get("/transactions", response_model=List[TransactionOut])
def list_transactions(db: Session = Depends(get_db)):
    rows = db.query(TransactionORM).order_by(TransactionORM.id.desc()).all()
    return [TransactionOut(id=r.id, amount=r.amount, category=r.category, date=r.date) for r in rows]

# simple summary by category
@app.get("/summary")
def summary(db: Session = Depends(get_db)) -> Dict[str, float]:
    totals: Dict[str, float] = {}
    for r in db.query(TransactionORM).all():
        totals[r.category] = totals.get(r.category, 0.0) + r.amount
    return totals

# bulk insert (add many at once)
@app.post("/transactions/bulk")
def add_transactions_bulk(txns: List[TransactionIn], db: Session = Depends(get_db)):
    rows = [TransactionORM(amount=t.amount, category=t.category, date=t.date) for t in txns]
    db.add_all(rows)
    db.commit()
    return {"ok": True, "added": len(rows)}

class TransactionUpdate(BaseModel):
    amount: Optional[float] = Field(default=None, gt=0)
    category: Optional[str] = None
    date: Optional[str] = None

@app.get("/transactions", response_model=List[TransactionOut])
def list_transactions(
    category: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    db: Session = Depends(get_db)
):
    query = db.query(TransactionORM)

    if category:
        query = query.filter(TransactionORM.category == category)

    if from_date:
        query = query.filter(TransactionORM.date >= from_date)

    if to_date:
        query = query.filter(TransactionORM.date <= to_date)

    rows = query.order_by(TransactionORM.id.desc()).all()
    return [
        TransactionOut(id=r.id, amount=r.amount, category=r.category, date=r.date)
        for r in rows
    ]

@app.put("/transactions/{txn_id}", response_model=TransactionOut)
def replace_transaction(txn_id: int, data: TransactionIn, db: Session = Depends(get_db)):
    row = db.query(TransactionORM).get(txn_id)
    if not row:
        raise HTTPException(status_code=404, detail="Transaction not found")
    row.amount = data.amount
    row.category = data.category
    row.date = data.date
    db.commit()
    db.refresh(row)
    return TransactionOut(id=row.id, amount=row.amount, category=row.category, date=row.date)

@app.patch("/transactions/{txn_id}", response_model=TransactionOut)
def update_transaction(txn_id: int, data: TransactionUpdate, db: Session = Depends(get_db)):
    row = db.query(TransactionORM).get(txn_id)
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
def delete_transaction(txn_id: int, db: Session = Depends(get_db)):
    row = db.query(TransactionORM).get(txn_id)
    if not row:
        raise HTTPException(status_code=404, detail="Transaction not found")
    db.delete(row)
    db.commit()
    return {"ok": True, "deleted_id": txn_id}


