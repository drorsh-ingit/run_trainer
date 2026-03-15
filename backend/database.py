from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from config import settings

db_url = settings.database_url.replace("postgres://", "postgresql://", 1)
is_sqlite = "sqlite" in db_url
engine = create_engine(
    db_url,
    connect_args={"check_same_thread": False} if is_sqlite else {"sslmode": "require"},
    pool_pre_ping=True,  # test connections before use — fixes Neon idle disconnects
    pool_recycle=300,    # recycle connections every 5 minutes
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
