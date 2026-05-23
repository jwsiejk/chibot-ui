# AskChappy MVP Login, Roles, and Admin Voice Studio

## Purpose
This document defines the lightweight MVP login model and admin-only Voice Studio concept for AskChappy V1. It is intentionally simple for local/demo use and product UX control.

## 1) MVP login model (local/demo only)
AskChappy V1 starts with a lightweight email-only login modal.

User flow:
1. User opens `/chappy`.
2. Login modal asks for email address only.
3. No password for MVP/demo.
4. Email is used only for local/demo role selection and personalization.

Important notes:
- This is not production authentication.
- Email-only login is for local/demo MVP only.
- This login approach can be replaced later if needed.

## 2) MVP roles

### `standard_user`
Can:
- Enter AskChappy.
- Start Open Q&A sessions.
- Use guided modes.
- View session recap.
- Hear the currently published Chappy voice.

Cannot:
- Access Voice Studio.
- Create/change/publish the Chappy voice profile.

### `admin`
Can:
- Access hidden/admin-only controls.
- Access Voice Studio.
- Upload/record Chappy voice samples.
- Generate/test voice profile when implementation exists.
- Approve/publish the active Chappy voice profile.
- Disable/revert to fallback voice.

Initial admin account:
- `jsiejk@ddn.com`

Role rule for MVP:
- `jsiejk@ddn.com` resolves to `admin`.
- All other emails resolve to `standard_user`.

Important framing:
- Admin-only controls are for UX governance and product consistency.
- They prevent normal users from changing the shared Chappy voice.
- This is not a heavy security-hardening model.

## 3) Planned admin routes
- `/admin` — admin dashboard (hidden from standard user navigation)
- `/admin/voice` — Voice Studio
- `/admin/avatar` — future avatar setup/review

Route visibility and access rules:
- Admin routes are hidden unless logged in as `admin`.
- Standard users should not see admin navigation.
- Accessing admin routes as `standard_user` shows a simple “not authorized” state.
- No voice cloning controls appear in normal user session route: `/chappy/session/:sessionId`.

## 4) Admin-only Voice Studio concept (planned workflow)
Voice Studio is planned workflow documentation, not MVP code in this docs PR.

Workflow:
1. Admin opens `/admin/voice`.
2. Admin records or uploads Chappy voice samples.
3. System creates a draft voice profile.
4. Admin test-generates sample Chappy speech.
5. Admin approves voice.
6. Admin publishes it as the global AskChappy voice.
7. All standard users hear the published Chappy voice in future sessions.

Boundary rule:
- Voice cloning does not happen inside normal Zoom-like user sessions.
- User sessions only consume the currently published voice profile.

## 5) MVP consent note (lightweight)
For MVP documentation:
- Chapman should agree to using his voice for AskChappy.
- The app does not need a heavy legal/security workflow for this MVP.
- Lightweight admin confirmation is enough for MVP docs.

Example confirmation text:
- “I confirm Chapman approved using this voice for AskChappy.”

Non-goal:
- Do not over-engineer consent storage or legal workflow in V1 MVP docs.

## 6) Voice profile lifecycle
Lifecycle states:

```text
draft
testing
approved
published
disabled
```

Lifecycle intent:
- `draft`: initial generated profile, not user-facing.
- `testing`: admin QA and sample generation.
- `approved`: accepted for release but not active globally.
- `published`: active global voice used by standard user sessions.
- `disabled`: removed from active use; fallback voice may be used.
