"""Тесты для функций хеширования и проверки паролей."""

from app.core.auth import hash_password, verify_password


def test_hash_password_returns_string():
    """hash_password должна возвращать строку."""
    hashed = hash_password("test123")
    assert isinstance(hashed, str)
    assert len(hashed) > 0


def test_hash_password_different_hashes():
    """Один и тот же пароль должен давать разные хеши (из-за соли)."""
    hash1 = hash_password("test123")
    hash2 = hash_password("test123")
    assert hash1 != hash2


def test_verify_password_correct():
    """verify_password должна возвращать True для правильного пароля."""
    password = "mysecretpassword"
    hashed = hash_password(password)
    assert verify_password(password, hashed) is True


def test_verify_password_incorrect():
    """verify_password должна возвращать False для неправильного пароля."""
    password = "mysecretpassword"
    hashed = hash_password(password)
    assert verify_password("wrongpassword", hashed) is False


def test_hash_password_truncates_to_72_bytes():
    """Пароли длиннее 72 байт должны обрезаться."""
    long_password = "a" * 100  # 100 байт
    hashed = hash_password(long_password)

    # Проверяем, что обрезанный пароль (72 байта) проходит проверку
    truncated = long_password[:72]
    assert verify_password(truncated, hashed) is True

    # Но полный пароль тоже должен работать (т.к. обрезается внутри verify)
    assert verify_password(long_password, hashed) is True
