import time
import jwt
from fastapi import HTTPException
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from dayzero.shared.config import settings

passwords = PasswordHasher()


def issue(subject, company_id, role='admin', **extra):
    return jwt.encode({'sub': subject, 'company_id': company_id, 'role': role,
                       'iat': time.time(), 'exp': time.time() + (900 if role == 'job' else 43200),
                       **extra}, settings().jwt_secret.get_secret_value(), algorithm='HS256')


def decode(token, role=None):
    try:
        claims = jwt.decode(token, settings().jwt_secret.get_secret_value(), algorithms=['HS256'])
        if role and claims['role'] != role:
            raise HTTPException(403, 'permission_denied')
        return claims
    except jwt.PyJWTError:
        raise HTTPException(401, 'invalid_token') from None


def verify(password, encoded):
    try:
        return passwords.verify(encoded, password)
    except VerificationError:
        return False
