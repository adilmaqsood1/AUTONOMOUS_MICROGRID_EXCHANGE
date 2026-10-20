"""Key management infrastructure: abstract KeyManager, EncryptedLocalKeyManager,
and cryptographic wallet verification.

Never logs or leaks plaintext private keys.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
from abc import ABC, abstractmethod
from typing import Any

from cryptography.fernet import Fernet
from eth_account import Account
from eth_account.messages import encode_defunct


class KeyManager(ABC):
    """Abstract key management interface for development and enterprise custody systems."""

    @abstractmethod
    def create_key_pair(self) -> tuple[str, str]:
        """Generate a new EVM key pair.
        Returns:
            (checksum_address, encrypted_key_reference)
        """
        ...

    @abstractmethod
    def sign_transaction(self, encrypted_ref: str, transaction_dict: dict[str, Any]) -> Any:
        """Sign an EVM transaction dict using the encrypted key reference."""
        ...

    @abstractmethod
    def sign_message(self, encrypted_ref: str, message: str) -> Any:
        """Sign an arbitrary message string using the encrypted key reference."""
        ...

    @abstractmethod
    def get_address_from_ref(self, encrypted_ref: str) -> str:
        """Derive the public checksum address from an encrypted key reference."""
        ...


class EncryptedLocalKeyManager(KeyManager):
    """Local development key manager using Fernet (authenticated AES-128-CBC + HMAC-SHA256).

    Derives a 32-byte Fernet key from the configured master secret.
    Plaintext keys are only decrypted in-memory during signing operations.
    """

    def __init__(self, master_secret: str | None = None) -> None:
        secret = master_secret or os.getenv("MGX_MASTER_KEY_SECRET") or "mgx_microgrid_default_dev_master_key_secret_32b!"
        # Deterministically derive 32-byte urlsafe base64 key
        digest = hashlib.sha256(secret.encode("utf-8")).digest()
        self._fernet_key = base64.urlsafe_b64encode(digest)
        self._cipher = Fernet(self._fernet_key)

    def _encrypt(self, plaintext: str) -> str:
        return self._cipher.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def _decrypt(self, ciphertext: str) -> str:
        return self._cipher.decrypt(ciphertext.encode("utf-8")).decode("utf-8")

    def create_key_pair(self) -> tuple[str, str]:
        acct = Account.create()
        encrypted_ref = self._encrypt(acct.key.hex())
        return acct.address, encrypted_ref

    def get_address_from_ref(self, encrypted_ref: str) -> str:
        private_key = self._decrypt(encrypted_ref)
        acct = Account.from_key(private_key)
        return acct.address

    def sign_transaction(self, encrypted_ref: str, transaction_dict: dict[str, Any]) -> Any:
        private_key = self._decrypt(encrypted_ref)
        acct = Account.from_key(private_key)
        return acct.sign_transaction(transaction_dict)

    def sign_message(self, encrypted_ref: str, message: str) -> Any:
        private_key = self._decrypt(encrypted_ref)
        acct = Account.from_key(private_key)
        signable = encode_defunct(text=message)
        return acct.sign_message(signable)

    def import_private_key(self, private_key_hex: str) -> tuple[str, str]:
        """Helper to import an existing dev/deployer private key securely."""
        clean_key = private_key_hex if private_key_hex.startswith("0x") else f"0x{private_key_hex}"
        acct = Account.from_key(clean_key)
        encrypted_ref = self._encrypt(clean_key)
        return acct.address, encrypted_ref


class TestKeyManager(KeyManager):
    """In-memory key manager for fast hermetic unit testing without cipher overhead."""
    __test__ = False

    def __init__(self) -> None:
        self._keys: dict[str, str] = {}  # ref -> private_key

    def create_key_pair(self) -> tuple[str, str]:
        acct = Account.create()
        ref = f"test_ref_{secrets.token_hex(8)}"
        self._keys[ref] = acct.key.hex()
        return acct.address, ref

    def get_address_from_ref(self, encrypted_ref: str) -> str:
        pk = self._keys[encrypted_ref]
        return Account.from_key(pk).address

    def sign_transaction(self, encrypted_ref: str, transaction_dict: dict[str, Any]) -> Any:
        pk = self._keys[encrypted_ref]
        return Account.from_key(pk).sign_transaction(transaction_dict)

    def sign_message(self, encrypted_ref: str, message: str) -> Any:
        pk = self._keys[encrypted_ref]
        signable = encode_defunct(text=message)
        return Account.from_key(pk).sign_message(signable)


def generate_wallet_challenge_nonce() -> str:
    """Generate a cryptographically secure hex nonce for external wallet challenge."""
    return secrets.token_hex(32)


def verify_wallet_signature(address: str, message: str, signature_hex: str) -> bool:
    """Cryptographically verify that the given EVM address signed the message text."""
    try:
        signable = encode_defunct(text=message)
        recovered_address = Account.recover_message(signable, signature=signature_hex)
        return recovered_address.lower() == address.lower()
    except Exception:
        return False
