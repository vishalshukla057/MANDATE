from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings

# Engine configuration
connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    settings.DATABASE_URL,
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
        policy, audit, webhook, job, user
    )
    Base.metadata.create_all(bind=engine)
    if settings.DATABASE_URL.startswith("sqlite"):
        with engine.begin() as conn:
            idxs = conn.execute(text("PRAGMA index_list('audit_log')")).fetchall()
            for row in idxs:
                idx_name, is_unique = row[1], row[2]
                if idx_name == "ix_audit_log_seq" and is_unique:
                    conn.execute(text("DROP INDEX IF EXISTS ix_audit_log_seq"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_log_seq ON audit_log (seq)"))
                    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_audit_tenant_seq ON audit_log (tenant_id, seq)"))

