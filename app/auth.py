import json
import jwt
from datetime import datetime, timezone, timedelta
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "scheduler" / "config.json"

def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

def verify_login(login_id: str, password: str) -> bool:
    config = load_config()
    return login_id == config["login_id"] and password == config["db_password"]

def create_token(remember: bool) -> str:
    config = load_config()
    exp = datetime.now(timezone.utc) + (timedelta(days=30) if remember else timedelta(hours=12))
    payload = {"sub": config["login_id"], "exp": exp}
    return jwt.encode(payload, config["jwt_secret"], algorithm="HS256")

def verify_token(token: str) -> bool:
    try:
        config = load_config()
        jwt.decode(token, config["jwt_secret"], algorithms=["HS256"])
        return True
    except jwt.ExpiredSignatureError:
        return False
    except jwt.InvalidTokenError:
        return False