"""Server-side, per-session access to the unfinished budget workspace."""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

import streamlit as st

LOCAL_CONFIG = Path('.streamlit/owner_access.json')
ITERATIONS = 600_000
SESSION_SECONDS = 8 * 60 * 60
_lock = threading.Lock()
_failures = {}


def hash_password(password):
    if len(password) < 12:
        raise ValueError('비밀번호를 12자 이상 입력하세요.')
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    return f'pbkdf2_sha256${ITERATIONS}${salt}${digest}'


def valid_hash(encoded):
    try:
        algorithm, iterations, salt, digest = encoded.split('$')
        return (algorithm == 'pbkdf2_sha256' and 600_000 <= int(iterations) <= 2_000_000
                and len(bytes.fromhex(salt)) == 16 and len(bytes.fromhex(digest)) == 32)
    except (ValueError, TypeError, AttributeError):
        return False


def verify_password(password, encoded):
    if not valid_hash(encoded) or not isinstance(password, str) or len(password) > 1024:
        return False
    _, iterations, salt, expected = encoded.split('$')
    actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(iterations)).hex()
    return hmac.compare_digest(actual, expected)


def configured_hash():
    value = os.environ.get('BUDGET_OWNER_PASSWORD_HASH')
    if value is not None:
        return value if valid_hash(value) else ''
    try:
        settings = st.secrets.get('budget_owner', {})
        if not hasattr(settings, 'get'):
            return ''
        if 'password_hash' in settings:
            value = settings['password_hash']
            return value if valid_hash(value) else ''
    except (FileNotFoundError, KeyError):
        pass
    except Exception:
        return ''
    try:
        value = json.loads(LOCAL_CONFIG.read_text(encoding='utf-8'))['password_hash']
        return value if valid_hash(value) else ''
    except (OSError, ValueError, KeyError, TypeError):
        return ''


def is_owner():
    encoded = configured_hash()
    grant = st.session_state.get('_budget_owner_grant')
    return bool(encoded and isinstance(grant, dict) and
                hmac.compare_digest(str(grant.get('key', '')), hashlib.sha256(encoded.encode()).hexdigest()) and
                isinstance(grant.get('expires'), (int, float)) and grant['expires'] > time.time())


def require_owner():
    if not is_owner():
        st.error('접근 권한이 없습니다.')
        st.stop()


def _submit():
    password = st.session_state.pop('_budget_owner_password', '')
    encoded = configured_hash()
    key = hashlib.sha256(encoded.encode()).hexdigest()
    now = time.time()
    # Shared across sessions in this process: opening a fresh tab cannot reset
    # the attempt limit. No passwords or client identifiers are retained.
    with _lock:
        attempts = [t for t in _failures.get(key, []) if now - t < 300]
        if len(attempts) >= 10:
            st.session_state['_budget_owner_error'] = '잠시 후 다시 시도해 주세요.'
            return
        if encoded and verify_password(password, encoded):
            _failures.pop(key, None)
            st.session_state['_budget_owner_grant'] = {'key': key, 'expires': now + SESSION_SECONDS}
            st.session_state.pop('_budget_owner_error', None)
            st.session_state['current_page'] = '🧭 편성예산·부기관리'
        else:
            _failures[key] = attempts + [now]
            st.session_state.pop('_budget_owner_grant', None)
            st.session_state['_budget_owner_error'] = '인증 정보를 확인해 주세요.'


def render_owner_login():
    st.subheader('관리자 인증')
    if not configured_hash():
        st.info('관리자 접근이 설정되지 않았습니다.')
        return
    with st.form('_budget_owner_login'):
        st.text_input('관리자 비밀번호', type='password', key='_budget_owner_password', max_chars=1024)
        st.form_submit_button('인증 후 열기', on_click=_submit, type='primary')
    if st.session_state.get('_budget_owner_error'):
        st.error(st.session_state['_budget_owner_error'])


def logout():
    for key in list(st.session_state):
        if key.startswith(('_budget_owner_', 'v44_', 'v459_')):
            st.session_state.pop(key, None)
    st.session_state['current_page'] = 'HOME'
    if 'owner' in st.query_params:
        del st.query_params['owner']
