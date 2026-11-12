from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings

# Engine configuration
connect_args = {}
db_url = settings.effective_database_url
if db_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    db_url,
    connect_args=connect_args,
    echo=False,
    future=True
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app.models import (
        client, invoice, input_record, payment, reminder,
        offer, contractor, payout, dispute, agent,
        policy, audit, webhook, job
    )
    Base.metadata.create_all(bind=engine)

