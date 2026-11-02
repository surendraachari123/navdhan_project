"""
Pydantic request/response models.

FastAPI uses these to automatically validate incoming JSON before your
route code ever runs. If a request doesn't match the shape/rules defined
here, FastAPI raises a RequestValidationError automatically - which
main.py catches and turns into a clean 422 response - so the route
handlers never have to manually check "is this a valid email?" etc.
"""
import re
from datetime import date
from typing import Optional

from pydantic import BaseModel, EmailStr, field_validator, Field

# Indian PAN format: 5 letters, 4 digits, 1 letter (e.g. ABCDE1234F).
PAN_REGEX = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")

# Loosely validates phone numbers: optional leading +, 7-15 digits/dashes.
# Intentionally permissive since real-world phone formats vary a lot.
MOBILE_REGEX = re.compile(r"^\+?[0-9\-]{7,15}$")


class InvestorBase(BaseModel):
    """Fields shared by investor creation. investor_id is added separately
    in Investor below because it's the primary key and treated differently
    on updates vs creates."""

    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr  # pydantic validates this is a real email format automatically
    mobile: str
    kyc_status: str
    pan: str

    @field_validator("pan")
    @classmethod
    def validate_pan(cls, v: str) -> str:
        # Normalize first (strip whitespace, force uppercase) so
        # "abcde1234f" and "ABCDE1234F " both pass and get stored consistently.
        v = v.strip().upper()
        if not PAN_REGEX.match(v):
            raise ValueError("pan must be in the format ABCDE1234F")
        return v

    @field_validator("mobile")
    @classmethod
    def validate_mobile(cls, v: str) -> str:
        if not MOBILE_REGEX.match(v.strip()):
            raise ValueError("mobile must be a valid phone number")
        return v.strip()

    @field_validator("kyc_status")
    @classmethod
    def validate_kyc_status(cls, v: str) -> str:
        # Restricts kyc_status to a fixed set of valid values instead of
        # accepting any random string, which would otherwise silently
        # corrupt data (typos like "Verfied" would previously be accepted).
        allowed = {"Verified", "Pending", "Rejected"}
        if v not in allowed:
            raise ValueError(f"kyc_status must be one of {sorted(allowed)}")
        return v


class Investor(InvestorBase):
    """Full investor payload - used for POST (create) and PUT (full update),
    where the investor_id is required."""
    investor_id: str = Field(..., min_length=1, max_length=50)


class InvestorUpdate(BaseModel):
    """Used for PATCH (partial update) - every field is Optional so the
    caller only needs to send the fields they actually want to change.
    The validators still run, but only when a value is actually provided
    (each one returns early with `if v is None: return v`)."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    email: Optional[EmailStr] = None
    mobile: Optional[str] = None
    kyc_status: Optional[str] = None
    pan: Optional[str] = None

    @field_validator("pan")
    @classmethod
    def validate_pan(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v  # field wasn't sent - nothing to validate
        v = v.strip().upper()
        if not PAN_REGEX.match(v):
            raise ValueError("pan must be in the format ABCDE1234F")
        return v

    @field_validator("mobile")
    @classmethod
    def validate_mobile(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not MOBILE_REGEX.match(v.strip()):
            raise ValueError("mobile must be a valid phone number")
        return v.strip()

    @field_validator("kyc_status")
    @classmethod
    def validate_kyc_status(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        allowed = {"Verified", "Pending", "Rejected"}
        if v not in allowed:
            raise ValueError(f"kyc_status must be one of {sorted(allowed)}")
        return v


class Transaction(BaseModel):
    """A mutual fund transaction request payload.

    Note: transaction_id is deliberately NOT included here - the INSERT
    statement in main.py never writes it, which implies the database
    generates it itself (e.g. via a sequence or trigger). If that's not
    the case, this model needs transaction_id added back in.
    """
    folio_id: str = Field(..., min_length=1, max_length=50)
    investor_id: str = Field(..., min_length=1, max_length=50)
    scheme_id: str = Field(..., min_length=1, max_length=50)
    # gt=0 (greater than 0) rejects zero/negative units, nav, and amount,
    # which wouldn't make sense for a real transaction.
    units: float = Field(..., gt=0)
    nav: float = Field(..., gt=0)
    amount: float = Field(..., gt=0)
    txn_type: str
    txn_date: date  # pydantic validates and parses this into a real date object

    @field_validator("txn_type")
    @classmethod
    def validate_txn_type(cls, v: str) -> str:
        allowed = {"PURCHASE","SIP","REDEMPTION"}
        if v.upper() not in allowed:
            raise ValueError(f"txn_type must be one of {sorted(allowed)}")
        return v.upper()  # normalize so "buy"/"Buy"/"BUY" all store the same way


from pydantic import BaseModel

class InvestorRequest(BaseModel):
    investor_id: str


# ---------------------------------------------------------------------
# Auth / users / registration
# ---------------------------------------------------------------------
VALID_ROLES = {"admin", "manager", "user"}


class RegistrationRequestCreate(BaseModel):
    """Public 'sign up' form payload -> POST /register.
    This does NOT create a login. It only files a request that an admin
    reviews via the /admin/registration-requests endpoints."""
    username: str = Field(..., min_length=3, max_length=50)
    full_name: str = Field(..., min_length=1, max_length=150)
    email: EmailStr
    mobile: Optional[str] = None
    requested_role: str = Field(default="user")
    reason: Optional[str] = Field(default=None, max_length=500)

    @field_validator("requested_role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in VALID_ROLES:
            raise ValueError(f"requested_role must be one of {sorted(VALID_ROLES)}")
        return v

    @field_validator("mobile")
    @classmethod
    def validate_mobile(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return v
        if not MOBILE_REGEX.match(v.strip()):
            raise ValueError("mobile must be a valid phone number")
        return v.strip()


class RegistrationApprove(BaseModel):
    """Optional overrides an admin can apply when approving a request -
    e.g. bump the role up/down from what the applicant asked for."""
    role: Optional[str] = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in VALID_ROLES:
            raise ValueError(f"role must be one of {sorted(VALID_ROLES)}")
        return v


class RegistrationReject(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=500)


class ChangePasswordRequest(BaseModel):
    """Authenticated user changing their own password. Requires the
    current password so a stolen/left-open session token alone can't be
    used to lock the real owner out."""
    current_password: str
    new_password: str = Field(..., min_length=8, max_length=100)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8, max_length=100)


class UserRoleUpdate(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in VALID_ROLES:
            raise ValueError(f"role must be one of {sorted(VALID_ROLES)}")
        return v

# ---------------------------------------------------------------------
# NavDhan AI
# ---------------------------------------------------------------------
from typing import Literal, List


class AskRequest(BaseModel):
    """One-shot question for POST /ai/ask."""
    question: str = Field(..., min_length=3, max_length=500)

    @field_validator("question")
    @classmethod
    def strip_question(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("question is too short")
        return v


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=2000)
    # The SQL the assistant ran for this turn (sent back by the client so
    # follow-up questions like "and only for SIPs?" can build on it).
    sql: Optional[str] = Field(default=None, max_length=3000)


class ChatRequest(BaseModel):
    """Conversation so far for POST /ai/chat. The server is stateless: the
    client sends the whole history each time, ending with the new user message."""
    messages: List[ChatMessage] = Field(..., min_length=1, max_length=30)

    @field_validator("messages")
    @classmethod
    def last_must_be_user(cls, v):
        if v[-1].role != "user":
            raise ValueError("the last message must be from the user")
        return v
