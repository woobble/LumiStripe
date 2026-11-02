use base64::Engine as _;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use rand::TryRng as _;
use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use subtle::ConstantTimeEq;
use thiserror::Error;

pub const SESSION_COOKIE: &str = "lumistripe_session";
pub const SESSION_MAX_AGE_SECONDS: u64 = 60 * 60 * 24 * 30;
const MAX_SESSIONS: usize = 1024;
const MAX_FAILURES: usize = 5;
const FAILURE_WINDOW: Duration = Duration::from_secs(60);

#[derive(Clone)]
pub struct PairingAuth(Arc<AuthState>);

struct AuthState {
    pairing_code: Option<String>,
    records: Mutex<AuthRecords>,
}

#[derive(Default)]
struct AuthRecords {
    sessions: HashMap<String, Instant>,
    failures: VecDeque<Instant>,
    next_cleanup: Option<Instant>,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum PairingError {
    #[error("The pairing code is incorrect.")]
    InvalidCode,
    #[error("Too many pairing attempts. Please try again later.")]
    RateLimited { retry_after: u64 },
}

impl PairingAuth {
    pub fn new(pairing_code: Option<String>) -> Result<Self, String> {
        if pairing_code.as_ref().is_some_and(|code| {
            code.len() != 4 || !code.is_ascii() || !code.bytes().all(|byte| byte.is_ascii_digit())
        }) {
            return Err("pairing code must contain exactly four ASCII digits".to_owned());
        }
        Ok(Self(Arc::new(AuthState {
            pairing_code,
            records: Mutex::new(AuthRecords::default()),
        })))
    }

    pub fn required(&self) -> bool {
        self.0.pairing_code.is_some()
    }

    pub fn authenticated(&self, session: Option<&str>) -> bool {
        if !self.required() {
            return true;
        }
        let Some(session) = session else {
            return false;
        };
        let now = Instant::now();
        let mut records = self.lock_records();
        self.cleanup(&mut records, now);
        records
            .sessions
            .get(session)
            .is_some_and(|expires_at| *expires_at > now)
    }

    pub fn pair(&self, code: &str) -> Result<String, PairingError> {
        self.pair_at(code, Instant::now())
    }

    fn pair_at(&self, code: &str, now: Instant) -> Result<String, PairingError> {
        if !self.required() {
            return Ok(random_session_token());
        }
        let mut records = self.lock_records();
        self.cleanup(&mut records, now);
        let cutoff = now.checked_sub(FAILURE_WINDOW).unwrap_or(now);
        while records
            .failures
            .front()
            .is_some_and(|failure| *failure <= cutoff)
        {
            records.failures.pop_front();
        }
        if records.failures.len() >= MAX_FAILURES {
            let retry_after = records
                .failures
                .front()
                .and_then(|failure| failure.checked_add(FAILURE_WINDOW))
                .map(|unlocks_at| {
                    let delay = unlocks_at.saturating_duration_since(now);
                    delay
                        .as_secs()
                        .saturating_add(u64::from(delay.subsec_nanos() > 0))
                        .max(1)
                })
                .unwrap_or(1);
            return Err(PairingError::RateLimited { retry_after });
        }
        let expected = self.0.pairing_code.as_deref().expect("pairing is enabled");
        if bool::from(code.as_bytes().ct_eq(expected.as_bytes())) {
            let token = random_session_token();
            if records.sessions.len() >= MAX_SESSIONS {
                if let Some(oldest) = records.sessions.keys().next().cloned() {
                    records.sessions.remove(&oldest);
                }
            }
            records.sessions.insert(
                token.clone(),
                now + Duration::from_secs(SESSION_MAX_AGE_SECONDS),
            );
            Ok(token)
        } else {
            records.failures.push_back(now);
            Err(PairingError::InvalidCode)
        }
    }

    pub fn revoke(&self, session: Option<&str>) {
        if let Some(session) = session {
            self.lock_records().sessions.remove(session);
        }
    }

    fn cleanup(&self, records: &mut AuthRecords, now: Instant) {
        if records.next_cleanup.is_some_and(|next| now < next) {
            return;
        }
        records.next_cleanup = Some(now + Duration::from_secs(60));
        records.sessions.retain(|_, expiry| *expiry > now);
    }

    fn lock_records(&self) -> std::sync::MutexGuard<'_, AuthRecords> {
        self.0
            .records
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }
}

fn random_session_token() -> String {
    let mut bytes = [0_u8; 32];
    rand::rngs::SysRng
        .try_fill_bytes(&mut bytes)
        .expect("operating system random source is available");
    URL_SAFE_NO_PAD.encode(bytes)
}

#[cfg(test)]
mod tests {
    use super::{PairingAuth, PairingError};
    use std::time::{Duration, Instant};

    #[test]
    fn opaque_sessions_authenticate_expire_and_revoke() {
        let auth = PairingAuth::new(Some("1234".to_owned())).unwrap();
        let now = Instant::now();
        let token = auth.pair_at("1234", now).unwrap();
        assert_ne!(token, "1234");
        assert!(auth.authenticated_at(Some(&token), now));
        auth.revoke(Some(&token));
        assert!(!auth.authenticated_at(Some(&token), now));

        let expired = auth.pair_at("1234", now).unwrap();
        assert!(!auth.authenticated_at(
            Some(&expired),
            now + Duration::from_secs(60 * 60 * 24 * 30 + 1)
        ));
    }

    #[test]
    fn failures_share_a_fixed_rate_limit_window() {
        let auth = PairingAuth::new(Some("1234".to_owned())).unwrap();
        let now = Instant::now();
        for offset in 0..5 {
            assert_eq!(
                auth.pair_at("0000", now + Duration::from_secs(offset)),
                Err(PairingError::InvalidCode)
            );
        }
        assert_eq!(
            auth.pair_at("1234", now + Duration::from_secs(10)),
            Err(PairingError::RateLimited { retry_after: 50 })
        );
    }

    #[test]
    fn disabled_pairing_never_requires_a_stored_session() {
        let auth = PairingAuth::new(None).unwrap();
        assert!(auth.authenticated(None));
        let token = auth.pair("1234").unwrap();
        assert!(auth.authenticated(Some(&token)));
    }
}

impl PairingAuth {
    #[cfg(test)]
    fn authenticated_at(&self, session: Option<&str>, now: Instant) -> bool {
        if !self.required() {
            return true;
        }
        let Some(session) = session else {
            return false;
        };
        let mut records = self.lock_records();
        self.cleanup(&mut records, now);
        records
            .sessions
            .get(session)
            .is_some_and(|expires_at| *expires_at > now)
    }
}
