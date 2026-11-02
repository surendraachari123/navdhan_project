"""
One-off CLI utility to create a login (APP_USERS) row with a properly
bcrypt-hashed password. Run manually, from the server_api folder:

    python create_user.py

Never insert a plaintext password directly into APP_USERS - always go
through this script (or the hash_password() helper in auth.py) so the
database only ever stores a bcrypt hash, never the real password.
"""
import getpass

import oracledb

from config import DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_SERVICE, VALID_ROLES
from auth import hash_password


def main():
    username = input("New username: ").strip()
    email = input("Email: ").strip()
    password = getpass.getpass("New password: ")
    confirm = getpass.getpass("Confirm password: ")

    if password != confirm:
        print("Passwords do not match. Aborting.")
        return

    role = input(f"Role {VALID_ROLES} [user]: ").strip() or "user"
    if role not in VALID_ROLES:
        print(f"Invalid role. Must be one of {VALID_ROLES}. Aborting.")
        return

    password_hash = hash_password(password)

    dsn = oracledb.makedsn(DB_HOST, DB_PORT, service_name=DB_SERVICE)
    with oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=dsn) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO app_users (username, password_hash, email, role, is_active)
                VALUES (:1, :2, :3, :4, 1)
                """,
                (username, password_hash, email, role),
            )
        conn.commit()

    print(f"User '{username}' created successfully with role '{role}'.")


if __name__ == "__main__":
    main()
