import hashlib

import models
from db import SessionLocal


def main() -> None:
    db = SessionLocal()
    try:
        rows = db.query(models.Credential).all()
        updated = 0
        for row in rows:
            current = str(row.password or "")
            if not current or current.startswith("sha256$"):
                continue
            row.password = "sha256$" + hashlib.sha256(current.encode("utf-8")).hexdigest()
            updated += 1
        db.commit()
        print(updated)
    finally:
        db.close()


if __name__ == "__main__":
    main()
