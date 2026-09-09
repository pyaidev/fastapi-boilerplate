import base64
import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parent.parent
template = (root / ".env.example").read_text()
values = {
    "CHANGE_ME_SECRET": secrets.token_urlsafe(64),
    "CHANGE_ME_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    "CHANGE_ME_DB_PASSWORD": secrets.token_urlsafe(32),
    "CHANGE_ME_GRAFANA_PASSWORD": secrets.token_urlsafe(32),
}
for placeholder, value in values.items():
    template = template.replace(placeholder, value)
try:
    descriptor = os.open(root / ".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print(".env already exists; preserved.")
else:
    with os.fdopen(descriptor, "w") as stream:
        stream.write(template)
    print("Created private .env with random local credentials.")
