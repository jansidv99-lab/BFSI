from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from api.rate_limit import login_limiter
from auth.tokens import create_access_token, create_refresh_token
from auth.users import authenticate_user, create_user

router = APIRouter(tags=["auth"])


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str


class RegisterResponse(BaseModel):
    user_id: int
    username: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest):
    if len(body.password) < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 8 characters")
    try:
        user_id = create_user(body.username, body.email, body.password)
    except ValueError as e:
        msg = str(e)
        if msg == "username_taken":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already taken")
        if msg == "email_taken":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Registration failed")
    return RegisterResponse(user_id=user_id, username=body.username)


@router.post("/login", response_model=TokenResponse)
def login(request: Request, form: OAuth2PasswordRequestForm = Depends()):
    login_limiter.check(f"login:{request.client.host}")
    user = authenticate_user(form.username, form.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(
        access_token=create_access_token(user["id"], user["username"]),
        refresh_token=create_refresh_token(user["id"]),
    )
