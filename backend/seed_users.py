"""
Create initial users from environment variables.

Usage:
    SEED_USERS="alice:password1,bob:password2" python seed_users.py
"""
import os
import sys

# Ensure we're in the backend dir so imports work
sys.path.insert(0, os.path.dirname(__file__))

from database import SessionLocal, Base, engine
from models.models import User
from services.auth import hash_password

Base.metadata.create_all(bind=engine)

seed = os.environ.get("SEED_USERS", "")
if not seed:
    print("Set SEED_USERS=username:password,... to create users")
    sys.exit(0)

db = SessionLocal()
for pair in seed.split(","):
    pair = pair.strip()
    if ":" not in pair:
        print(f"Skipping malformed entry: {pair!r}")
        continue
    username, password = pair.split(":", 1)
    if db.query(User).filter(User.username == username).first():
        print(f"User {username!r} already exists — skipping")
        continue
    db.add(User(username=username, hashed_password=hash_password(password)))
    print(f"Created user {username!r}")

db.commit()
db.close()
print("Done.")
