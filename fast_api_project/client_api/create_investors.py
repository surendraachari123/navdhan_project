"""
Example script: create a single investor.
Now requires login first, since POST /investors is a protected endpoint.
"""
import getpass

from api_client import login, create_investor, APIError, current_role

if not current_role():
    username = input("Username: ").strip()
    password = getpass.getpass("Password: ")
    login(username, password)

investor = {
    "investor_id": "INV011111114",
    "name": "Priya_Sharma",
    "email": "priya.sharma200@example.com",
    "mobile": "+91-9876543210",
    "kyc_status": "Verified",
    "pan": "ABCDE1234F",
}

try:
    result = create_investor(**investor)
    print("Success:", result)
except APIError as e:
    print(f"Request failed [{e.status_code}]: {e.detail}")
