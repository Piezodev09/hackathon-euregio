# Security and privacy

The platform is multi-tenant: customers (organisations) manage their stations, devices and teams
themselves. The measures follow OWASP ASVS (level 2 as the goal), the OWASP Top 10 and
NIST SP 800-63B for passwords.

## Implemented security measures

### Identity and sign-in
| Measure | Implementation | Code |
|---|---|---|
| Password hashing | scrypt (N=2¹⁵, r=8, p=1, 16-byte salt), automatic rehash when the cost factor increases | `security.py` |
| Password policy | ≥ 12 characters, ≤ 128, list of common passwords, must not contain e-mail/name, no forced special characters (NIST) | `password_problems` |
| No account enumeration | same response for unknown e-mail, wrong password and locked account; dummy hash equalises timing; sign-up/reset always answer "check your e-mail" | `routes/auth.py` |
| Brute-force protection | account lock after 5 failures with growing duration (5, 10, 20 … min, max. 24 h), notification e-mail; IP rate limit 10/min for auth, 5/h for endpoints that send e-mail | `_register_failure`, `Core` |
| E-mail verification | required before the first sign-in; one-time token, valid 48 h | `verify-email` |
| Two-factor (TOTP) | RFC 6238, ±1 time step, **replay protection** (last step stored), 10 recovery codes (hashed only), can be required per organisation, mandatory for platform admins | `totp_verify`, `mfa/*` |
| 2FA secret at rest | AES-256-GCM with associated data (user ID) – stealing the database alone is not enough | `SecretBox` |
| Password reset | one-time token, valid 1 h, ends all sessions, notification | `password/reset` |
| Security notifications | e-mails on lock-out, password change, 2FA on/off, use of a recovery code | |

### Sessions
| Measure | Implementation |
|---|---|
| Server-side sessions | 256-bit random token, only its SHA-256 hash in the database, revocable at once |
| Cookie | `__Host-` prefix (with HTTPS), `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/` |
| Lifetime | 60 min idle, 12 h absolute |
| Session fixation | a new session on every sign-in, the old one is discarded |
| Revocation | list of active sessions, sign out one/all others; password/role change and enabling 2FA end other sessions; suspending a tenant ends all |
| CSRF | synchronizer token (`X-CSRF-Token`) for every state-changing request **plus** origin/`Sec-Fetch-Site` check **plus** SameSite=Strict |

### Authorisation and tenant isolation
- Every query is filtered by `tenant_id`; resources of other tenants return **404** (no existence oracle).
- Unguessable IDs (`st_…`, `usr_…`, 96 bit) instead of sequential numbers.
- Roles: owner > admin > operator > viewer. Nobody grants higher rights than their own; the last
  owner can neither be removed nor demoted.
- Device tokens are valid for **one** station only (`station_mismatch` otherwise), revocable,
  stored only as hashes and shown exactly once.
- Agent pairing with a one-time code (~50 bit, 30 min, hashed, rate-limited); device tokens rotate
  automatically every 30 days with a short grace period; remote commands only from a fixed list;
  updates only with a matching SHA-256, safe extraction and automatic rollback (see [agent.md](agent.md)).
- Public display links: read only, reduced data (no AI/event details), rotatable, can be disabled;
  token in the URL fragment and a header – never in server logs.
- Plan limits (stations, spaces, users, features) are enforced on the server.
- Platform admins are separate from tenants and need 2FA.

### Transport, headers, input
| Measure | Implementation |
|---|---|
| TLS | HTTPS mandatory in `production` (start is refused otherwise); HSTS 2 years incl. subdomains |
| Content Security Policy | `default-src 'none'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'` – no inline JS/CSS |
| Other headers | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy` (camera, microphone, location … off), COOP/CORP `same-origin`, `Cache-Control: no-store` for API and pages, `X-Request-ID` |
| Host header | `TrustedHostMiddleware` with an allow list |
| Input | strict Pydantic schemas (`extra=forbid`), lengths, patterns, control characters rejected; this also rules out e-mail header injection |
| Request size | 64 KB, also for chunked transfers |
| Error output | no stack traces, no echoed input, no OpenAPI/docs pages |
| XSS in the frontend | no `innerHTML`; all content as text nodes |
| SQL injection | parameterised queries only |
| `security.txt` | `/.well-known/security.txt` for vulnerability reports |

### Operation
- Insecure configurations are rejected in `production` (HTTP, `*` hosts, weak scrypt factor, missing data key).
- Database file `0600`, `secure_delete`, systemd hardening (`NoNewPrivileges`, `ProtectSystem=strict`, …), nftables example.
- Audit log per tenant (sign-ins, failures, roles, tokens, plan, export, acknowledgements …) and platform-wide.
- Automatic deletion: measurements/events after the plan period (7/30/90 days), audit log after 365 days, expired sessions/tokens.

## Threat model (excerpt)

| Threat | Countermeasure |
|---|---|
| Credential stuffing / brute force | account lock, IP limit, 2FA, no enumeration |
| Session theft | HttpOnly cookie, short lifetimes, revocation, CSP against XSS |
| CSRF | token + origin + SameSite=Strict |
| Tenant A reads data of B (IDOR) | tenant filter in every query, 404, unguessable IDs, tests `test_tenancy.py` |
| Forged sensor data | device token per station, validation, audit |
| Stolen device token | only one station affected, revocable, automatic rotation, "last seen" + IP visible |
| Manipulated agent update | SHA-256 over the authenticated channel, safe extraction, version check, rollback (open: release signature) |
| Guessed pairing code | 50 bit, 30 min, single use, rate limit per IP |
| Database leak | passwords with scrypt, tokens hashed, 2FA secrets AES-GCM |
| Abuse of display links | read only, limited data, rotatable, rate limit |
| Misinterpretation of a warning | factual wording, no reference to persons |

## Privacy

- No cameras, no RFID, no identification of people. Measurements: space, state, vibration value, time, source.
- Account data: name, e-mail, role, language; sessions: IP and user agent (security purpose, max. 12 h).
- Data subject rights: data export (JSON, Art. 20 GDPR), delete account, delete the whole organisation (cascade).
- No tracking or advertising cookies; only one technically necessary session cookie.
- **To be settled by the operator before production use:** privacy policy and imprint
  (`/legal/*`), data processing agreement with customers, hosting location, record of processing
  activities, possibly a DPIA in a school context.

## Known limitations / open points

- No payment integration – plan changes are audited, billing is manual.
- Rate limiting in memory per process (several instances would need Redis or similar).
- SQLite for small installations; for many customers move to PostgreSQL with row-level security.
- No QR code for the 2FA setup (key + `otpauth://` link); WebAuthn/passkeys as an extension.
- Agent releases are protected by checksum but not yet cryptographically signed.
- No external penetration test has been carried out.
