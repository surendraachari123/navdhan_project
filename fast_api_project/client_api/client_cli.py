"""
client_cli.py

Interactive command-line program that lets you call every endpoint of
the investor/transactions API by typing values in, instead of hardcoding
them in a script. Uses api_client.py for the actual HTTP calls.

Run with:
    python client_cli.py
"""
import getpass

from api_client import (
    APIError,
    home,
    health_check,
    create_transaction,
    get_transactions_by_investor,
    create_investor,
    get_all_investors,
    get_investor,
    update_investor,
    patch_investor,
    delete_investor,
    get_investor_transactions,
    login,
    logout,
    current_role,
    must_change_password,
    register_request,
    change_password,
    forgot_password,
    reset_password,
    admin_list_registration_requests,
    admin_approve_registration,
    admin_reject_registration,
    admin_list_users,
    admin_update_user,
    admin_delete_user,
)


def prompt(label, required=True):
    """Keep asking until a non-empty value is given (if required)."""
    while True:
        value = input(f"{label}: ").strip()
        if value or not required:
            return value
        print("This field is required.")


def prompt_float(label):
    """Keep asking until a valid number is entered."""
    while True:
        raw = input(f"{label}: ").strip()
        try:
            return float(raw)
        except ValueError:
            print("Please enter a valid number.")


def prompt_optional(label):
    """Returns None if the user leaves it blank - used for PATCH,
    where blank means 'don't change this field'."""
    value = input(f"{label} (leave blank to skip): ").strip()
    return value if value else None


def print_result(data):
    print("\nResult:")
    print(data)


def print_error(e: APIError):
    print(f"\nRequest failed [{e.status_code}]: {e.detail}")


# ---------- Auth actions ----------

def action_login():
    username = prompt("Username")
    password = getpass.getpass("Password: ")
    try:
        result = login(username, password)
        print(f"\nLogged in as '{username}' (role: {result['role']}).")
        if result.get("force_password_change"):
            print("You must set a new password before doing anything else - use menu option 'Change password'.")
    except APIError as e:
        print_error(e)


def action_logout():
    logout()
    print("Logged out.")


def action_register():
    print("Submit a registration request - an admin will review it and email you a temporary password.")
    username = prompt("Desired username")
    full_name = prompt("Full name")
    email = prompt("Email")
    mobile = prompt_optional("Mobile")
    role = prompt_optional("Requested role (admin/manager/user, leave blank for 'user')") or "user"
    reason = prompt_optional("Reason / notes")
    try:
        print_result(register_request(username, full_name, email, mobile, role, reason))
    except APIError as e:
        print_error(e)


def action_change_password():
    current = getpass.getpass("Current password: ")
    new = getpass.getpass("New password: ")
    confirm = getpass.getpass("Confirm new password: ")
    if new != confirm:
        print("Passwords do not match.")
        return
    try:
        print_result(change_password(current, new))
    except APIError as e:
        print_error(e)


def action_forgot_password():
    email = prompt("Account email")
    try:
        print_result(forgot_password(email))
    except APIError as e:
        print_error(e)


def action_reset_password():
    token = prompt("Reset token (from the email)")
    new = getpass.getpass("New password: ")
    confirm = getpass.getpass("Confirm new password: ")
    if new != confirm:
        print("Passwords do not match.")
        return
    try:
        print_result(reset_password(token, new))
    except APIError as e:
        print_error(e)


# ---------- Admin actions ----------

def action_admin_list_requests():
    status = prompt_optional("Status filter (PENDING/APPROVED/REJECTED/ALL, blank = PENDING)") or "PENDING"
    try:
        print_result(admin_list_registration_requests(status))
    except APIError as e:
        print_error(e)


def action_admin_approve_request():
    request_id = prompt("Request ID to approve")
    role = prompt_optional("Override role (blank = use requested role)")
    try:
        print_result(admin_approve_registration(int(request_id), role))
    except APIError as e:
        print_error(e)


def action_admin_reject_request():
    request_id = prompt("Request ID to reject")
    reason = prompt_optional("Reason")
    try:
        print_result(admin_reject_registration(int(request_id), reason))
    except APIError as e:
        print_error(e)


def action_admin_list_users():
    try:
        print_result(admin_list_users())
    except APIError as e:
        print_error(e)


def action_admin_update_user():
    username = prompt("Username to update")
    role = prompt_optional("New role (admin/manager/user, blank = no change)")
    active_raw = prompt_optional("Active? (y/n, blank = no change)")
    is_active = None
    if active_raw is not None:
        is_active = active_raw.strip().lower() == "y"
    try:
        print_result(admin_update_user(username, role, is_active))
    except APIError as e:
        print_error(e)


def action_admin_delete_user():
    username = prompt("Username to delete")
    confirm = input(f"Are you sure you want to delete user '{username}'? (y/n): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return
    try:
        print_result(admin_delete_user(username))
    except APIError as e:
        print_error(e)


# ---------- Menu actions ----------

def action_home():
    try:
        print_result(home())
    except APIError as e:
        print_error(e)


def action_health():
    try:
        print_result(health_check())
    except APIError as e:
        print_error(e)


def action_create_transaction():
    folio_id = prompt("Folio ID")
    investor_id = prompt("Investor ID")
    scheme_id = prompt("Scheme ID")
    units = prompt_float("Units")
    nav = prompt_float("NAV")
    amount = prompt_float("Amount")
    txn_type = prompt("Transaction type (e.g. PURCHASE/SIP/REDEMPTION)").upper()
    txn_date = prompt("Transaction date (YYYY-MM-DD)")
    try:
        print_result(create_transaction(
            folio_id, investor_id, scheme_id, units, nav, amount, txn_type, txn_date
        ))
    except APIError as e:
        print_error(e)


def action_get_transactions_by_investor():
    investor_id = prompt("Investor ID")
    try:
        print_result(get_transactions_by_investor(investor_id))
    except APIError as e:
        print_error(e)


def action_create_investor():
    investor_id = prompt("Investor ID")
    name = prompt("Name")
    email = prompt("Email")
    mobile = prompt("Mobile")
    kyc_status = prompt("KYC status")
    pan = prompt("PAN")
    try:
        print_result(create_investor(investor_id, name, email, mobile, kyc_status, pan))
    except APIError as e:
        print_error(e)


def action_get_all_investors():
    try:
        print_result(get_all_investors())
    except APIError as e:
        print_error(e)


def action_get_investor():
    investor_id = prompt("Investor ID")
    try:
        print_result(get_investor(investor_id))
    except APIError as e:
        print_error(e)


def action_update_investor():
    investor_id = prompt("Investor ID to update")
    print("PUT replaces the whole record - all fields below are required.")
    name = prompt("Name")
    email = prompt("Email")
    mobile = prompt("Mobile")
    kyc_status = prompt("KYC status")
    pan = prompt("PAN")
    try:
        print_result(update_investor(investor_id, name, email, mobile, kyc_status, pan))
    except APIError as e:
        print_error(e)


def action_patch_investor():
    investor_id = prompt("Investor ID to patch")
    print("PATCH only changes fields you fill in - leave others blank to keep them as-is.")
    fields = {}
    for field_name, label in [
        ("name", "Name"),
        ("email", "Email"),
        ("mobile", "Mobile"),
        ("kyc_status", "KYC status"),
        ("pan", "PAN"),
    ]:
        value = prompt_optional(label)
        if value is not None:
            fields[field_name] = value

    if not fields:
        print("No fields entered - nothing to update.")
        return

    try:
        print_result(patch_investor(investor_id, **fields))
    except APIError as e:
        print_error(e)


def action_delete_investor():
    investor_id = prompt("Investor ID to delete")
    confirm = input(f"Are you sure you want to delete '{investor_id}'? (y/n): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return
    try:
        print_result(delete_investor(investor_id))
    except APIError as e:
        print_error(e)


def action_get_investor_transactions():
    investor_id = prompt("Investor ID")
    try:
        print_result(get_investor_transactions(investor_id))
    except APIError as e:
        print_error(e)


MENU = {
    "1": ("Home / liveness check", action_home),
    "2": ("Health check (DB reachable?)", action_health),
    "3": ("Create a transaction", action_create_transaction),
    "4": ("Get transactions by investor", action_get_transactions_by_investor),
    "5": ("Create an investor", action_create_investor),
    "6": ("Get all investors", action_get_all_investors),
    "7": ("Get one investor", action_get_investor),
    "8": ("Update investor (PUT - full replace)", action_update_investor),
    "9": ("Patch investor (partial update)", action_patch_investor),
    "10": ("Delete investor [admin only]", action_delete_investor),
    "11": ("Get investor + their transactions", action_get_investor_transactions),
    "12": ("Login", action_login),
    "13": ("Logout", action_logout),
    "14": ("Register (submit request)", action_register),
    "15": ("Change my password", action_change_password),
    "16": ("Forgot password (email me a reset link)", action_forgot_password),
    "17": ("Reset password (using emailed token)", action_reset_password),
    "18": ("[admin] List registration requests", action_admin_list_requests),
    "19": ("[admin] Approve registration request", action_admin_approve_request),
    "20": ("[admin] Reject registration request", action_admin_reject_request),
    "21": ("[admin] List users", action_admin_list_users),
    "22": ("[admin] Update user (role / active)", action_admin_update_user),
    "23": ("[admin] Delete user", action_admin_delete_user),
    "0": ("Exit", None),
}


def main():
    while True:
        role = current_role()
        status = f"logged in as role '{role}'" if role else "not logged in"
        print(f"\n=== Investor / Transactions API Client ({status}) ===")
        if must_change_password():
            print("!! You must change your password (option 15) before doing anything else.")
        for key, (label, _) in MENU.items():
            print(f"  {key}. {label}")

        choice = input("\nChoose an option: ").strip()

        if choice == "0":
            print("HAVE A NICE DAY!!!")
            break

        entry = MENU.get(choice)
        if entry is None:
            print("Invalid choice, try again.")
            continue

        _, action = entry
        action()


if __name__ == "__main__":
    main()
