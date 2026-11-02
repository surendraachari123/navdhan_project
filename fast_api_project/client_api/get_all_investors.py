"""
Example script: list every investor.
Now requires login first, since GET /investors is a protected endpoint.
"""
import getpass

from api_client import login, get_all_investors, APIError, current_role

if not current_role():
    username = input("Username: ").strip()
    password = getpass.getpass("Password: ")
    login(username, password)

try:
    print(get_all_investors())
except APIError as e:
    print(f"Request failed [{e.status_code}]: {e.detail}")
