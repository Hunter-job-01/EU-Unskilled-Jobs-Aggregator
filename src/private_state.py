"""Authenticated encryption for candidate-specific notification state."""
import json
from cryptography.fernet import Fernet, InvalidToken


def cipher(key):
    try:
        return Fernet(key.encode('ascii'))
    except (ValueError, TypeError, AttributeError, UnicodeError):
        raise ValueError('Private notification state key is missing or invalid') from None


def seal(state, key):
    payload = json.dumps(state, sort_keys=True, separators=(',', ':')).encode()
    return {'encrypted_state_v1': cipher(key).encrypt(payload).decode('ascii')}


def unseal(payload, key):
    if not isinstance(payload, dict) or 'encrypted_state_v1' not in payload:
        # Explicit migration preserves accepted jobs and independent day gates.
        if not isinstance(payload, dict):
            raise ValueError('Private notification state is invalid')
        return payload
    try:
        state = json.loads(cipher(key).decrypt(payload['encrypted_state_v1'].encode()))
        if not isinstance(state, dict):
            raise ValueError
        return state
    except (InvalidToken, ValueError, TypeError, AttributeError):
        raise ValueError('Private notification state cannot be decrypted') from None
